"""Le fonti dirette degli anime (AnimeUnity e AnimeWorld): puntate scaricate in HTTP, senza torrent.

Qui c'è il sito AnimeUnity e la logica comune; AnimeWorld è in animeworld.py.

Come funziona:
- su AnimeUnity cerco l'anime (POST /livesearch, serve il token CSRF della home);
- l'elenco delle puntate arriva da /info_api/{id}, a blocchi di 120;
- per ogni puntata /embed-url/{id} dà la pagina del lettore (vixcloud), che contiene
  `window.downloadUrl`: il file MP4 da scaricare. Il link scade, quindi lo chiedo
  ogni volta che il download parte o riparte (download.py → risolutori).

Una serie di Sonarr si "collega" a una o più voci dei siti (di solito una per stagione,
oppure una sola con la numerazione assoluta, come One Piece). Il collegamento ricorda da quale
puntata scaricare: Cerca scarica quelle già uscite e, ogni ora, le nuove che compaiono.
A download finito il file va a Sonarr (download.py → _passa_a_sonarr), che lo mette in Jellyfin.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import html
import json
import logging
import re
import time
import unicodedata

import httpx

from .puntate_italiane import titoli_ricordati
from .rete import ErroreServizio
from .serie import e_anime

log = logging.getLogger("cerca.animeunity")

# AnimeUnity è dietro Cloudflare: con un User-Agent da programma risponde "accesso negato"
AGENTE_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
BLOCCO_PUNTATE = 120  # /info_api non ne dà di più per volta
OGNI = 3600  # le puntate nuove si controllano una volta all'ora
MAX_PER_GIRO = 300  # puntate messe in coda al massimo in un giro (ne scarico poche alla volta)
DURATA_TOKEN = 20 * 60
CHIAVE = "animeunity:"
# in ordine di preferenza (a parità di lingua): se una non dà il file si prova l'altra
FONTI = ("animeunity", "animeworld")
NOMI_FONTI = {"animeunity": "AnimeUnity", "animeworld": "AnimeWorld"}

RE_CSRF = re.compile(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', re.I)
RE_DOWNLOAD = re.compile(r"""window\.downloadUrl\s*=\s*['"]([^'"]+)['"]""")
RE_EMBED = re.compile(r"""<iframe[^>]+src=['"]([^'"]+)['"]""", re.I)


# --- funzioni pure (testate) ---------------------------------------------------

def estrai_csrf(pagina: str) -> str:
    m = RE_CSRF.search(pagina or "")
    return html.unescape(m.group(1)) if m else ""


def estrai_download(pagina: str) -> str:
    """Il link MP4 dalla pagina del lettore di vixcloud ("" se non c'è)."""
    m = RE_DOWNLOAD.search(pagina or "")
    return html.unescape(m.group(1)).replace("\\/", "/") if m else ""


def numero_puntata(testo) -> int | None:
    """"12" → 12. Le puntate a metà ("12.5") e i doppi ("1-2") non si collegano a Sonarr."""
    testo = str(testo or "").strip()
    return int(testo) if testo.isdigit() else None


def normalizza(record: dict) -> dict:
    """Una voce di AnimeUnity con i nomi di Cerca."""
    titolo = record.get("title_it") or record.get("title") or record.get("title_eng") or ""
    altri = [t for t in (record.get("title"), record.get("title_eng")) if t and t != titolo]
    return {
        "fonte": "animeunity",
        "id": int(record.get("id") or 0),
        "slug": record.get("slug") or "",
        "titolo": titolo,
        "altri_titoli": altri,
        "anno": str(record.get("date") or "")[:4],
        "puntate": int(record.get("episodes_count") or 0),
        "tipo": record.get("type") or "",
        "doppiato": bool(int(record.get("dub") or 0)) or titolo.rstrip().endswith("(ITA)"),
        "immagine": record.get("imageurl") or "",
        "anilist": record.get("anilist_id"),
        "stato": record.get("status") or "",
    }


def _semplice(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    t = re.sub(r"\(ita\)", " ", t)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def ordina_risultati(voci: list[dict], titoli: list[str], italiano: bool) -> list[dict]:
    """Prima le voci col titolo più vicino, poi quelle nella lingua preferita, poi dalla più vecchia
    (la prima stagione prima della seconda)."""
    cercati = {_semplice(t) for t in titoli if t}

    def chiave(v: dict):
        nomi = {_semplice(n) for n in [v["titolo"], *v.get("altri_titoli", [])]}
        uguale = bool(nomi & cercati)
        inizia = any(n.startswith(c) for n in nomi for c in cercati if c)
        return (not uguale, not inizia, v["doppiato"] != italiano, v["anno"] or "9999", str(v["id"]))

    return sorted(voci, key=chiave)


def indovina_stagione(voce: dict, voci: list[dict], stagioni: dict[int, int], assoluta: bool) -> int:
    """La stagione di Sonarr di una voce di AnimeUnity (0 = numerazione assoluta).

    `stagioni` è {numero stagione: quante puntate}. Una voce con più puntate di qualsiasi
    stagione (One Piece) va con la numerazione assoluta; altrimenti conta la sua posizione
    tra le voci della stessa lingua, dalla più vecchia.
    """
    normali = sorted(n for n in stagioni if n > 0)
    if not normali:
        return 1
    if len(normali) == 1:
        return normali[0]
    if assoluta and voce["puntate"] > max(stagioni.values()):
        return 0
    simili = sorted((v for v in voci if v["doppiato"] == voce["doppiato"] and v.get("fonte") == voce.get("fonte")
                     and v["tipo"] in ("TV", "ONA", voce["tipo"])),
                    key=lambda v: (v["anno"] or "9999", len(v["titolo"]), str(v["id"])))
    posto = next((i for i, v in enumerate(simili) if v["id"] == voce["id"]), 0)
    return normali[min(posto, len(normali) - 1)]


def puntata_sonarr(episodi: list[dict], stagione: int, numero: int) -> dict | None:
    """La puntata di Sonarr per il numero di AnimeUnity: nella stagione, o con la numerazione assoluta."""
    for e in episodi:
        if stagione > 0 and e.get("seasonNumber") == stagione and e.get("episodeNumber") == numero:
            return e
        if stagione == 0 and e.get("seasonNumber", 0) > 0 and e.get("absoluteEpisodeNumber") == numero:
            return e
    return None


def numero_au(collegamento: dict, episodio: dict) -> int | None:
    """Il contrario: il numero su AnimeUnity di una puntata di Sonarr (None se non è di quella voce)."""
    if collegamento["stagione"] > 0:
        return episodio.get("episodeNumber") if episodio.get("seasonNumber") == collegamento["stagione"] else None
    return episodio.get("absoluteEpisodeNumber") if episodio.get("seasonNumber", 0) > 0 else None


def nome_file(titolo: str, episodio: dict, doppiato: bool = False) -> str:
    """"Frieren - S01E05 - ITA.mp4": Sonarr capisce stagione, puntata e (con ITA) la lingua dal nome."""
    pulito = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", titolo or "Anime")
    pulito = " ".join(pulito.split()).strip(" .") or "Anime"
    lingua = " - ITA" if doppiato else ""
    return f"{pulito} - S{episodio['seasonNumber']:02d}E{episodio['episodeNumber']:02d}{lingua}.mp4"


def candidati_forti(voci: list[dict], titoli: list[str]) -> list[dict]:
    """Le voci che sono davvero quell'anime: titolo uguale, o che inizia col titolo
    ("Frieren 2", "Frieren (ITA)"). I film e gli speciali a parte non si collegano da soli."""
    cercati = [c for c in {_semplice(t) for t in titoli if t} if len(c) >= 3]
    forti = []
    for v in voci:
        if v["tipo"].casefold() in ("movie", "film", "special"):
            continue
        nomi = {_semplice(n) for n in [v["titolo"], *v.get("altri_titoli", [])]}
        if any(n == c or n.startswith(c + " ") for n in nomi for c in cercati):
            forti.append(v)
    # ONA e OVA sono di solito spin-off ("Mini Anime"): solo se di quel sito non c'è la serie TV
    con_tv = {v.get("fonte") for v in forti if v["tipo"].upper() == "TV"}
    return [v for v in forti if v["tipo"].upper() not in ("ONA", "OVA") or v.get("fonte") not in con_tv]


def scegli_collegamenti(voci: list[dict], titoli: list[str], stagioni: dict[int, int], assoluta: bool,
                        italiano: bool) -> list[dict]:
    """I collegamenti automatici: per ogni sito e stagione la voce doppiata e quella sottotitolata migliori."""
    forti = ordina_risultati(candidati_forti(voci, titoli), titoli, italiano)
    scelti: dict[tuple[str, int, bool], dict] = {}
    for v in forti:
        stagione = indovina_stagione(v, forti, stagioni, assoluta)
        scelti.setdefault((v.get("fonte", "animeunity"), stagione, v["doppiato"]), {**v, "stagione": stagione})
    return list(scelti.values())


def lingua_file_italiana(file: dict | None) -> bool:
    return any((lingua.get("name") or "").casefold() == "italian" for lingua in (file or {}).get("languages") or [])


def fonte_di(collegamento: dict) -> str:
    return collegamento.get("fonte") or "animeunity"


def ordina_fonti(disponibili: list[tuple[dict, dict]], italiano: bool) -> list[tuple[dict, dict]]:
    """Prima la lingua preferita, poi i siti nell'ordine di FONTI."""
    def chiave(x):
        f = fonte_di(x[0])
        return (bool(x[0].get("doppiato")) != italiano, FONTI.index(f) if f in FONTI else 99)
    return sorted(disponibili, key=chiave)


def scegli_fonte(episodio: dict, disponibili: list[tuple[dict, dict]], italiano: bool) -> tuple[dict, dict] | None:
    """Tra le voci collegate che hanno la puntata: prima quella nella lingua preferita."""
    return ordina_fonti(disponibili, italiano)[0] if disponibili else None


def alternative(disponibili: list[tuple[dict, dict]], scelta: dict, italiano: bool) -> list[list]:
    """Le fonti della stessa lingua della scelta, nell'ordine in cui provarle: [[sito, id puntata], …]."""
    return [[fonte_di(c), p["id"]] for c, p in ordina_fonti(disponibili, italiano)
            if bool(c.get("doppiato")) == bool(scelta.get("doppiato"))]


def da_scaricare(episodi: list[dict], fonti: dict[int, list[tuple[dict, dict]]], *, file_italiani: set[int],
                 fatte: dict[str, str], in_corso: set[int], solo: set[int] | None, da: tuple[int, int] | None,
                 italiano: bool) -> tuple[list[tuple[dict, dict, dict]], list[dict]]:
    """Cosa scaricare da AnimeUnity e cosa resta a Sonarr.

    - con `solo` (scelta a mano): quelle puntate;
    - altrimenti le puntate da `da` in avanti non ancora scaricate da qui.
    In più, sempre: le puntate già presenti ma non in italiano, se AnimeUnity le ha doppiate
    (la versione italiana sostituisce il resto).
    Restituisce ([(puntata, collegamento, puntata sul sito)], [puntate che nessun sito ha]).
    """
    scegli, mancano = [], []
    for e in episodi:
        if e["id"] in in_corso or e.get("seasonNumber", 0) <= 0 and not (solo and e["id"] in solo):
            continue
        fonte = scegli_fonte(e, fonti.get(e["id"], []), italiano)
        gia = fatte.get(str(e["id"]))
        if e.get("hasFile"):
            ita_qui = fonte is not None and fonte[0].get("doppiato")
            if ita_qui and italiano and gia != "ita" and e["id"] not in file_italiani:
                scegli.append((e, *fonte))
            continue
        voluta = (e["id"] in solo) if solo is not None else (
            da is not None and (e.get("seasonNumber", 0), e.get("episodeNumber", 0)) >= da and gia is None)
        if not voluta:
            continue
        if fonte is None:
            mancano.append(e)
        else:
            scegli.append((e, *fonte))
    return scegli, mancano


# --- il sito -------------------------------------------------------------------

class AnimeUnity:
    def __init__(self, config) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._token = ""
        self._token_quando = 0.0
        self._lock: asyncio.Lock | None = None

    @property
    def attivo(self) -> bool:
        return bool(self.config.animeunity_url)

    @property
    def base(self) -> str:
        return self.config.animeunity_url

    def client(self) -> httpx.AsyncClient:
        # client tutto suo: i cookie della sessione di AnimeUnity e un User-Agent da browser
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30, connect=15), follow_redirects=True,
                headers={"User-Agent": AGENTE_BROWSER, "Accept-Language": "it-IT,it;q=0.9"},
            )
        return self._client

    async def chiudi(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _errore(self) -> ErroreServizio:
        return ErroreServizio("AnimeUnity non risponde.",
                              "Il sito cambia spesso indirizzo: se ora è un altro, mettilo in ANIMEUNITY_URL nel .env.")

    async def _get(self, percorso: str, **kw) -> httpx.Response:
        try:
            r = await self.client().get(f"{self.base}{percorso}", **kw)
        except httpx.HTTPError as exc:
            raise self._errore() from exc
        if r.status_code in (403, 503):
            raise ErroreServizio("AnimeUnity blocca la richiesta (protezione anti-bot).", "Riprova tra qualche minuto.")
        if not r.is_success:
            raise self._errore()
        return r

    async def _csrf(self, rinnova: bool = False) -> str:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if rinnova or not self._token or time.monotonic() - self._token_quando > DURATA_TOKEN:
                self._token = estrai_csrf((await self._get("/")).text)
                self._token_quando = time.monotonic()
        return self._token

    async def cerca(self, titolo: str) -> list[dict]:
        if not self.attivo:
            raise ErroreServizio("AnimeUnity non è attivo.", "Metti l'indirizzo del sito in ANIMEUNITY_URL nel .env.")
        for tentativo in range(2):
            token = await self._csrf(rinnova=tentativo > 0)
            try:
                r = await self.client().post(f"{self.base}/livesearch", data={"title": titolo}, headers={
                    "X-CSRF-TOKEN": token, "X-Requested-With": "XMLHttpRequest", "Referer": f"{self.base}/",
                    "Accept": "application/json"})
            except httpx.HTTPError as exc:
                raise self._errore() from exc
            if r.status_code == 419 and tentativo == 0:  # token scaduto
                continue
            if not r.is_success:
                raise self._errore()
            try:
                return [normalizza(x) for x in (r.json().get("records") or []) if x.get("id")]
            except ValueError as exc:
                raise self._errore() from exc
        return []

    async def puntate(self, anime_id: int) -> list[dict]:
        """Tutte le puntate: [{id, numero (testo), file}]."""
        try:
            quante = int((await self._get(f"/info_api/{anime_id}/")).json().get("episodes_count") or 0)
        except ValueError as exc:
            raise self._errore() from exc
        risultato: list[dict] = []
        for inizio in range(1, quante + 1, BLOCCO_PUNTATE):
            fine = min(inizio + BLOCCO_PUNTATE - 1, quante)
            try:
                dati = (await self._get(f"/info_api/{anime_id}/1",
                                        params={"start_range": inizio, "end_range": fine})).json()
            except ValueError as exc:
                raise self._errore() from exc
            risultato += [{"id": e["id"], "numero": str(e.get("number") or ""), "file": e.get("file_name") or ""}
                          for e in dati.get("episodes") or [] if e.get("id")]
        return risultato

    async def link_video(self, puntata_id: int) -> tuple[str, dict]:
        """Il link MP4 della puntata (scade dopo qualche ora) e le intestazioni per scaricarlo."""
        embed = (await self._get(f"/embed-url/{puntata_id}", headers={"Referer": f"{self.base}/"})).text.strip()
        if not embed.startswith("http"):
            m = RE_EMBED.search(embed)
            embed = html.unescape(m.group(1)) if m else ""
        if not embed.startswith("http"):
            raise ErroreServizio("AnimeUnity non dà il lettore di questa puntata.", "Riprova più tardi.")
        try:
            r = await self.client().get(embed, headers={"Referer": f"{self.base}/"})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ErroreServizio("Il lettore di AnimeUnity non risponde.", "Riprova più tardi.") from exc
        url = estrai_download(r.text)
        if not url:
            raise ErroreServizio("Questa puntata su AnimeUnity non si può scaricare (solo in streaming).",
                                 "Prova con un'altra voce di AnimeUnity, o lascia che la cerchi Sonarr.")
        return url, {"Referer": embed, "User-Agent": AGENTE_BROWSER}


# --- serie collegate -----------------------------------------------------------

def chiave_voce(voce: dict) -> str:
    return f"{fonte_di(voce)}:{voce['id']}"


class AnimeDiretti:
    """Le serie di Sonarr collegate ai siti diretti e il giro che scarica le puntate nuove.

    Per ogni serie (chiave "animeunity:{id}" nello stato, per i dati già salvati) ricordo:
    - collegamenti: le voci dei siti e a quale stagione vanno (0 = numerazione assoluta);
    - da: [stagione, puntata] da cui scaricare anche le nuove (None = solo quelle scelte a mano);
    - fatte: {id puntata Sonarr: "ita" | "sub"} quelle già prese da qui;
    - cercato: quando ho cercato l'anime da solo l'ultima volta.
    """

    RICERCA_OGNI = 24 * 3600
    RISERVA_GIORNI = 2  # dopo, una puntata che nessun sito ha la cerca Sonarr

    def __init__(self, siti: dict, archivio, serie, download, config) -> None:
        self.siti = siti  # {"animeunity": AnimeUnity, "animeworld": AnimeWorld}
        self.archivio = archivio
        self.serie = serie
        self.download = download
        self.config = config
        self._lock = asyncio.Lock()

    @property
    def italiano(self) -> bool:
        return (self.config.lingue_audio or ("it",))[0] == "it"

    @property
    def attivo(self) -> bool:
        return any(s.attivo for s in self.siti.values())

    def siti_attivi(self) -> list[str]:
        return [f for f in FONTI if f in self.siti and self.siti[f].attivo]

    # --- stato -----------------------------------------------------------------
    def leggi(self, serie_id: int) -> dict:
        try:
            stato = json.loads(self.archivio.stato_leggi(f"{CHIAVE}{serie_id}") or "{}")
        except ValueError:
            stato = {}
        if not isinstance(stato, dict):
            stato = {}
        stato.setdefault("collegamenti", [])
        stato.setdefault("da", None)
        stato.setdefault("fatte", {})
        stato.setdefault("cercato", 0)
        for c in stato["collegamenti"]:
            c.setdefault("fonte", "animeunity")
        return stato

    def _scrivi(self, serie_id: int, stato: dict) -> None:
        self.archivio.stato_scrivi(f"{CHIAVE}{serie_id}", json.dumps(stato))

    def collegamenti(self, serie_id: int) -> list[dict]:
        """Solo quelli dei siti accesi."""
        attivi = set(self.siti_attivi())
        return [c for c in self.leggi(serie_id)["collegamenti"] if fonte_di(c) in attivi]

    def serie_collegate(self) -> list[int]:
        return [int(k[len(CHIAVE):]) for k in self.archivio.stato_con_prefisso(CHIAVE) if k[len(CHIAVE):].isdigit()]

    def collega(self, serie_id: int, voce: dict, stagione: int) -> None:
        """Collegamento scelto a mano: prende il posto di quello (stesso sito e lingua) di quella stagione."""
        stato = self.leggi(serie_id)
        posto = (fonte_di(voce), stagione, bool(voce.get("doppiato")))
        stato["collegamenti"] = [c for c in stato["collegamenti"] if chiave_voce(c) != chiave_voce(voce)
                                 and (fonte_di(c), c["stagione"], c["doppiato"]) != posto]
        stato["collegamenti"].append(self._collegamento(voce, stagione, auto=False))
        stato["scollegati"] = [x for x in stato.get("scollegati") or [] if x != chiave_voce(voce)]
        self._scrivi(serie_id, stato)

    def scollega(self, serie_id: int, fonte: str, anime_id) -> None:
        stato = self.leggi(serie_id)
        chiave = f"{fonte}:{anime_id}"
        stato["collegamenti"] = [c for c in stato["collegamenti"] if chiave_voce(c) != chiave]
        stato.setdefault("scollegati", []).append(chiave)  # la ricerca automatica non lo ricollega
        self._scrivi(serie_id, stato)

    def segui_da(self, serie_id: int, da: tuple[int, int] | None) -> None:
        stato = self.leggi(serie_id)
        stato["da"] = list(da) if da else None
        self._scrivi(serie_id, stato)

    @staticmethod
    def _collegamento(voce: dict, stagione: int, auto: bool) -> dict:
        return {"fonte": fonte_di(voce), "id": voce["id"], "slug": voce.get("slug", ""),
                "titolo": voce.get("titolo", ""), "doppiato": bool(voce.get("doppiato")),
                "immagine": voce.get("immagine", ""), "anno": voce.get("anno", ""),
                "puntate": voce.get("puntate", 0), "stagione": stagione, "auto": auto}

    # --- ricerca -----------------------------------------------------------------
    async def cerca(self, titolo: str) -> list[dict]:
        """Su tutti i siti accesi insieme; un sito che non risponde non ferma gli altri."""
        fonti = self.siti_attivi()
        esiti = await asyncio.gather(*(self.siti[f].cerca(titolo) for f in fonti), return_exceptions=True)
        voci, errori = [], []
        for f, esito in zip(fonti, esiti, strict=True):
            if isinstance(esito, ErroreServizio):
                errori.append(esito)
                log.info("%s: ricerca di %s non riuscita (%s)", NOMI_FONTI.get(f, f), titolo, esito.cosa)
            elif isinstance(esito, BaseException):
                log.warning("%s: errore nella ricerca", NOMI_FONTI.get(f, f), exc_info=esito)
            else:
                voci += esito
        if not voci and errori:
            raise errori[0]
        return voci

    # --- collegamento automatico ----------------------------------------------
    async def collega_da_solo(self, serie_id: int, dati: dict, episodi: list[dict], forza: bool = False) -> list[dict]:
        """Cerca l'anime sui siti e lo collega da solo (una volta al giorno, o subito con `forza`)."""
        stato = self.leggi(serie_id)
        if not self.attivo or not (dati.get("seriesType") == "anime" or e_anime(dati)):
            return self.collegamenti(serie_id)
        if not forza and time.time() - stato["cercato"] < self.RICERCA_OGNI:
            return self.collegamenti(serie_id)
        titoli = list(dict.fromkeys(t for t in [*titoli_ricordati(self.archivio, serie_id), dati.get("title", ""),
                                                *(a.get("title", "") for a in dati.get("alternateTitles") or [])] if t))
        voci: dict[str, dict] = {}
        for t in titoli[:3]:
            try:
                for v in await self.cerca(t):
                    voci.setdefault(chiave_voce(v), v)
            except ErroreServizio:
                return self.collegamenti(serie_id)
        stagioni: dict[int, int] = {}
        for e in episodi:
            stagioni[e.get("seasonNumber", 0)] = stagioni.get(e.get("seasonNumber", 0), 0) + 1
        assoluta = any(e.get("absoluteEpisodeNumber") for e in episodi)
        scollegati = set(stato.get("scollegati") or [])
        a_mano = [c for c in stato["collegamenti"] if not c.get("auto")]
        occupati = {(fonte_di(c), c["stagione"], c["doppiato"]) for c in a_mano}
        automatici = [self._collegamento(v, v["stagione"], auto=True)
                      for v in scegli_collegamenti([v for k, v in voci.items() if k not in scollegati],
                                                   titoli, stagioni, assoluta, self.italiano)
                      if (fonte_di(v), v["stagione"], v["doppiato"]) not in occupati]
        stato.update(collegamenti=a_mano + automatici, cercato=time.time())
        self._scrivi(serie_id, stato)
        if automatici:
            log.info("Anime diretti: %s collegata a %s", dati.get("title"),
                     ", ".join(f"{c['titolo']} ({NOMI_FONTI.get(c['fonte'], c['fonte'])})" for c in automatici))
        return self.collegamenti(serie_id)

    # --- download --------------------------------------------------------------
    async def _fonti(self, collegamenti: list[dict], episodi: list[dict]):
        fonti: dict[int, list[tuple[dict, dict]]] = {}
        for c in collegamenti:
            try:
                puntate = await self.siti[fonte_di(c)].puntate(c["id"])
            except ErroreServizio:
                log.info("%s: elenco puntate di %s non disponibile", NOMI_FONTI.get(fonte_di(c)), c["titolo"])
                continue
            for p in puntate:
                n = numero_puntata(p["numero"])
                e = puntata_sonarr(episodi, c["stagione"], n) if n is not None else None
                if e is not None:
                    fonti.setdefault(e["id"], []).append((c, p))
        return fonti

    async def scarica(self, serie_id: int, *, solo: list[int] | None = None) -> dict:
        """Prende dai siti diretti quello che può (vedi da_scaricare). Restituisce
        {"avviate": [id puntate], "mancano": [id puntate che nessun sito ha]}."""
        async with self._lock:
            dati = await self.serie.dati(serie_id)
            episodi = await self.serie.episodi(serie_id)
            collegamenti = await self.collega_da_solo(serie_id, dati, episodi, forza=solo is not None
                                                      and not self.collegamenti(serie_id))
            if not collegamenti:
                return {"avviate": [], "mancano": list(solo or [])}
            stato = self.leggi(serie_id)
            fonti = await self._fonti(collegamenti, episodi)
            file_italiani: set[int] = set()
            if any(e.get("hasFile") for e in episodi) and self.italiano:
                per_file = {f["id"]: f for f in await self.serie.file_puntate(serie_id)}
                file_italiani = {e["id"] for e in episodi if lingua_file_italiana(per_file.get(e.get("episodeFileId")))}
            scelte, mancano = da_scaricare(
                episodi, fonti, file_italiani=file_italiani, fatte=stato["fatte"],
                in_corso=self.archivio.puntate_in_download(serie_id), solo=set(solo) if solo is not None else None,
                da=tuple(stato["da"]) if stato["da"] else None, italiano=self.italiano)
            nome = (titoli_ricordati(self.archivio, serie_id)[:1] or [dati.get("title") or "Anime"])[0]
            poster = next((i.get("remoteUrl") for i in dati.get("images") or [] if i.get("coverType") == "poster"), "")
            avviate = []
            for e, c, _p in scelte[:MAX_PER_GIRO]:
                doppiato = bool(c.get("doppiato"))
                await self.download.da_diretto(
                    serie_id=serie_id, titolo_serie=nome, poster=poster or "", episodio=e,
                    fonti=alternative(fonti[e["id"]], c, self.italiano),
                    nome_file=nome_file(dati.get("title") or nome, e, doppiato), doppiato=doppiato)
                stato["fatte"][str(e["id"])] = "ita" if doppiato else "sub"
                avviate.append(e["id"])
            self._scrivi(serie_id, stato)
            if avviate:
                # i preferiti: Sonarr non le cerca più come torrent (e ferma quelli già partiti)
                await self.serie.annulla_puntate(avviate)
                log.info("Anime diretti: %s puntate in coda per %s", len(avviate), dati.get("title"))
            if solo is None and mancano:
                # riserva: quelle uscite da un po' che nessun sito ha le cerca Sonarr
                limite = time.time() - self.RISERVA_GIORNI * 86400
                vecchie = [e["id"] for e in mancano if not e.get("monitored") and e.get("airDateUtc")
                           and _quando(e["airDateUtc"]) < limite]
                if vecchie:
                    await self.serie.scarica_puntate(vecchie)
            return {"avviate": avviate, "mancano": [e["id"] for e in mancano]}

    async def link(self, fonti: list[list]) -> tuple[str, dict]:
        """Il file della puntata dal primo sito che lo dà (gli altri sono la riserva)."""
        ultimo = ErroreServizio("Nessun sito dà il file di questa puntata.", "Riprova più tardi.")
        for fonte, puntata_id in fonti:
            sito = self.siti.get(fonte)
            if sito is None or not sito.attivo:
                continue
            try:
                return await sito.link_video(puntata_id)
            except ErroreServizio as exc:
                log.info("%s: puntata %s non disponibile (%s)", NOMI_FONTI.get(fonte, fonte), puntata_id, exc.cosa)
                ultimo = exc
        raise ultimo

    async def giro(self) -> int:
        if not self.attivo or not self.serie.attivo:
            return 0
        totale = 0
        for serie_id in self.serie_collegate():
            try:
                totale += len((await self.scarica(serie_id))["avviate"])
            except ErroreServizio as exc:
                log.info("Anime diretti, serie %s: %s", serie_id, exc.cosa)
            except Exception:  # noqa: BLE001 - una serie che non va non deve fermare le altre
                log.exception("Anime diretti: errore con la serie %s", serie_id)
        return totale

    async def ciclo(self) -> None:
        while True:
            try:
                await self.giro()
            except Exception:  # noqa: BLE001
                log.warning("Controllo delle puntate degli anime diretti non riuscito", exc_info=True)
            await asyncio.sleep(OGNI)


def _quando(testo: str) -> float:
    try:
        return dt.datetime.fromisoformat(testo.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


def con_download_cerca(stagioni: list[dict], per_puntata: dict[int, dict]) -> list[dict]:
    """Alle puntate di Sonarr aggiunge i download di Cerca (AnimeUnity): "In download 40%", "In coda"…"""
    for st in stagioni:
        for p in st["puntate"]:
            d = per_puntata.get(p["id"])
            if not d or p["stato"] == "pronta" and d.get("stato") == "errore":
                continue
            if d.get("stato") == "errore":
                p.update(stato="problema", messaggio=d.get("messaggio") or "")
            elif d.get("stato") == "in_corso":
                totale = d.get("totale") or 0
                p.update(stato="download", percentuale=round(100 * (d.get("scaricati") or 0) / totale, 1) if totale else 0)
            else:
                p.update(stato="download", percentuale=100)
            p.update(seguita=True, fonte=NOMI_FONTI.get(d.get("fonte") or "", "Link diretto"), download=d["id"])
    return stagioni
