"""I download che fanno da soli Radarr e Sonarr, con la lingua in evidenza.

- coda(): cosa stanno scaricando adesso, con la lingua dell'audio;
- in_attesa(): film e puntate già scaricati ma non nella lingua preferita
  (verranno sostituiti appena esce la versione giusta);
- ricerca_notturna(): ogni notte fa ricercare a Radarr e Sonarr le versioni
  migliori e quello che non avevano trovato, per non affidarsi solo alle uscite nuove.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import time

from .config import Config
from .ordine_download import posizioni
from .radarr import Radarr
from .serie import Serie

log = logging.getLogger("cerca.automatici")

NOMI = {"it": "Italiano", "es": "Spagnolo", "fr": "Francese", "de": "Tedesco", "pt": "Portoghese", "en": "Inglese"}
DA_INGLESE = {"Italian": "Italiano", "Spanish": "Spagnolo", "French": "Francese", "German": "Tedesco",
              "Portuguese": "Portoghese", "English": "Inglese"}
PREFISSO = "Lingua · "
# Le tracce audio vere del file, come le legge Radarr/Sonarr (mediaInfo.audioLanguages = "eng/ita")
DA_ISO = {"ita": "Italiano", "spa": "Spagnolo", "fre": "Francese", "fra": "Francese", "ger": "Tedesco",
          "deu": "Tedesco", "por": "Portoghese", "eng": "Inglese"}
OGNI_CONTROLLO_TRACCE = 1800
# Un torrent che non scarica niente da tante ore di solito non finirà mai (nessuna fonte):
# si toglie, va nella lista nera e Radarr/Sonarr cercano subito un'altra versione.
ORE_FERMO = 6
STATI_FERMI = {"stalledDL", "metaDL", "forcedMetaDL"}
ORA_RICERCA = 4  # di notte, quando nessuno guarda
CACHE_ATTESA = 600


def fermi(prima: dict[str, tuple[int, float]], torrent: list[dict], adesso: float,
          ore: float = ORE_FERMO) -> tuple[dict[str, tuple[int, float]], set[str]]:
    """(da quando ogni torrent è fermo, quelli fermi da almeno `ore`).

    Fermo = qBittorrent lo dà senza scambio di dati e i byte scaricati non sono cambiati
    dall'ultimo controllo. Appena riparte, il conto ricomincia."""
    ora: dict[str, tuple[int, float]] = {}
    for t in torrent:
        h = (t.get("hash") or "").lower()
        if t.get("state") not in STATI_FERMI or (t.get("progress") or 0) >= 1 or not h:
            continue
        scaricati = int(t.get("completed") or 0)
        precedente = prima.get(h)
        ora[h] = (scaricati, precedente[1] if precedente and precedente[0] == scaricati else adesso)
    return ora, {h for h, (_, dal) in ora.items() if adesso - dal >= ore * 3600}


def tracce_audio(media: dict | None) -> list[str]:
    """Le lingue delle tracce audio del file ("eng/ita" → ["Inglese", "Italiano"])."""
    codici = re.split(r"[/,| ]+", ((media or {}).get("audioLanguages") or "").lower())
    return list(dict.fromkeys(DA_ISO[c] for c in codici if c in DA_ISO))


def lingua_da(formati: list[dict] | None, lingue: list[dict] | None = None,
              ordine: tuple[str, ...] = ("it", "es", "en"), media: dict | None = None) -> str:
    """La lingua dell'audio: dalle tracce vere del file se ci sono, se no dai formati di mediabox
    ("Lingua · Spagnolo"), se no da quella letta da Radarr.
    Con più lingue (es. ITA.SPA) conta quella più in alto nelle preferenze."""
    trovate = tracce_audio(media) or [
        f.get("name", "").removeprefix(PREFISSO) for f in formati or []
        if f.get("name", "").startswith(PREFISSO) and not f.get("name", "").startswith(PREFISSO + "MULTI")]
    if not trovate:
        trovate = [DA_INGLESE[x["name"]] for x in lingue or [] if x.get("name") in DA_INGLESE]
    if not trovate:
        return ""
    posto = {NOMI[c]: i for i, c in enumerate(ordine) if c in NOMI}
    return min(trovate, key=lambda n: posto.get(n, len(posto)))


def titolo_film(film: dict, riserva: str = "") -> str:
    return f"{film.get('title') or riserva} ({film.get('year') or ''})".replace(" ()", "")


def stato_coda(r: dict) -> tuple[str, str]:
    """Lo stato di un download di Radarr/Sonarr detto in parole semplici."""
    if r.get("trackedDownloadStatus") in ("warning", "error") or r.get("status") in ("warning", "failed"):
        messaggi = [m for s in r.get("statusMessages") or [] for m in s.get("messages") or []]
        return "problema", "Problema: " + (messaggi[0] if messaggi else r.get("errorMessage") or "controlla qBittorrent")
    fase = r.get("trackedDownloadState") or ""
    if fase in ("importPending", "importing"):
        return "importazione", "Scaricato: lo sto mettendo in Jellyfin"
    if fase == "importBlocked":
        return "problema", "Scaricato, ma non riesco a metterlo in Jellyfin da solo"
    stato = r.get("status") or ""
    if stato == "delay":
        return "coda", "Trovato: parte tra poco"
    if stato in ("queued", "paused"):
        return "coda", "In coda: parte appena finiscono gli altri"
    if stato == "completed":
        return "importazione", "Scaricato: lo sto mettendo in Jellyfin"
    return "download", "In download"


def secondi(testo: str | None) -> int:
    """Il tempo rimanente di Radarr/Sonarr ("01:02:03" o "1.02:03:04") in secondi."""
    if not testo:
        return 0
    giorni, ore = "0", testo
    if "." in testo.split(":")[0]:
        giorni, ore = testo.split(".", 1)
    try:
        h, m, s = (int(float(x)) for x in ore.split(":"))
        return int(giorni) * 86400 + h * 3600 + m * 60 + s
    except ValueError:
        return 0


def fonte(r: dict) -> str:
    client = (r.get("downloadClient") or "").lower()
    return "Telegram" if "tgarr" in client or "telegram" in client or r.get("protocol") == "usenet" else "Torrent"


def copertina(oggetto: dict | None) -> str:
    """La locandina di un film o di una serie di Radarr/Sonarr, nella misura piccola."""
    for immagine in (oggetto or {}).get("images") or []:
        if immagine.get("coverType") == "poster":
            url = immagine.get("remoteUrl") or ""
            return url.replace("/t/p/original/", "/t/p/w342/")
    return ""


def gruppo_film(film: dict, id_film: int | None) -> dict:
    return {"gruppo": f"film:{id_film or film.get('id')}", "gruppo_titolo": titolo_film(film),
            "poster": copertina(film), "voce": "Film"}


def gruppo_serie(serie: dict | None, id_serie: int | None, voce: str) -> dict:
    serie = serie or {}
    return {"gruppo": f"serie:{id_serie or serie.get('id')}", "gruppo_titolo": serie.get("title") or "Serie",
            "poster": copertina(serie), "voce": voce}


def per_stagione(coppie) -> dict[str, list[int]]:
    """Le puntate di una voce divise per stagione, per "Annulla stagione" nella pagina Download."""
    stagioni: dict[str, list[int]] = {}
    for stagione, id_puntata in coppie:
        if id_puntata is not None:
            stagioni.setdefault(str(stagione or 0), []).append(id_puntata)
    return stagioni


def percentuale(totale: float, mancante: float) -> float:
    return round(100 * (1 - mancante / totale), 1) if totale else 0.0


def episodio(e: dict | None) -> str:
    e = e or {}
    return f"St. {e.get('seasonNumber', 0)} · Ep. {e.get('episodeNumber', 0)}"


def episodi(elenco: list[dict]) -> str:
    """Più puntate dello stesso torrent: "St. 1 · Ep. 1–24", "St. 1–2 · 30 puntate"."""
    elenco = [e or {} for e in elenco]
    if len(elenco) == 1:
        return episodio(elenco[0])
    stagioni = sorted({e.get("seasonNumber", 0) for e in elenco})
    numeri = sorted(e.get("episodeNumber", 0) for e in elenco)
    if len(stagioni) == 1:
        consecutive = numeri == list(range(numeri[0], numeri[0] + len(numeri)))
        if consecutive:
            return f"St. {stagioni[0]} · Ep. {numeri[0]}–{numeri[-1]}"
        return f"St. {stagioni[0]} · {len(numeri)} puntate"
    return f"St. {stagioni[0]}–{stagioni[-1]} · {len(numeri)} puntate"


async def tutte_le_pagine(chiama, percorso: str, params: dict, per_pagina: int = 250) -> list[dict]:
    """Tutti i record di un elenco a pagine di Radarr/Sonarr (coda, mancanti...).

    Sonarr mette nella coda un record per ogni puntata: un pacchetto di una stagione intera
    da solo riempie una pagina, e quello che resta fuori sembrerebbe "da trovare"."""
    record: list[dict] = []
    pagina = 1
    while True:
        dati = await chiama("GET", percorso, params={**params, "page": pagina, "pageSize": per_pagina}) or {}
        nuovi = dati.get("records") or []
        record.extend(nuovi)
        totale = dati.get("totalRecords") or 0
        if not nuovi or len(record) >= totale or pagina >= 100:
            return record
        pagina += 1


class Automatici:
    def __init__(self, config: Config, radarr: Radarr, serie: Serie, qbit=None) -> None:
        self.config = config
        self.qbit = qbit
        self._campioni: dict[str, tuple[float, int]] = {}
        self._velocita_qbit: dict[str, int] = {}
        self._posti_qbit: dict[str, int] = {}
        self._fermi: dict[str, tuple[int, float]] = {}
        self.radarr = radarr
        self.serie = serie
        self._attesa: tuple[float, list[dict]] | None = None
        self.avvisi = None  # AvvisiItaliano: avvisa dei MULTI che hanno l'audio nella lingua preferita

    @property
    def preferita(self) -> str:
        return NOMI.get(self.config.lingue_audio[0], "Italiano")

    def _lingua(self, dati: dict) -> str:
        return lingua_da(dati.get("customFormats"), dati.get("languages"), self.config.lingue_audio,
                         dati.get("mediaInfo"))

    def _voce(self, titolo: str, dettaglio: str, lingua: str, **altro) -> dict:
        return {
            "titolo": titolo, "dettaglio": dettaglio, "lingua": lingua or "lingua sconosciuta",
            "preferita": lingua == self.preferita, "lingua_preferita": self.preferita, **altro,
        }

    def _velocita(self, r: dict) -> int:
        """Byte al secondo: da qBittorrent se è un torrent, se no da quanto è sceso o dal tempo rimanente."""
        hash_torrent = (r.get("downloadId") or "").lower()
        if hash_torrent in self._velocita_qbit:
            return self._velocita_qbit[hash_torrent]
        mancante = int(r.get("sizeleft") or 0)
        chiave = f"{r.get('downloadId')}|{r.get('id')}"
        adesso = time.monotonic()
        prima = self._campioni.get(chiave)
        self._campioni[chiave] = (adesso, mancante)
        if prima and adesso - prima[0] > 1 and prima[1] > mancante:
            return int((prima[1] - mancante) / (adesso - prima[0]))
        rimanente = secondi(r.get("timeleft"))
        return int(mancante / rimanente) if rimanente and mancante else 0

    def _da_coda(self, r: dict, titolo: str, tipo: str, ids: list[int]) -> dict:
        stato, testo = stato_coda(r)
        velocita = self._velocita(r) if stato == "download" else 0
        mancante = int(r.get("sizeleft") or 0)
        rimanente = secondi(r.get("timeleft")) or (int(mancante / velocita) if velocita else 0)
        return self._voce(titolo, r.get("title", ""), self._lingua(r), tipo=tipo, ids=ids, stato=stato,
                          stato_testo=testo, percentuale=percentuale(r.get("size", 0), r.get("sizeleft", 0)),
                          dimensione=r.get("size", 0), velocita=velocita, rimanente=rimanente, fonte=fonte(r),
                          annullabile=True, ricercabile=False, **self._posto(r, stato))

    def _posto(self, r: dict, stato: str) -> dict:
        """L'hash del torrent e il suo posto nella coda di qBittorrent (per "Scarica prima questa")."""
        hash_torrent = (r.get("downloadId") or "").lower()
        if (r.get("protocol") or "torrent") != "torrent" or not hash_torrent:
            return {}
        posto = self._posti_qbit.get(hash_torrent, 0)
        return {"hash": hash_torrent, "posto": posto, "anticipabile": stato == "coda" and posto > 1}

    async def _leggi_qbit(self) -> None:
        if self.qbit is None:
            return
        try:
            torrent = await self.qbit.tutti()
            self._velocita_qbit = {t.get("hash", "").lower(): int(t.get("dlspeed") or 0) for t in torrent}
            self._posti_qbit = {h.lower(): p for h, p in posizioni(torrent).items()}
        except Exception:  # noqa: BLE001 - senza qBittorrent la velocità si stima
            self._velocita_qbit = {}
            self._posti_qbit = {}

    async def coda(self) -> list[dict]:
        """Tutto quello che Radarr e Sonarr stanno scaricando o cercando, con lo stato in parole semplici."""
        voci: list[dict] = []
        await self._leggi_qbit()
        in_coda_film: set[int] = set()
        in_coda_puntate: set[int] = set()
        try:
            for r in await tutte_le_pagine(self.radarr._r, "queue", {"includeMovie": "true"}):
                film = r.get("movie") or {}
                in_coda_film.add(r.get("movieId"))
                voci.append({**self._da_coda(r, titolo_film(film, r.get("title", "")), "film", [r.get("movieId")]),
                             **gruppo_film(film, r.get("movieId"))})
            for m in await tutte_le_pagine(self.radarr._r, "wanted/missing", {"monitored": "true"}):
                if m.get("id") in in_coda_film:
                    continue
                uscito = m.get("isAvailable", True)
                voci.append(self._voce(
                    titolo_film(m), "", "", tipo="film", ids=[m["id"]], stato="cerco" if uscito else "non_uscito",
                    stato_testo=("Cerco una fonte: riprovo da solo appena esce una versione e ogni notte" if uscito
                                 else "Non è ancora uscito: lo scarico appena è disponibile"),
                    percentuale=0, dimensione=0, annullabile=True, ricercabile=uscito, senza_lingua=True,
                    **gruppo_film(m, m.get("id"))))
        except Exception:  # noqa: BLE001 - se Radarr non risponde la pagina mostra il resto
            log.warning("Coda di Radarr non disponibile", exc_info=True)
        if self.serie.attivo:
            try:
                coda = await tutte_le_pagine(self.serie._api, "queue", {
                    "includeSeries": "true", "includeEpisode": "true"})
                # un torrent con più puntate (stagione intera) = una voce sola, con tutte le puntate
                per_torrent: dict[str, list[dict]] = {}
                for r in coda:
                    in_coda_puntate.add(r.get("episodeId"))
                    chiave = (r.get("downloadId") or "").lower() or f"voce:{r.get('id')}"
                    per_torrent.setdefault(chiave, []).append(r)
                for gruppo in per_torrent.values():
                    gruppo.sort(key=lambda r: ((r.get("episode") or {}).get("seasonNumber", 0),
                                               (r.get("episode") or {}).get("episodeNumber", 0)))
                    # se una delle puntate ha un problema, si vede quello
                    r = next((x for x in gruppo if stato_coda(x)[0] == "problema"), gruppo[0])
                    quali = episodi([x.get("episode") for x in gruppo])
                    titolo = f"{(r.get('series') or {}).get('title', '')} · {quali}"
                    ids = [x.get("episodeId") for x in gruppo]
                    voci.append({**self._da_coda(r, titolo, "serie", ids),
                                 **gruppo_serie(r.get("series"), r.get("seriesId"), quali),
                                 "per_stagione": per_stagione(
                                     ((x.get("episode") or {}).get("seasonNumber", 0), x.get("episodeId")) for x in gruppo)})
                mancanti = await tutte_le_pagine(self.serie._api, "wanted/missing", {
                    "monitored": "true", "includeSeries": "true"})
                per_serie: dict[int, tuple[dict, list[int]]] = {}
                stagione_di: dict[int, int] = {}
                for e in mancanti:
                    if e.get("id") not in in_coda_puntate:
                        serie = e.get("series") or {}
                        per_serie.setdefault(e.get("seriesId") or serie.get("id") or 0, (serie, []))[1].append(e["id"])
                        stagione_di[e["id"]] = e.get("seasonNumber", 0)
                for id_serie, (serie, ids) in per_serie.items():
                    quante = "1 puntata" if len(ids) == 1 else f"{len(ids)} puntate"
                    nome = serie.get("title") or "Serie"
                    voci.append(self._voce(
                        f"{nome} · {quante} da trovare", "", "", tipo="serie", ids=ids, stato="cerco",
                        stato_testo="Cerco una fonte: riprovo da solo appena escono e ogni notte",
                        percentuale=0, dimensione=0, annullabile=True, ricercabile=True, senza_lingua=True,
                        per_stagione=per_stagione((stagione_di.get(i, 0), i) for i in ids),
                        **gruppo_serie(serie, id_serie, f"{quante} da trovare")))
            except Exception:  # noqa: BLE001
                log.warning("Coda di Sonarr non disponibile", exc_info=True)
        return voci

    async def annulla(self, tipo: str, ids: list[int]) -> str:
        if tipo == "serie":
            await self.serie.annulla_puntate(ids)
            messaggio = "Annullato: quelle puntate non vengono più cercate né scaricate."
        else:
            messaggio = ""
            for i in ids:
                messaggio = await self.radarr.annulla(i)
        self._attesa = None
        return messaggio

    async def cerca_di_nuovo(self, tipo: str, ids: list[int]) -> str:
        if tipo == "serie":
            await self.serie.scarica_puntate(ids)
        else:
            for i in ids:
                await self.radarr.cerca_di_nuovo(i)
        return "Ricerca avviata: se trovo una versione parte da sola."

    async def stato_film(self, tmdb_id: int) -> dict:
        """Lo stato del film per la sua scheda: niente, cerco, in download, pronto."""
        film = await self.radarr.film(tmdb_id)
        if not film or not film.get("id"):
            return {"stato": "nessuno"}
        coda = await self.radarr._r("GET", "queue/details", params={"movieId": film["id"]}) or []
        if coda:
            await self._leggi_qbit()
            voce = self._da_coda(coda[0], titolo_film(film), "film", [film["id"]])
            return {**voce, "id": film["id"]}
        if film.get("hasFile"):
            lingua = self._lingua(film.get("movieFile") or {})
            return {**self._voce(titolo_film(film), "", lingua, stato="pronto",
                                 stato_testo="Pronto in Jellyfin"), "id": film["id"],
                    "migliorabile": bool(film.get("monitored")) and lingua != self.preferita}
        if film.get("monitored"):
            uscito = film.get("isAvailable", True)
            return {"stato": "cerco" if uscito else "non_uscito", "id": film["id"],
                    "stato_testo": ("Cerco una fonte: riprovo da solo appena esce una versione e ogni notte" if uscito
                                    else "Non è ancora uscito: lo scarico appena è disponibile")}
        return {"stato": "nessuno", "id": film["id"]}

    async def in_attesa(self) -> list[dict]:
        """Già scaricati ma non nella lingua preferita (Radarr/Sonarr li sostituiscono da soli)."""
        if self._attesa is not None and time.monotonic() - self._attesa[0] < CACHE_ATTESA:
            return self._attesa[1]
        voci: list[dict] = []
        try:
            for m in await tutte_le_pagine(self.radarr._r, "wanted/cutoff", {}):
                file = m.get("movieFile") or {}
                lingua = self._lingua(file)
                titolo = f"{m.get('title', '')} ({m.get('year', '')})".replace(" ()", "")
                voci.append(self._voce(titolo, file.get("relativePath", ""), lingua, tipo="film",
                                       **gruppo_film(m, m.get("id"))))
        except Exception:  # noqa: BLE001
            log.warning("Elenco da migliorare di Radarr non disponibile", exc_info=True)
        if self.serie.attivo:
            try:
                for e in await tutte_le_pagine(self.serie._api, "wanted/cutoff", {
                        "includeSeries": "true", "includeEpisodeFile": "true"}):
                    file = e.get("episodeFile") or {}
                    titolo = f"{(e.get('series') or {}).get('title', '')} · {episodio(e)}"
                    voci.append(self._voce(titolo, file.get("relativePath", ""), self._lingua(file), tipo="serie",
                                           **gruppo_serie(e.get("series"), e.get("seriesId"), episodio(e))))
            except Exception:  # noqa: BLE001
                log.warning("Elenco da migliorare di Sonarr non disponibile", exc_info=True)
        voci = [v for v in voci if not v["preferita"]]
        self._attesa = (time.monotonic(), voci)
        return voci

    async def cerca_tutto(self) -> None:
        await self.radarr._r("POST", "command", json={"name": "CutoffUnmetMoviesSearch"})
        await self.radarr._r("POST", "command", json={"name": "MissingMoviesSearch"})
        if self.serie.attivo:
            await self.serie._api("POST", "command", json={"name": "CutoffUnmetEpisodeSearch"})
            await self.serie._api("POST", "command", json={"name": "MissingEpisodeSearch"})
        self._attesa = None

    def _gia_buono(self, file: dict) -> bool:
        """Il nome non dichiara la prima lingua (es. "MULTi"), ma le tracce audio del file sì."""
        prima = self.preferita
        dichiarata = any(f.get("name") == PREFISSO + prima for f in file.get("customFormats") or [])
        return not dichiarata and prima in tracce_audio(file.get("mediaInfo"))

    async def controlla_tracce(self) -> int:
        """Ferma la ricerca di una versione migliore per i file che hanno già l'audio nella prima lingua.

        Radarr e Sonarr giudicano dal nome: un "MULTi" con dentro l'italiano resterebbe "da
        migliorare" per sempre e verrebbe sostituito senza motivo. Restano scaricati e in
        Jellyfin; non si cercano più (film o puntata non più "monitorati").
        """
        film: list[dict] = []
        puntate: list[dict] = []
        try:
            dati = await tutte_le_pagine(self.radarr._r, "wanted/cutoff", {})
            film = [m for m in dati if self._gia_buono(m.get("movieFile") or {})]
            if film:
                await self.radarr._r("PUT", "movie/editor", json={"movieIds": [m["id"] for m in film], "monitored": False})
        except Exception:  # noqa: BLE001
            film = []
            log.warning("Controllo delle tracce audio in Radarr non riuscito", exc_info=True)
        if self.serie.attivo:
            try:
                dati = await tutte_le_pagine(self.serie._api, "wanted/cutoff", {
                    "includeEpisodeFile": "true", "includeSeries": "true"})
                puntate = [e for e in dati if self._gia_buono(e.get("episodeFile") or {})]
                if puntate:
                    await self.serie._api("PUT", "episode/monitor", json={
                        "episodeIds": [e["id"] for e in puntate], "monitored": False})
            except Exception:  # noqa: BLE001
                puntate = []
                log.warning("Controllo delle tracce audio in Sonarr non riuscito", exc_info=True)
        fermati = len(film) + len(puntate)
        if fermati:
            log.info("%s file hanno già l'audio in %s: non cerco altre versioni", fermati, self.preferita.lower())
            self._attesa = None
            if self.avvisi is not None:
                try:
                    await self.avvisi.multi_confermati(film, puntate)
                except Exception:  # noqa: BLE001 - l'avviso non deve fermare il controllo
                    log.warning("Avviso dei MULTI in %s non riuscito", self.preferita.lower(), exc_info=True)
        return fermati

    async def cambia_fermi(self) -> list[str]:
        """Toglie i download fermi da ORE_FERMO ore e fa cercare a Radarr/Sonarr un'altra versione.

        La versione tolta va nella lista nera, così non viene ripresa; ne viene scelta un'altra
        con le stesse regole (lingua prima di tutto) tra quelle con abbastanza fonti."""
        if self.qbit is None:
            return []
        try:
            torrent = await self.qbit.tutti()
        except Exception:  # noqa: BLE001
            return []
        self._fermi, bloccati = fermi(self._fermi, torrent, time.time())
        if not bloccati:
            return []
        cambiati = []
        servizi = [("Radarr", self.radarr._r)]
        if self.serie.attivo:
            servizi.append(("Sonarr", self.serie._api))
        for nome, chiama in servizi:
            try:
                for r in await tutte_le_pagine(chiama, "queue", {}):
                    if (r.get("downloadId") or "").lower() in bloccati:
                        await chiama("DELETE", f"queue/{r['id']}", params={
                            "removeFromClient": "true", "blocklist": "true", "skipRedownload": "false"})
                        cambiati.append(r.get("title", ""))
                        self._fermi.pop((r.get("downloadId") or "").lower(), None)
            except Exception:  # noqa: BLE001
                log.warning("Non riesco a cambiare i download fermi in %s", nome, exc_info=True)
        if cambiati:
            log.info("Download fermi da %s ore, cerco un'altra versione: %s", ORE_FERMO, ", ".join(cambiati))
        return cambiati

    async def ciclo_tracce(self) -> None:
        """Ogni 30 minuti: tracce audio vere e download fermi."""
        while True:
            try:
                await self.controlla_tracce()
                await self.cambia_fermi()
            except Exception:  # noqa: BLE001 - un errore imprevisto non deve fermare il controllo per sempre
                log.exception("Errore nel controllo di tracce audio e download fermi")
            await asyncio.sleep(OGNI_CONTROLLO_TRACCE)

    async def ricerca_notturna(self) -> None:
        while True:
            adesso = dt.datetime.now()
            prossima = adesso.replace(hour=ORA_RICERCA, minute=0, second=0, microsecond=0)
            if prossima <= adesso:
                prossima += dt.timedelta(days=1)
            await asyncio.sleep((prossima - adesso).total_seconds())
            try:
                await self.cerca_tutto()
                log.info("Ricerca notturna avviata: versioni migliori e quelli mancanti")
            except Exception:  # noqa: BLE001 - riproviamo la notte dopo
                log.exception("Ricerca notturna non riuscita")
