"""Catalogo senza chiavi: lo usa Cerca quando nelle impostazioni non c'è una chiave TMDB.

Fonti, tutte gratuite e senza registrazione:
- Radarr (già installato): ricerca e schede dei film, in italiano, con locandine;
  film di tendenza e popolari dalla sua pagina "Scopri";
- Wikidata: i film più noti di un genere e i film originali delle piattaforme;
- TVmaze: ricerca delle serie, serie in onda adesso e sulle piattaforme.

Gli id dei film restano quelli di TMDB (Radarr li usa), così download, Jellyfin
e Radarr funzionano come con la chiave. Senza chiave manca solo "dove vederlo
in Italia" (dati JustWatch): al suo posto c'è un link alla ricerca di JustWatch.
"""

from __future__ import annotations

import asyncio
import contextvars
import datetime as dt
import html
import json
import logging
import re
import time
from urllib.parse import quote

import httpx

from . import justwatch
from .config import Config
from .db import Archivio
from .rete import AGENTE, ErroreServizio, Limitatore
from .tmdb import GENERI_NO_KIDS, GENERI_PER_KIDS, adatto_ai_bambini

log = logging.getLogger("cerca.catalogo")

UN_GIORNO = 24 * 3600
# Quanto aspettare ogni risposta: meglio un messaggio chiaro che una pagina che carica all'infinito
TEMPO_MASSIMO = {"Radarr": 15, "Wikidata": 20, "TVmaze": 15, "JustWatch": 15}
PAUSA_DOPO_ERRORE = 60
_wikidata = Limitatore(per_secondo=1)
_justwatch = Limitatore(per_secondo=2)
_tvmaze = Limitatore(per_secondo=2)  # TVmaze chiede al massimo 20 richieste ogni 10 secondi

# id TMDB, nome italiano, nome inglese (Radarr), genere di Wikidata
GENERI_FILM = [
    (28, "Azione", "Action", "Q188473"),
    (12, "Avventura", "Adventure", "Q319221"),
    (16, "Animazione", "Animation", "Q202866"),
    (35, "Commedia", "Comedy", "Q157443"),
    (80, "Crime", "Crime", "Q959790"),
    (99, "Documentario", "Documentary", "Q93204"),
    (18, "Dramma", "Drama", "Q130232"),
    (10751, "Famiglia", "Family", "Q1361932"),
    (14, "Fantasy", "Fantasy", "Q157394"),
    (36, "Storia", "History", "Q17013749"),
    (27, "Horror", "Horror", "Q200092"),
    (10402, "Musica", "Music", "Q842256"),
    (9648, "Mistero", "Mystery", "Q1200678"),
    (10749, "Romance", "Romance", "Q1054574"),
    (878, "Fantascienza", "Science Fiction", "Q471839"),
    (53, "Thriller", "Thriller", "Q2484376"),
    (10752, "Guerra", "War", "Q369747"),
]
# id TMDB delle serie, nome italiano, generi di TVmaze
GENERI_SERIE = [
    (10759, "Action & Adventure", ("Action", "Adventure")),
    (16, "Animazione", ("Anime",)),
    (35, "Commedia", ("Comedy",)),
    (80, "Crime", ("Crime",)),
    (18, "Dramma", ("Drama",)),
    (10751, "Famiglia", ("Family",)),
    (10762, "Kids", ("Children",)),
    (9648, "Mistero", ("Mystery", "Thriller")),
    (10765, "Sci-Fi & Fantasy", ("Science-Fiction", "Fantasy", "Supernatural")),
    (10768, "Guerra e politica", ("War", "History")),
    (37, "Western", ("Western",)),
]
# Le piattaforme hanno gli stessi id di TMDB, così le scelte valgono anche con la chiave
PIATTAFORME = [
    # id, nome, elemento di Wikidata, nome su TVmaze
    (8, "Netflix", "Q907311", "Netflix"),
    (119, "Amazon Prime Video", "Q4740856", "Prime Video"),
    (337, "Disney Plus", "Q54958752", "Disney+"),
    (1899, "HBO Max", "Q65359104", "HBO Max"),
    (350, "Apple TV+", "", "Apple TV"),
    (531, "Paramount Plus", "Q27903045", "Paramount+"),
    (283, "Crunchyroll", "Q1142035", "Crunchyroll"),
]
TIPI_SERIE = {"Scripted", "Animation", "Documentary"}
ID_DA_INGLESE = {inglese.casefold(): i for i, _, inglese, _ in GENERI_FILM}
NOMI_FILM = {i: nome for i, nome, _, _ in GENERI_FILM}


def esterno_non_raggiungibile(nome: str) -> ErroreServizio:
    return ErroreServizio(
        f"{nome} non risponde.", "Controlla che il PC di mediabox sia collegato a Internet e riprova tra poco."
    )


def locandina(film: dict, misura: str = "w342") -> str:
    """Le immagini di Radarr arrivano in originale: chiediamo la misura giusta."""
    for immagine in film.get("images") or []:
        if immagine.get("coverType") == "poster" and immagine.get("remoteUrl"):
            return immagine["remoteUrl"].replace("/t/p/original/", f"/t/p/{misura}/")
    return film.get("remotePoster") or ""


def sfondo(film: dict) -> str:
    for immagine in film.get("images") or []:
        if immagine.get("coverType") == "fanart" and immagine.get("remoteUrl"):
            return immagine["remoteUrl"].replace("/t/p/original/", "/t/p/w1280/")
    return ""


def riassunto_radarr(f: dict) -> dict:
    voti = f.get("ratings") or {}
    voto = (voti.get("tmdb") or {}).get("value") or (voti.get("imdb") or {}).get("value") or 0
    return {
        "id": f.get("tmdbId"),
        "titolo": f.get("title") or f.get("originalTitle") or "",
        "titolo_originale": f.get("originalTitle") or f.get("title") or "",
        "anno": str(f.get("year") or "") if f.get("year") else "",
        "poster": locandina(f),
        "trama": f.get("overview") or "",
        "voto": round(float(voto), 1),
        "popolarita": f.get("popularity") or 0,
        "genre_ids": [ID_DA_INGLESE[g.casefold()] for g in f.get("genres") or [] if g.casefold() in ID_DA_INGLESE],
    }


# Radarr e TVmaze rispondono con schede piene di dati che non usiamo (attori, video,
# episodi...): la lista "Scopri" di Radarr arriva a diversi MB. Teniamo solo quello che
# serve, così cache, memoria e processore restano leggeri anche su un PC lento.
CAMPI_FILM = ("tmdbId", "title", "originalTitle", "year", "overview", "genres", "popularity", "inCinemas",
              "isTrending", "isPopular", "runtime", "imdbId", "remotePoster", "adult")
CAMPI_SERIE = ("id", "name", "image", "summary", "weight", "genres", "premiered", "type", "externals")


def snellisci_film(f: object) -> object:
    if not isinstance(f, dict):
        return f
    piccolo = {k: f[k] for k in CAMPI_FILM if k in f}
    piccolo["images"] = [{"coverType": i.get("coverType"), "remoteUrl": i.get("remoteUrl")}
                         for i in f.get("images") or [] if i.get("coverType") in ("poster", "fanart")]
    voti = f.get("ratings") or {}
    piccolo["ratings"] = {k: {"value": (voti.get(k) or {}).get("value")} for k in ("tmdb", "imdb") if voti.get(k)}
    return piccolo


def snellisci_radarr(dati: object) -> object:
    return [snellisci_film(f) for f in dati] if isinstance(dati, list) else snellisci_film(dati)


def snellisci_serie(s: dict) -> dict:
    piccola = {k: s[k] for k in CAMPI_SERIE if k in s}
    for k in ("webChannel", "network"):
        if s.get(k):
            piccola[k] = {"name": s[k].get("name")}
    return piccola


def snellisci_tvmaze(dati: object) -> object:
    """Ricerche e palinsesti: di ogni voce teniamo solo la serie."""
    if not isinstance(dati, list):
        return dati
    voci = []
    for x in dati:
        serie = (x.get("_embedded") or {}).get("show") or x.get("show") if isinstance(x, dict) else None
        if serie:
            voci.append({"show": snellisci_serie(serie)})
    return voci


def testo_semplice(html_testo: str | None) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", html_testo or "")).strip()


def riassunto_tvmaze(s: dict) -> dict:
    immagine = s.get("image") or {}
    canale = (s.get("webChannel") or {}).get("name") or (s.get("network") or {}).get("name") or ""
    return {
        "id": s.get("id"),
        "titolo": s.get("name") or "",
        "titolo_originale": s.get("name") or "",
        "anno": (s.get("premiered") or "")[:4],
        "poster": immagine.get("medium") or immagine.get("original") or "",
        "trama": testo_semplice(s.get("summary")),
        "popolarita": s.get("weight") or 0,
        "serie": True,
        "generi_tvmaze": s.get("genres") or [],
        "canale": canale,
        "premiere": s.get("premiered") or "",
        "imdb_id": (s.get("externals") or {}).get("imdb") or "",
        "tvdb": (s.get("externals") or {}).get("thetvdb"),
    }


# Nella home le righe usano solo le schede dei film già pronte: quelle mancanti si
# preparano in sottofondo, una alla volta, e compaiono alla visita successiva.
# Chiedere a Radarr 100 schede insieme bloccava i PC lenti (e la ricerca restava in coda).
solo_pronti: contextvars.ContextVar[bool] = contextvars.ContextVar("solo_pronti", default=False)
SCHEDE_IN_CODA = 200
PAUSA_TRA_SCHEDE = 0.5  # secondi: lascia respirare Radarr
SCHEDE_INSIEME = 2
MEMORIA_SECONDI = 300  # le risposte già lette restano in memoria 5 minuti
MEMORIA_MASSIMA = 400


# Le righe per genere della vista di una piattaforma (codici JustWatch)
VISTA_GENERI_FILM = [
    ("drm", "Film drammatici"), ("act", "Film d'azione"), ("cmy", "Film commedia"), ("fml", "Film per famiglie"),
    ("trl", "Thriller"), ("rma", "Film romantici"), ("scf", "Fantascienza"), ("hrr", "Horror"),
    ("ani", "Animazione"), ("crm", "Crime"), ("doc", "Documentari"),
]
VISTA_GENERI_SERIE = [
    ("drm", "Serie drammatiche"), ("crm", "Serie crime"), ("cmy", "Serie commedia"), ("act", "Azione e avventura"),
    ("scf", "Fantascienza e fantasy"), ("trl", "Thriller e misteri"), ("ani", "Animazione e anime"),
    ("fml", "Per tutta la famiglia"), ("doc", "Documentari"),
]


class CatalogoLibero:
    def __init__(self, config: Config, archivio: Archivio, client: httpx.AsyncClient) -> None:
        self.config = config
        self.archivio = archivio
        self.client = client
        self._contemporanei = asyncio.Semaphore(SCHEDE_INSIEME)
        self._coda: dict[int, None] = {}  # schede da preparare in sottofondo (in ordine)
        self._preparatore: asyncio.Task | None = None
        self._memoria: dict[str, tuple[float, object]] = {}  # le ultime risposte, già lette
        self._in_volo: dict[str, asyncio.Future] = {}  # la stessa richiesta chiesta da più righe insieme
        self._senza_scheda: dict[int, float] = {}  # film che Radarr non trova: non li richiediamo per un po'
        self._giu: dict[str, float] = {}  # servizio → fino a quando non richiamarlo

    # --- richieste con cache -------------------------------------------------------------
    @staticmethod
    def _chiave(url: str, parametri: dict) -> str:
        return f"libero2:{url}:{sorted(parametri.items())}"

    async def _json(self, url: str, parametri: dict, durata: float, *, intestazioni: dict | None = None,
                    limitatore: Limitatore | None = None, nome: str = "", snellisci=None,
                    corpo: dict | None = None) -> object:
        chiave = self._chiave(url, {**parametri, **({"corpo": json.dumps(corpo, sort_keys=True)} if corpo else {})})
        ricordo = self._memoria.get(chiave)
        if ricordo and ricordo[0] > time.monotonic():
            return ricordo[1]
        # se un'altra riga sta già chiedendo la stessa cosa, aspettiamo la sua risposta
        compito = self._in_volo.get(chiave)
        if compito is None:
            compito = asyncio.ensure_future(
                self._scarica(chiave, url, parametri, durata, intestazioni, limitatore, nome, snellisci, corpo))
            self._in_volo[chiave] = compito
            compito.add_done_callback(lambda _c: self._in_volo.pop(chiave, None))
        return await asyncio.shield(compito)

    def _ricorda(self, chiave: str, dati: object, durata: float) -> None:
        while len(self._memoria) >= MEMORIA_MASSIMA:
            self._memoria.pop(next(iter(self._memoria)))
        self._memoria[chiave] = (time.monotonic() + min(durata, MEMORIA_SECONDI), dati)

    async def _scarica(self, chiave, url, parametri, durata, intestazioni, limitatore, nome, snellisci,
                       corpo=None) -> object:
        # leggere e scrivere la cache in un thread a parte lascia Cerca libero di rispondere agli altri
        in_cache = await asyncio.to_thread(self.archivio.cache_leggi, chiave)
        if in_cache is not None:
            self._ricorda(chiave, in_cache, durata)
            return in_cache
        # un servizio che non ha risposto da poco non lo richiamiamo subito: niente attese a catena
        if time.monotonic() < self._giu.get(nome, 0):
            if (riserva := await self._riserva(chiave, nome)) is not None:
                return riserva
            raise self._non_risponde(nome)
        if limitatore:
            await limitatore.attendi()
        try:
            tempo = httpx.Timeout(TEMPO_MASSIMO.get(nome, 20), connect=5)
            if corpo is not None:
                r = await self.client.post(url, json=corpo, headers=intestazioni or {}, timeout=tempo)
            else:
                r = await self.client.get(url, params=parametri, headers=intestazioni or {}, timeout=tempo)
        except httpx.HTTPError as exc:
            self._giu[nome] = time.monotonic() + PAUSA_DOPO_ERRORE
            log.warning("%s non risponde: %s", nome, type(exc).__name__)
            if (riserva := await self._riserva(chiave, nome)) is not None:
                return riserva
            raise self._non_risponde(nome) from exc
        if r.status_code == 404:
            raise ErroreServizio(f"{nome} non conosce questo titolo.", "Torna indietro e scegline un altro.")
        if r.status_code == 429 or r.status_code >= 500:
            if (riserva := await self._riserva(chiave, nome)) is not None:
                return riserva
        if r.status_code == 429:
            raise ErroreServizio(f"{nome} chiede di rallentare.", "Riprova tra qualche minuto.")
        if not r.is_success:
            raise ErroreServizio(f"{nome} ha risposto con un errore ({r.status_code}).", "Riprova tra qualche minuto.")
        contenuto = r.content
        dati = await asyncio.to_thread(lambda: (snellisci or (lambda d: d))(json.loads(contenuto)))
        await asyncio.to_thread(self.archivio.cache_scrivi, chiave, dati, durata)
        self._ricorda(chiave, dati, durata)
        return dati

    async def _riserva(self, chiave: str, nome: str) -> object | None:
        """La risposta scaduta, se c'è: meglio quella di ieri che una pagina di errore."""
        try:
            dati = await asyncio.to_thread(self.archivio.cache_leggi, chiave, scadute=True)
        except TypeError:  # archivi senza riserva (nei test)
            return None
        if dati is not None:
            log.info("%s non risponde: uso l'ultima risposta salvata", nome)
        return dati

    @staticmethod
    def _non_risponde(nome: str) -> ErroreServizio:
        if nome == "Radarr":
            return ErroreServizio(
                "Radarr non risponde, e senza chiave TMDB i film arrivano da lui.",
                "Controlla che sia acceso: apri \"Controllo servizi\" in Cerca, oppure sul PC scrivi \"mediabox status\". "
                "Sui PC con poca memoria può volerci qualche minuto dopo l'accensione.",
            )
        return esterno_non_raggiungibile(nome)

    async def _radarr(self, percorso: str, parametri: dict, durata: float) -> object:
        return await self._json(
            f"{self.config.radarr_url}{percorso}", parametri, durata,
            intestazioni={"X-Api-Key": self.config.radarr_chiave}, nome="Radarr", snellisci=snellisci_radarr,
        )

    async def _wikidata(self, query: str, durata: float = 7 * UN_GIORNO) -> list[dict]:
        dati = await self._json(
            "https://query.wikidata.org/sparql", {"query": query, "format": "json"}, durata,
            intestazioni={"Accept": "application/sparql-results+json", "User-Agent": AGENTE},
            limitatore=_wikidata, nome="Wikidata",
        )
        return [{k: v.get("value", "") for k, v in riga.items()} for riga in dati.get("results", {}).get("bindings", [])]

    async def _tvmaze(self, percorso: str, parametri: dict, durata: float) -> object:
        return await self._json(f"https://api.tvmaze.com{percorso}", parametri, durata,
                                intestazioni={"User-Agent": AGENTE}, limitatore=_tvmaze, nome="TVmaze",
                                snellisci=snellisci_tvmaze)

    async def _jw(self, durata: float = 6 * 3600, **filtri) -> list[dict] | None:
        """Titoli di tendenza da JustWatch; None se JustWatch non risponde (si usano le altre fonti)."""
        try:
            elenco = await self._json(
                justwatch.INDIRIZZO, {}, durata, corpo=justwatch.corpo(paese=self.config.paese, **filtri),
                intestazioni={"User-Agent": AGENTE}, limitatore=_justwatch, nome="JustWatch",
                snellisci=justwatch.titoli,
            )
        except ErroreServizio as exc:
            log.info("JustWatch non disponibile: %s", exc.cosa)
            return None
        return list(elenco) if isinstance(elenco, list) else None

    # --- film --------------------------------------------------------------------------------
    async def cerca(self, testo: str) -> list[dict]:
        dati = await self._radarr("/api/v3/movie/lookup", {"term": testo}, UN_GIORNO)
        return [riassunto_radarr(f) for f in dati if f.get("tmdbId")][:20]

    async def cerca_kids(self, testo: str) -> list[dict]:
        return [f for f in await self.cerca(testo) if adatto_ai_bambini(f)]

    async def _film(self, tmdb_id: int) -> dict:
        return await self._radarr("/api/v3/movie/lookup/tmdb", {"tmdbId": tmdb_id}, 7 * UN_GIORNO)

    async def dettagli(self, tmdb_id: int) -> dict:
        dati = await self._film(tmdb_id)
        film = riassunto_radarr(dati)
        film.update(
            durata=dati.get("runtime") or 0,
            imdb_id=dati.get("imdbId") or "",
            generi=[NOMI_FILM[g] for g in film["genre_ids"] if g in NOMI_FILM],
            sfondo=sfondo(dati),
        )
        return film

    async def _da_id(self, ids: list[int], kids: bool = False) -> list[dict]:
        """Le schede di più film insieme (per le righe che arrivano da Wikidata)."""
        adesso = time.monotonic()
        ids = [i for i in dict.fromkeys(ids) if self._senza_scheda.get(i, 0) < adesso]
        if solo_pronti.get():
            pronte = await asyncio.to_thread(self._schede_pronte, ids)
            self._prepara([i for i in ids if i not in pronte])
            film = [riassunto_radarr(pronte[i]) for i in ids if i in pronte]
        else:
            async def uno(tmdb_id: int) -> dict | None:
                async with self._contemporanei:
                    try:
                        return riassunto_radarr(await self._film(tmdb_id))
                    except ErroreServizio:
                        self._senza_scheda[tmdb_id] = time.monotonic() + UN_GIORNO
                        return None

            film = list(await asyncio.gather(*(uno(i) for i in ids)))
        film = [f for f in film if f and f["poster"]]
        return [f for f in film if adatto_ai_bambini(f)] if kids else film

    def _schede_pronte(self, ids: list[int]) -> dict[int, dict]:
        url = f"{self.config.radarr_url}/api/v3/movie/lookup/tmdb"
        pronte = {}
        for i in ids:
            dati = self.archivio.cache_leggi(self._chiave(url, {"tmdbId": i}))
            if dati:
                pronte[i] = dati
        return pronte

    def _prepara(self, ids: list[int]) -> None:
        for i in ids:
            if len(self._coda) >= SCHEDE_IN_CODA:
                break
            self._coda.setdefault(i, None)
        if self._coda and (self._preparatore is None or self._preparatore.done()):
            self._preparatore = asyncio.create_task(self._prepara_in_sottofondo())

    async def _prepara_in_sottofondo(self) -> None:
        while self._coda:
            tmdb_id = next(iter(self._coda))
            self._coda.pop(tmdb_id, None)
            if time.monotonic() < self._giu.get("Radarr", 0):
                self._coda.clear()  # Radarr non risponde: riproveremo alla prossima visita
                return
            try:
                async with self._contemporanei:
                    await self._film(tmdb_id)
            except ErroreServizio:
                self._senza_scheda[tmdb_id] = time.monotonic() + UN_GIORNO
            except Exception:  # noqa: BLE001 - la preparazione in sottofondo non deve mai fermarsi male
                log.exception("Errore nel preparare la scheda %s", tmdb_id)
            await asyncio.sleep(PAUSA_TRA_SCHEDE)

    async def _scopri_radarr(self) -> list[dict]:
        """Film di tendenza e popolari della pagina "Scopri" di Radarr."""
        dati = await self._radarr(
            "/api/v3/importlist/movie",
            {"includeRecommendations": "false", "includeTrending": "true", "includePopular": "true"},
            6 * 3600,
        )
        return [f for f in dati if f.get("tmdbId")]

    async def tendenze(self) -> list[dict]:
        film = [f for f in await self._scopri_radarr() if f.get("isTrending")] or await self._scopri_radarr()
        film = sorted(film, key=lambda f: f.get("popularity") or 0, reverse=True)
        return [riassunto_radarr(f) for f in film if locandina(f)][:18]

    async def al_cinema(self, oggi: dt.date | None = None) -> list[dict]:
        oggi = oggi or dt.date.today()
        dal = (oggi - dt.timedelta(days=90)).isoformat()
        film = [f for f in await self._scopri_radarr() if dal <= (f.get("inCinemas") or "")[:10] <= oggi.isoformat()]
        film = sorted(film, key=lambda f: f.get("popularity") or 0, reverse=True)
        unici = {f["tmdbId"]: f for f in film}
        return [riassunto_radarr(f) for f in unici.values() if locandina(f)][:18]

    # --- serie -------------------------------------------------------------------------------
    async def cerca_serie(self, testo: str) -> list[dict]:
        dati = await self._tvmaze("/search/shows", {"q": testo}, UN_GIORNO)
        return [riassunto_tvmaze(x["show"]) for x in dati if x.get("show")][:20]

    async def _serie_del_momento(self, oggi: dt.date | None = None) -> list[dict]:
        """Le serie con episodi usciti negli ultimi giorni (streaming e TV italiana), dalla più seguita."""
        oggi = oggi or dt.date.today()
        serie: dict[int, dict] = {}
        for giorni in range(3):
            data = (oggi - dt.timedelta(days=giorni)).isoformat()
            try:
                web = await self._tvmaze("/schedule/web", {"date": data}, 6 * 3600)
                tv = await self._tvmaze("/schedule", {"country": self.config.paese, "date": data}, 6 * 3600)
            except ErroreServizio:
                continue
            for episodio in list(web) + list(tv):
                show = (episodio.get("_embedded") or {}).get("show") or episodio.get("show") or {}
                # solo serie vere, animazione e documentari: niente sport, talk show e notiziari
                if show.get("id") and show.get("image") and show.get("type", "Scripted") in TIPI_SERIE:
                    serie[show["id"]] = show
        if not serie:
            raise esterno_non_raggiungibile("TVmaze")
        return [riassunto_tvmaze(s) for s in sorted(serie.values(), key=lambda s: s.get("weight") or 0, reverse=True)]

    async def tendenze_serie(self) -> list[dict]:
        if (serie := await self._jw(serie=True)) is not None and serie:
            return serie[:18]
        return (await self._serie_del_momento())[:18]

    # --- home in stile Netflix --------------------------------------------------------------
    async def generi(self, tipo: str = "movie", lingua: str | None = None) -> list[dict]:
        if tipo == "tv":
            return [{"id": i, "nome": nome} for i, nome, _ in GENERI_SERIE]
        return [{"id": i, "nome": nome} for i, nome, _, _ in GENERI_FILM]

    async def generi_kids(self) -> list[dict]:
        return [{"id": g, "nome": NOMI_FILM[g]} for g in GENERI_PER_KIDS if g in NOMI_FILM]

    async def scopri(self, tipo: str, genere: int, pagina: int = 1, kids: bool = False) -> list[dict]:
        # prima JustWatch: i titoli di quel genere di tendenza sulle piattaforme
        codice = justwatch.GENERI.get(genere)
        if codice and pagina <= 5:
            per_pagina = 24
            per_bambini = kids or (tipo == "tv" and genere == 10762)  # il genere "Kids" delle serie
            elenco = await self._jw(serie=tipo == "tv", generi=[codice], quanti=per_pagina * pagina,
                                    escludi=justwatch.NO_KIDS if per_bambini else None)
            if elenco:
                elenco = [t for t in elenco if adatto_ai_bambini(t)] if kids and tipo != "tv" else elenco
                return elenco[(pagina - 1) * per_pagina:pagina * per_pagina]
        if tipo == "tv":
            if pagina > 1:
                return []
            nomi = next((n for i, _, n in GENERI_SERIE if i == genere), ())
            return [s for s in await self._serie_del_momento() if set(s["generi_tvmaze"]) & set(nomi)]
        qid = next((q for i, _, _, q in GENERI_FILM if i == genere), "")
        try:
            if not qid:
                raise ErroreServizio("Genere sconosciuto.", "")
            escludi = ""
            if kids:  # niente horror, thriller, crime e guerra tra i cartoni
                qid_esclusi = [q for i, _, _, q in GENERI_FILM if i in GENERI_NO_KIDS]
                valori = " ".join(f"wd:{q}" for q in qid_esclusi)
                escludi = "FILTER NOT EXISTS { ?f wdt:P136 ?no . VALUES ?no { " + valori + " } }"
            righe = await self._wikidata(
                "SELECT ?tmdb WHERE { ?f wdt:P136 wd:" + qid + " ; wdt:P4947 ?tmdb ; wikibase:sitelinks ?s . "
                + escludi + " } ORDER BY DESC(?s) LIMIT 24 OFFSET " + str((max(1, pagina) - 1) * 24)
            )
            ids = [int(r["tmdb"]) for r in righe if r.get("tmdb", "").isdigit()]
            film = await self._da_id(ids, kids=kids)
            if not film or (solo_pronti.get() and len(film) < 6):
                raise ErroreServizio("Schede non ancora pronte.", "")  # intanto i film del momento di quel genere
            # Wikidata a volte mette un film in troppi generi: teniamo quelli che anche TMDB (via Radarr) mette lì
            stesso_genere = [f for f in film if genere in f["genre_ids"]]
            return stesso_genere if len(stesso_genere) >= 6 else film
        except ErroreServizio:
            if pagina > 1:
                return []
            # Wikidata non risponde: ci accontentiamo dei film del momento di quel genere
            film = [riassunto_radarr(f) for f in await self._scopri_radarr() if locandina(f)]
            film = [f for f in film if genere in f["genre_ids"]]
            return [f for f in film if adatto_ai_bambini(f)] if kids else film

    async def kids_cartoni(self) -> list[dict]:
        return (await self.scopri("movie", 16, kids=True))[:18]

    async def kids_famiglia(self) -> list[dict]:
        return (await self.scopri("movie", 10751, kids=True))[:18]

    async def top_settimana(self, tipo: str) -> list[dict]:
        if elenco := await self._jw(serie=tipo == "tv", quanti=20):
            return elenco[:10]
        if tipo == "tv":
            return (await self._serie_del_momento())[:10]
        return (await self.tendenze())[:10]

    async def appena_usciti(self, tipo: str, oggi: dt.date | None = None) -> list[dict]:
        oggi = oggi or dt.date.today()
        # i più visti tra quelli usciti quest'anno e l'anno scorso
        if elenco := await self._jw(serie=tipo == "tv", dal_anno=oggi.year - 1, quanti=30):
            return elenco[:10]
        if tipo == "tv":
            dal = (oggi - dt.timedelta(days=120)).isoformat()
            return [s for s in await self._serie_del_momento(oggi) if s["premiere"] >= dal][:10]
        return (await self.al_cinema(oggi))[:10]

    async def id_genere(self, tipo: str, nomi: list[str]) -> dict[str, int]:
        tabella: dict[str, int] = {}
        if tipo == "tv":
            for i, nome, inglesi in GENERI_SERIE:
                tabella[nome.casefold()] = i
                for n in inglesi:
                    tabella.setdefault(n.casefold(), i)
        else:
            for i, nome, inglese, _ in GENERI_FILM:
                tabella[nome.casefold()] = i
                tabella[inglese.casefold()] = i
        return {n: tabella[n.casefold()] for n in nomi if n.casefold() in tabella}

    async def piattaforme(self, tipo: str = "movie") -> list[dict]:
        return [{"id": i, "nome": nome, "logo": "", "titolo_riga": f"Di tendenza su {nome}"}
                for i, nome, _ in justwatch.PIATTAFORME]

    async def su_piattaforma(self, tipo: str, piattaforma: int, pagina: int = 1) -> list[dict]:
        codice = justwatch.CODICI.get(piattaforma)
        if codice and pagina <= 5:
            per_pagina = 24
            elenco = await self._jw(serie=tipo == "tv", piattaforme=[codice], quanti=per_pagina * pagina)
            if elenco is not None:
                return elenco[(pagina - 1) * per_pagina:pagina * per_pagina]
        voce = next((p for p in PIATTAFORME if p[0] == piattaforma), None)
        if voce is None or pagina > 1:
            return []
        _, _, qid, nome_tvmaze = voce
        if tipo == "tv":
            return [s for s in await self._serie_del_momento() if s["canale"].casefold().startswith(nome_tvmaze.casefold())]
        # i film distribuiti dalla piattaforma (i suoi "originali") degli ultimi 3 anni, dai più conosciuti
        dal = (dt.date.today() - dt.timedelta(days=3 * 365)).isoformat()
        # conta la prima uscita: Wikidata segna anche quando un film vecchio arriva sulla piattaforma
        righe = await self._wikidata(
            "SELECT ?tmdb (MAX(?s) AS ?noto) WHERE { ?f wdt:P750 wd:" + qid + " ; wdt:P4947 ?tmdb ; wdt:P577 ?d ; "
            "wikibase:sitelinks ?s . } GROUP BY ?tmdb "
            'HAVING(MIN(?d) >= "' + dal + '"^^xsd:dateTime && MIN(?d) <= NOW()) ORDER BY DESC(?noto) LIMIT 30',
            UN_GIORNO,
        )
        ids = list(dict.fromkeys(int(r["tmdb"]) for r in righe if r.get("tmdb", "").isdigit()))
        return await self._da_id(ids[:24])

    async def vista_piattaforma(self, tipo: str, piattaforma: int, oggi: dt.date | None = None) -> dict:
        """La "home" di una piattaforma: in evidenza, Top 10, novità e una riga per genere.

        Tutto con una sola richiesta a JustWatch (salvata 6 ore): si apre in un attimo.
        """
        voce = next((p for p in justwatch.PIATTAFORME if p[0] == piattaforma), None)
        if voce is None:
            raise ErroreServizio("Piattaforma sconosciuta.", "Torna indietro e scegline un'altra.")
        _, nome, codice = voce
        serie = tipo == "tv"
        oggi = oggi or dt.date.today()
        generi = VISTA_GENERI_SERIE if serie else VISTA_GENERI_FILM
        base = {"serie": serie, "piattaforme": [codice]}
        sezioni = {"top": {**base, "quanti": 12}, "nuovi": {**base, "dal_anno": oggi.year - 1, "quanti": 24}}
        for codice_genere, _titolo in generi:
            sezioni[f"g_{codice_genere}"] = {**base, "generi": [codice_genere], "quanti": 24}
        dati = await self._json(
            justwatch.INDIRIZZO, {}, 6 * 3600, corpo=justwatch.corpo_vista(sezioni, paese=self.config.paese),
            intestazioni={"User-Agent": AGENTE}, limitatore=_justwatch, nome="JustWatch",
            snellisci=justwatch.titoli_vista,
        )
        top = (dati.get("top") or [])[:10]
        posizioni = {t["id"]: i + 1 for i, t in enumerate(top)}
        nuovi = {t["id"] for t in dati.get("nuovi") or []}

        def etichette(elenco: list[dict]) -> list[dict]:
            return [{**t, **({"top10": posizioni[t["id"]]} if t["id"] in posizioni else {}),
                     **({"nuovo": True} if t["id"] in nuovi else {})} for t in elenco]

        parola = "delle serie" if serie else "dei film"
        righe = []
        if top:
            righe.append({"tipo": "top10", "titolo": f"Top 10 {parola} su {nome} oggi", "elementi": etichette(top)})
        if dati.get("nuovi"):
            righe.append({"tipo": "nuovi", "titolo": f"Novità su {nome}", "elementi": etichette(dati["nuovi"])})
        for codice_genere, titolo in generi:
            elenco = dati.get(f"g_{codice_genere}") or []
            if len(elenco) >= 4:
                righe.append({"tipo": "genere", "titolo": titolo, "codice": codice_genere, "elementi": etichette(elenco)})
        in_evidenza = next((t for t in top if t.get("sfondo")), top[0] if top else None)
        return {"piattaforma": {"id": piattaforma, "nome": nome}, "tipo": tipo,
                "in_evidenza": {**in_evidenza, "top10": posizioni.get(in_evidenza["id"])} if in_evidenza else None,
                "righe": righe}

    async def provider(self, tmdb_id: int, durata_cache: float = 12 * 3600) -> dict:
        """Senza chiave TMDB non sappiamo dove si guarda: rimandiamo alla ricerca di JustWatch."""
        try:
            titolo = (await self.dettagli(tmdb_id))["titolo"]
        except ErroreServizio:
            titolo = ""
        return {
            "link": f"https://www.justwatch.com/it/cerca?q={quote(titolo)}" if titolo else "https://www.justwatch.com/it",
            "gruppi": [], "gratis": [], "senza_chiave": True,
        }
