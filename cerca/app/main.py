"""Cerca: ricerca unificata dei film per mediabox (TMDB + Internet Archive + Prowlarr)."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import mimetypes
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import diagnosi
from . import home as home_netflix
from .archive import InternetArchive
from .automatici import Automatici
from .avvisami import Avvisami
from .catalogo_libero import CatalogoLibero
from .config import Config
from .db import Archivio
from .download import GestoreDownload, e_magnet, nome_da_magnet
from .gruppi_download import raggruppa
from .jellyfin import Jellyfin
from .justwatch import CODICI as CODICI_PIATTAFORME
from .musica import Musica
from .prowlarr import Prowlarr, contenuto
from .pulizia import Pulizia
from .qbittorrent import QBittorrent
from .radarr import Radarr
from .rete import ErroreServizio, nuovo_client
from .sentinella import Sentinella, ultimo_blocco
from .serie import Serie
from .stato import ControlloDisco
from .tmdb import TMDB, tipo_tmdb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cerca")
# una riga per ogni richiesta fatta da Cerca: troppe, e sui PC lenti costano processore
logging.getLogger("httpx").setLevel(logging.WARNING)

CARTELLA = Path(__file__).parent
MAX_TORRENT = 10 * 1024 * 1024

STATI = {
    "in_corso": "Download in corso",
    "completato": "Scaricato, preparo l'importazione",
    "importazione": "Lo sto mettendo in Jellyfin…",
    "importato": "Pronto in Jellyfin",
    "da_associare": "Scegli di che film si tratta",
    "errore": "Problema",
}


class Servizi:
    """Contenitore dei servizi condivisi, creato all'avvio."""

    def __init__(self, config: Config, client: httpx.AsyncClient) -> None:
        self.config = config
        self.client = client
        self.archivio = Archivio(config.cartella_dati / "cerca.db")
        # con la chiave TMDB il catalogo completo, senza le fonti gratuite (Radarr, Wikidata, TVmaze)
        self.tmdb = TMDB(config, self.archivio, client) if config.con_tmdb else CatalogoLibero(config, self.archivio, client)
        # le vecchie risposte complete (diversi MB) occupavano l'archivio: si riscaricano snellite
        self.archivio.cache_togli("libero:")
        self.archive = InternetArchive(self.archivio, client)
        self.prowlarr = Prowlarr(config, self.archivio, client)
        self.qbit = QBittorrent(config, client)
        self.radarr = Radarr(config, client)
        self.jellyfin = Jellyfin(config, client)
        self.download = GestoreDownload(
            config, self.archivio, client, self.qbit, self.radarr, self.tmdb, self.jellyfin
        )
        self.avvisami = Avvisami(config, self.archivio, self.tmdb, client)
        self.download.notifica = self.avvisami.notifica
        self.serie = Serie(config, client)
        self.download.serie = self.serie
        self.automatici = Automatici(config, self.radarr, self.serie, self.qbit)
        self.musica = Musica(config, self.archivio, client, self.jellyfin)
        self.pulizia = Pulizia(config, self.archivio, client, self.qbit, self.jellyfin, self.avvisami)
        self.disco = ControlloDisco(config, self.archivio, self.avvisami, self.pulizia)


async def manutenzione_cache(archivio: Archivio) -> None:
    """Ogni 6 ore: via le risposte vecchie e la cache sotto il limite (in un thread, senza fermare le pagine)."""
    while True:
        try:
            tolte = await asyncio.to_thread(archivio.cache_pulisci)
            tolte += await asyncio.to_thread(archivio.cache_limita)
            dimensione = await asyncio.to_thread(archivio.cache_dimensione)
            log.info("Cache: tolte %s risposte vecchie, ora occupa %.1f MB", tolte, dimensione / 1024**2)
        except Exception:  # noqa: BLE001 - la pulizia non deve mai fermare Cerca
            log.exception("Errore nella pulizia della cache")
        await asyncio.sleep(6 * 3600)


async def dopo(secondi: float, coro) -> None:
    await asyncio.sleep(secondi)
    await coro


@asynccontextmanager
async def ciclo_di_vita(app: FastAPI):
    config = Config()
    async with nuovo_client() as client:
        servizi = Servizi(config, client)
        app.state.servizi = servizi
        sentinella = Sentinella(cartella=config.cartella_dati)
        sentinella.avvia()
        compiti = [
            asyncio.create_task(sentinella.battito()),
            # i controlli di sottofondo partono dopo: all'accensione il processore serve alle pagine
            asyncio.create_task(dopo(20, servizi.download.ciclo())),
            asyncio.create_task(servizi.avvisami.ciclo()),
            asyncio.create_task(dopo(60, servizi.disco.ciclo())),
            asyncio.create_task(dopo(300, manutenzione_cache(servizi.archivio))),
            asyncio.create_task(dopo(90, servizi.serie.sorveglia_importazioni(servizi.jellyfin))),
            asyncio.create_task(servizi.automatici.ricerca_notturna()),
        ]
        log.info("Cerca è pronto")
        yield
        sentinella.ferma()
        for c in compiti:
            c.cancel()
        for c in compiti:
            with contextlib.suppress(asyncio.CancelledError):
                await c


app = FastAPI(title="Cerca", lifespan=ciclo_di_vita, docs_url=None, redoc_url=None, openapi_url=None)
# il manifest dell'app per il telefono va servito con il suo tipo
mimetypes.add_type("application/manifest+json", ".webmanifest")
app.mount("/static", StaticFiles(directory=CARTELLA / "static"), name="static")
modelli = Jinja2Templates(directory=CARTELLA / "templates")


def impronta_statici(cartella: Path) -> str:
    """Cambia quando cambia un file della pagina: va negli indirizzi (?v=…), così dopo un
    aggiornamento il browser scarica i file nuovi invece di usare quelli vecchi che ha in memoria."""
    h = hashlib.sha256()
    for f in sorted(cartella.rglob("*")):
        if f.is_file():
            h.update(f.relative_to(cartella).as_posix().encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:10]


def regole_cache(percorso: str, versionato: bool) -> str:
    """I file con ?v= non cambiano mai (un anno); gli altri file statici una settimana; pagine e API sempre fresche."""
    if "/static/" in percorso:
        return "public, max-age=31536000, immutable" if versionato else "public, max-age=604800"
    return "no-cache"


modelli.env.globals["v"] = impronta_statici(CARTELLA / "static")


@app.middleware("http")
async def intestazioni_cache(request: Request, call_next):
    risposta = await call_next(request)
    if "cache-control" not in risposta.headers:
        risposta.headers["Cache-Control"] = regole_cache(request.url.path, "v" in request.query_params)
    return risposta


def dimensione_leggibile(byte: int | None) -> str:
    if not byte:
        return "?"
    valore = float(byte)
    for unita in ("B", "KB", "MB", "GB", "TB"):
        if valore < 1024 or unita == "TB":
            return f"{valore:.1f} {unita}".replace(".", ",") if unita in ("GB", "TB") else f"{valore:.0f} {unita}"
        valore /= 1024
    return "?"


modelli.env.filters["dimensione"] = dimensione_leggibile


def servizi(request: Request) -> Servizi:
    return request.app.state.servizi


MONDI = {
    "film": {"nome": "Film", "segnaposto": "Cerca un film…", "url": "/"},
    "serie": {"nome": "Serie TV", "segnaposto": "Cerca una serie…", "url": "/serie"},
    "kids": {"nome": "Kids", "segnaposto": "Cerca un cartone…", "url": "/kids"},
    "musica": {"nome": "Musica", "segnaposto": "Cerca un artista…", "url": "/musica"},
}
COOKIE_KIDS = "mediabox_kids"


def in_kids(request: Request) -> bool:
    return request.cookies.get(COOKIE_KIDS) == "si"


def pagina(request: Request, nome: str, **contesto) -> HTMLResponse:
    s = servizi(request)
    contesto.setdefault("errore", None)
    contesto.setdefault("mondo", "kids" if in_kids(request) else "film")
    contesto.setdefault("sezione", "cerca")
    attivi = [d for d in s.archivio.elenco_download(60) if d["stato"] in ("in_corso", "completato", "importazione")]
    return modelli.TemplateResponse(
        request,
        nome,
        {"base": s.config.root_path, "ntfy": bool(s.config.ntfy_topic), "disco": s.disco.stato(),
         "lidarr": bool(s.config.lidarr_url), "sonarr": bool(s.config.sonarr_url), "mondi": MONDI,
         "kids": in_kids(request), "download_attivi": len(attivi), "con_tmdb": s.config.con_tmdb, **contesto},
    )


def solo_adulti(request: Request) -> RedirectResponse | None:
    """In modalità Kids le pagine di download non si aprono."""
    if in_kids(request):
        return RedirectResponse(f"{servizi(request).config.root_path}/kids", status_code=303)
    return None


@app.exception_handler(ErroreServizio)
async def gestisci_errore(request: Request, exc: ErroreServizio):
    return JSONResponse(exc.come_dizionario(), status_code=502)


# --- pagine ----------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    s = servizi(request)
    if in_kids(request):
        return RedirectResponse(f"{s.config.root_path}/kids", status_code=303)
    avvisi = s.archivio.avvisami_elenco()
    return pagina(
        request,
        "home.html",
        mondo="film",
        ricerche=s.archivio.ultime_ricerche(8),
        gratis=[a for a in avvisi if a["gratis_dal"]],
        in_attesa=[a for a in avvisi if not a["gratis_dal"]],
        scaricati=[d for d in s.archivio.elenco_download(12) if d["stato"] == "importato"][:6],
    )


@app.get("/serie", response_class=HTMLResponse)
async def home_serie(request: Request):
    if (via := solo_adulti(request)) is not None:
        return via
    return pagina(request, "mondo.html", mondo="serie")


@app.get("/kids", response_class=HTMLResponse)
async def home_kids(request: Request):
    risposta = pagina(request, "mondo.html", mondo="kids", kids=True)
    risposta.set_cookie(COOKIE_KIDS, "si", max_age=365 * 86400, samesite="lax", path="/")
    return risposta


@app.get("/kids/esci")
async def esci_kids(request: Request):
    risposta = RedirectResponse(f"{servizi(request).config.root_path}/", status_code=303)
    risposta.delete_cookie(COOKIE_KIDS, path="/")
    return risposta


@app.get("/cerca", response_class=HTMLResponse)
async def risultati(request: Request, q: str = "", mondo: str = "film"):
    s = servizi(request)
    if in_kids(request):
        mondo = "kids"
    if mondo not in ("film", "serie", "kids"):
        mondo = "film"
    q = q.strip()
    if not q:
        return pagina(request, "risultati.html", q="", film=[], mondo=mondo)
    try:
        if mondo == "serie":
            film = await s.tmdb.cerca_serie(q)
        elif mondo == "kids":
            film = await s.tmdb.cerca_kids(q)
        else:
            film = await s.tmdb.cerca(q)
            s.archivio.aggiungi_ricerca(q)
    except ErroreServizio as exc:
        return pagina(request, "risultati.html", q=q, film=[], errore=exc, mondo=mondo)
    return pagina(request, "risultati.html", q=q, film=film, mondo=mondo)


@app.get("/film/{tmdb_id}", response_class=HTMLResponse)
async def scheda_film(request: Request, tmdb_id: int):
    s = servizi(request)
    try:
        film = await s.tmdb.dettagli(tmdb_id)
    except ErroreServizio as exc:
        return pagina(request, "film.html", film=None, errore=exc)
    return pagina(request, "film.html", film=film, avvisami=s.archivio.avvisami_presente(tmdb_id))


@app.get("/genere/{genere}", response_class=HTMLResponse)
async def pagina_genere(request: Request, genere: int, mondo: str = "film"):
    s = servizi(request)
    if in_kids(request):
        mondo = "kids"
    if mondo not in ("film", "serie", "kids"):
        mondo = "film"
    try:
        elenco = await (s.tmdb.generi_kids() if mondo == "kids" else s.tmdb.generi(tipo_tmdb(mondo)))
    except ErroreServizio as exc:
        return pagina(request, "genere.html", mondo=mondo, genere=None, generi=[], errore=exc)
    trovato = next((g for g in elenco if g["id"] == genere), None)
    if trovato is None:
        return RedirectResponse(f"{s.config.root_path}{MONDI[mondo]['url']}", status_code=303)
    return pagina(request, "genere.html", mondo=mondo, genere=trovato, generi=elenco)


@app.get("/piattaforma/{piattaforma}", response_class=HTMLResponse)
async def pagina_piattaforma(request: Request, piattaforma: int, mondo: str = "film"):
    s = servizi(request)
    if (via := solo_adulti(request)) is not None:
        return via
    mondo = "serie" if mondo == "serie" else "film"
    try:
        trovata = next((p for p in await s.tmdb.piattaforme(tipo_tmdb(mondo)) if p["id"] == piattaforma), None)
    except ErroreServizio as exc:
        return pagina(request, "piattaforma.html", mondo=mondo, piattaforma=None, errore=exc)
    if trovata is None:
        return RedirectResponse(f"{s.config.root_path}{MONDI[mondo]['url']}", status_code=303)
    # ogni piattaforma ha i suoi colori (solo i colori: niente loghi né nomi di marchi)
    return pagina(request, "piattaforma.html", mondo=mondo, piattaforma=trovata,
                  tema=CODICI_PIATTAFORME.get(piattaforma, ""))


@app.get("/diagnosi", response_class=HTMLResponse)
async def pagina_diagnosi(request: Request):
    return pagina(request, "diagnosi.html", sezione="diagnosi")


@app.get("/torrent", response_class=HTMLResponse)
async def pagina_torrent(request: Request):
    return solo_adulti(request) or pagina(request, "torrent.html", sezione="torrent")


@app.get("/download", response_class=HTMLResponse)
async def pagina_download(request: Request):
    return solo_adulti(request) or pagina(request, "download.html", sezione="download")


@app.get("/musica", response_class=HTMLResponse)
async def pagina_musica(request: Request):
    return solo_adulti(request) or pagina(request, "musica.html", mondo="musica")


@app.get("/spazio", response_class=HTMLResponse)
async def pagina_spazio(request: Request):
    return solo_adulti(request) or pagina(request, "spazio.html", sezione="spazio")


@app.get("/salute")
async def salute():
    return {"stato": "ok"}


# --- API usate dalle pagine ----------------------------------------------------------

@app.get("/api/tmdb")
async def api_tmdb(request: Request, q: str):
    return {"film": await servizi(request).tmdb.cerca(q)}


@app.get("/api/novita")
async def api_novita(request: Request, mondo: str = "film"):
    """Le due strisce della pagina iniziale di un mondo. Se una fallisce, l'altra si vede lo stesso."""
    s = servizi(request)
    if in_kids(request):
        mondo = "kids"
    fonti = {
        "film": (s.tmdb.tendenze, s.tmdb.al_cinema),
        "serie": (s.serie.nuovi_episodi, s.tmdb.tendenze_serie),
        "kids": (s.tmdb.kids_cartoni, s.tmdb.kids_famiglia),
    }.get(mondo, (s.tmdb.tendenze, s.tmdb.al_cinema))
    prima, seconda = await asyncio.gather(fonti[0](), fonti[1](), return_exceptions=True)
    risposta: dict = {"prima": [], "seconda": [], "mondo": mondo}
    for chiave, valore in (("prima", prima), ("seconda", seconda)):
        if isinstance(valore, ErroreServizio):
            risposta["errore"] = valore.come_dizionario()
        elif isinstance(valore, list):
            risposta[chiave] = valore
    return risposta


HOME_PRONTE: dict[tuple, tuple[float, dict]] = {}
DURATA_HOME = 600  # la home già pronta vale 10 minuti: aprire Cerca non rifà tutto ogni volta


@app.get("/api/home")
async def api_home(request: Request, mondo: str = "film"):
    """Le righe della home in stile Netflix di Film e Serie TV."""
    s = servizi(request)
    if in_kids(request) or mondo not in ("film", "serie"):
        return {"righe": []}
    chiave = (mondo, tuple(piattaforme_scelte(s) or ()))
    pronta = HOME_PRONTE.get(chiave)
    if pronta and pronta[0] > time.monotonic():
        return pronta[1]
    risposta = await home_netflix.righe(s.tmdb, s.jellyfin, tipo_tmdb(mondo), piattaforme_scelte(s))
    if mondo == "serie" and s.config.sonarr_url:
        try:
            episodi = await s.serie.nuovi_episodi()
        except ErroreServizio:
            episodi = []
        if episodi:
            risposta["righe"].append({"tipo": "episodi", "titolo": "Nuovi episodi delle tue serie", "elementi": episodi})
    if risposta.get("righe") and not risposta.get("errore"):
        HOME_PRONTE[chiave] = (time.monotonic() + DURATA_HOME, risposta)
    return risposta


@app.get("/api/vista/{piattaforma}")
async def api_vista(request: Request, piattaforma: int, mondo: str = "film"):
    """La home di una piattaforma (Netflix, Prime Video…): in evidenza, Top 10, novità e generi."""
    s = servizi(request)
    tipo = tipo_tmdb("serie" if mondo == "serie" else "film")
    if hasattr(s.tmdb, "vista_piattaforma"):
        return await s.tmdb.vista_piattaforma(tipo, piattaforma)
    # con la chiave TMDB: una riga sola con i più visti
    nome = next((p["nome"] for p in await s.tmdb.piattaforme(tipo) if p["id"] == piattaforma), "")
    elenco = await s.tmdb.su_piattaforma(tipo, piattaforma)
    return {"piattaforma": {"id": piattaforma, "nome": nome}, "tipo": tipo,
            "in_evidenza": elenco[0] if elenco else None,
            "righe": [{"tipo": "genere", "titolo": f"I più visti su {nome}", "elementi": elenco}] if elenco else []}


def piattaforme_scelte(s: Servizi) -> list[int] | None:
    """Le piattaforme scelte nella home (None = quelle di partenza)."""
    try:
        valore = json.loads(s.archivio.stato_leggi("piattaforme") or "null")
    except ValueError:
        return None
    return [int(x) for x in valore if str(x).isdigit()] if isinstance(valore, list) else None


@app.get("/api/piattaforme")
async def api_piattaforme(request: Request, mondo: str = "film"):
    s = servizi(request)
    tutte = await s.tmdb.piattaforme(tipo_tmdb(mondo))
    scelte = piattaforme_scelte(s)
    if scelte is None:
        scelte = list(home_netflix.PIATTAFORME_INIZIALI)
    return {"tutte": tutte[:40], "scelte": scelte, "massimo": home_netflix.MASSIMO_PIATTAFORME}


class SceltaPiattaforme(BaseModel):
    scelte: list[int]


@app.post("/api/piattaforme")
async def api_piattaforme_salva(request: Request, dati: SceltaPiattaforme):
    s = servizi(request)
    if in_kids(request):
        return JSONResponse({"errore": "In Kids non si cambiano le piattaforme."}, status_code=403)
    scelte = list(dict.fromkeys(dati.scelte))[: home_netflix.MASSIMO_PIATTAFORME]
    s.archivio.stato_scrivi("piattaforme", json.dumps(scelte))
    return {"scelte": scelte, "messaggio": "Fatto: la home mostra le tue piattaforme."}


@app.get("/api/piattaforma/{piattaforma}")
async def api_piattaforma(request: Request, piattaforma: int, mondo: str = "film", pagina: int = 1):
    s = servizi(request)
    elementi = await s.tmdb.su_piattaforma(tipo_tmdb(mondo), piattaforma, pagina=pagina)
    return {"elementi": elementi, "pagina": pagina, "altri": len(elementi) >= 10 and pagina < 20}


@app.get("/api/diagnosi")
async def api_diagnosi(request: Request):
    """Quali servizi rispondono: serve a capire perché una pagina non si carica."""
    s = servizi(request)
    esito = await diagnosi.controlla(s.config, s.client)
    esito["ultimo_blocco"] = await asyncio.to_thread(ultimo_blocco, s.config.cartella_dati)
    return esito


@app.get("/api/generi")
async def api_generi(request: Request, mondo: str = "film"):
    s = servizi(request)
    if in_kids(request) or mondo == "kids":
        return {"generi": await s.tmdb.generi_kids(), "mondo": "kids"}
    mondo = "serie" if mondo == "serie" else "film"
    return {"generi": await s.tmdb.generi(tipo_tmdb(mondo)), "mondo": mondo}


@app.get("/api/genere/{genere}")
async def api_genere(request: Request, genere: int, mondo: str = "film", pagina: int = 1):
    s = servizi(request)
    kids = in_kids(request) or mondo == "kids"
    tipo = "movie" if kids else tipo_tmdb(mondo)
    elementi = await s.tmdb.scopri(tipo, genere, pagina=pagina, kids=kids)
    return {"elementi": elementi, "pagina": pagina, "altri": len(elementi) >= 10 and pagina < 20}


@app.get("/api/musica/per-te")
async def api_musica_per_te(request: Request):
    return await servizi(request).musica.per_te()


@app.get("/api/musica/tendenze")
async def api_musica_tendenze(request: Request):
    return {"artisti": await servizi(request).musica.tendenze()}


class RichiestaSerie(BaseModel):
    titolo: str = ""
    tvdb: int | None = None
    tmdb: int | None = None
    imdb: str = ""


@app.post("/api/serie/segui")
async def api_serie_segui(request: Request, dati: RichiestaSerie):
    """"Segui la serie": la aggiunge a Sonarr, che scarica tutte le puntate e poi quelle nuove."""
    if in_kids(request):
        return JSONResponse({"errore": "In modalità Kids non si aggiungono serie."}, status_code=403)
    return await servizi(request).serie.segui(tvdb=dati.tvdb, tmdb=dati.tmdb, imdb=dati.imdb, titolo=dati.titolo)


@app.get("/api/serie/{serie_id}/torrent")
async def api_serie_torrent(request: Request, serie_id: int, nome: str = "", originale: str = ""):
    """Le puntate da scaricare: cercate con il titolo italiano, quello originale e quelli di Sonarr."""
    s = servizi(request)
    try:
        titoli_sonarr = await s.serie.titoli(serie_id)
    except ErroreServizio:
        titoli_sonarr = []
    return await s.prowlarr.cerca_serie(serie_id, [nome, originale, *titoli_sonarr])


class RichiestaPuntate(BaseModel):
    serie_id: int
    id: str
    nome: str = ""
    poster: str = ""


@app.post("/api/serie/scarica")
async def api_serie_scarica(request: Request, dati: RichiestaPuntate):
    s = servizi(request)
    originale = await asyncio.to_thread(s.prowlarr.risultato_originale, dati.serie_id, dati.id, "serie")
    magnet, file_torrent = await s.prowlarr.scarica_torrent(originale)
    cosa = contenuto(originale.get("title", ""))
    titolo = f"{dati.nome} · {cosa}" if dati.nome and cosa else (dati.nome or originale.get("title", ""))
    id_download = await s.download.da_torrent(
        fonte="prowlarr", categoria="cerca", film=None, titolo=titolo, magnet=magnet, file_torrent=file_torrent,
        serie=dati.serie_id, poster=dati.poster,
    )
    return {"id": id_download,
            "messaggio": "Download avviato: lo segui nella pagina Download. Quando finisce, la puntata va in Jellyfin."}


class RichiestaArtista(BaseModel):
    mbid: str


@app.post("/api/musica/lidarr")
async def api_musica_lidarr(request: Request, dati: RichiestaArtista):
    return {"messaggio": await servizi(request).musica.aggiungi_a_lidarr(dati.mbid)}


@app.get("/api/stato")
async def api_stato(request: Request):
    return await servizi(request).disco.aggiorna()


@app.get("/api/pulizia")
async def api_pulizia(request: Request):
    return await servizi(request).pulizia.anteprima()


@app.post("/api/pulizia")
async def api_pulizia_avvia(request: Request):
    esito = await servizi(request).pulizia.libera("richiesta dalla pagina Spazio")
    gb = esito["liberati"] / 1024**3
    if not esito["tolti"]:
        return {**esito, "messaggio": "Non c'era niente da togliere."}
    return {**esito, "messaggio": f"Liberati {gb:.1f} GB ({esito['tolti']} elementi)."}


@app.get("/api/film/{tmdb_id}/provider")
async def api_provider(request: Request, tmdb_id: int):
    return await servizi(request).tmdb.provider(tmdb_id)


@app.get("/api/film/{tmdb_id}/archive")
async def api_archive(request: Request, tmdb_id: int):
    s = servizi(request)
    film = await s.tmdb.dettagli(tmdb_id)
    return await s.archive.migliori_versioni(film)


@app.get("/api/film/{tmdb_id}/prowlarr")
async def api_prowlarr(request: Request, tmdb_id: int):
    s = servizi(request)
    film = await s.tmdb.dettagli(tmdb_id)
    return await s.prowlarr.cerca(film)


class RichiestaArchive(BaseModel):
    tmdb_id: int
    identificativo: str
    nome: str
    modo: str = "http"


@app.post("/api/scarica/archive")
async def api_scarica_archive(request: Request, dati: RichiestaArchive):
    s = servizi(request)
    film = await s.tmdb.dettagli(dati.tmdb_id)
    versioni = (await s.archive.migliori_versioni(film))["versioni"]
    versione = next(
        (v for v in versioni if v["identificativo"] == dati.identificativo and v["nome"] == dati.nome), None
    )
    if versione is None:
        raise ErroreServizio("Questa versione non è più disponibile.", "Ricarica la pagina e riprova.")
    if dati.modo == "torrent":
        id_download = await s.download.da_archive_torrent(film, versione)
    else:
        id_download = await s.download.da_archive_http(film, versione)
    return {"id": id_download, "messaggio": "Download avviato: lo trovi nella pagina Download."}


class RichiestaAuto(BaseModel):
    tmdb_id: int


@app.post("/api/scarica/auto")
async def api_scarica_auto(request: Request, dati: RichiestaAuto):
    s = servizi(request)
    film = await s.radarr.scarica_da_solo(dati.tmdb_id)
    gia = " (il file c'è già: cerco solo una versione migliore)" if film.get("hasFile") else ""
    return {"messaggio": f"Fatto! Radarr cerca la versione migliore, prima in italiano, e la mette in Jellyfin{gia}."}


class RichiestaProwlarr(BaseModel):
    tmdb_id: int
    id: str


@app.post("/api/scarica/prowlarr")
async def api_scarica_prowlarr(request: Request, dati: RichiestaProwlarr):
    s = servizi(request)
    film = await s.tmdb.dettagli(dati.tmdb_id)
    originale = await asyncio.to_thread(s.prowlarr.risultato_originale, dati.tmdb_id, dati.id)
    magnet, file_torrent = await s.prowlarr.scarica_torrent(originale)
    id_download = await s.download.da_torrent(
        fonte="prowlarr", categoria="cerca", film=film, titolo=originale.get("title", ""),
        magnet=magnet, file_torrent=file_torrent,
    )
    return {"id": id_download, "messaggio": "Download avviato in qBittorrent: lo trovi nella pagina Download."}


@app.post("/api/torrent")
async def api_torrent(
    request: Request,
    link: str = Form(""),
    tmdb_id: str = Form(""),
    file: UploadFile | None = File(None),
):
    s = servizi(request)
    link = link.strip()
    film = await s.tmdb.dettagli(int(tmdb_id)) if tmdb_id.strip().isdigit() else None
    contenuto = None
    if file is not None and file.filename:
        contenuto = await file.read(MAX_TORRENT + 1)
        if len(contenuto) > MAX_TORRENT or not contenuto.startswith(b"d"):
            raise ErroreServizio("Il file scelto non sembra un file .torrent.", "Scegli un file che finisce in .torrent.")
        titolo = Path(file.filename).stem
        magnet = None
    elif e_magnet(link):
        magnet, titolo = link, nome_da_magnet(link)
    elif link.startswith(("http://", "https://")):
        # Scarichiamo noi il .torrent: così gli errori sono chiari
        try:
            r = await s.client.get(link, timeout=30)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ErroreServizio(
                "Non riesco a scaricare il file .torrent da quell'indirizzo.",
                "Controlla il link, oppure scarica il file sul tuo dispositivo e caricalo qui.",
            ) from exc
        contenuto, magnet, titolo = r.content, None, Path(link.split("?")[0]).stem
        if not contenuto.startswith(b"d"):
            raise ErroreServizio("Quell'indirizzo non porta a un file .torrent.", "Controlla il link.")
    else:
        raise ErroreServizio(
            "Non hai inserito un torrent.",
            "Incolla un link magnet (inizia con \"magnet:?\"), l'indirizzo di un file .torrent, oppure carica un file.",
        )
    id_download = await s.download.da_torrent(
        fonte="manuale", categoria="manuale", film=film, titolo=titolo, magnet=magnet, file_torrent=contenuto
    )
    return {"id": id_download, "messaggio": "Torrent aggiunto: lo trovi nella pagina Download."}


@app.get("/api/download/gruppi")
async def api_download_gruppi(request: Request):
    """La pagina Download: una scheda per film o serie, con la copertina e lo stato di ogni voce."""
    s = servizi(request)
    coda, attesa = await asyncio.gather(s.automatici.coda(), s.automatici.in_attesa())
    cerca = (await api_download(request))["download"]
    return {"gruppi": raggruppa(coda, attesa, cerca, s.automatici.preferita), "lingua_preferita": s.automatici.preferita}


@app.get("/api/download")
async def api_download(request: Request):
    elenco = []
    for d in servizi(request).archivio.elenco_download(60):
        percentuale = round(100 * d["scaricati"] / d["totale"], 1) if d["totale"] else 0
        if d["stato"] in ("completato", "importazione", "importato"):
            percentuale = 100
        elenco.append({
            **d,
            "percentuale": percentuale,
            "stato_testo": STATI.get(d["stato"], d["stato"]),
            "scaricati_testo": dimensione_leggibile(d["scaricati"]),
            "totale_testo": dimensione_leggibile(d["totale"]),
            "velocita_testo": f"{dimensione_leggibile(d['velocita'])}/s" if d["velocita"] else "",
        })
    return {"download": elenco}


class RichiestaAssocia(BaseModel):
    tmdb_id: int


@app.get("/api/automatici")
async def api_automatici(request: Request):
    """Cosa scaricano o cercano Radarr/Sonarr e cosa aspetta ancora la lingua preferita."""
    s = servizi(request)
    coda, attesa = await asyncio.gather(s.automatici.coda(), s.automatici.in_attesa())
    return {"coda": coda, "attesa": attesa, "lingua_preferita": s.automatici.preferita}


class RichiestaAutomatici(BaseModel):
    tipo: str = "film"
    ids: list[int] = []


@app.post("/api/automatici/annulla")
async def api_automatici_annulla(request: Request, dati: RichiestaAutomatici):
    return {"messaggio": await servizi(request).automatici.annulla(dati.tipo, dati.ids)}


@app.post("/api/automatici/riprova")
async def api_automatici_riprova(request: Request, dati: RichiestaAutomatici):
    return {"messaggio": await servizi(request).automatici.cerca_di_nuovo(dati.tipo, dati.ids)}


@app.post("/api/automatici/cerca")
async def api_automatici_cerca(request: Request):
    await servizi(request).automatici.cerca_tutto()
    return {"messaggio": "Ricerca avviata: Radarr e Sonarr cercano le versioni migliori."}


@app.get("/api/film/{tmdb_id}/stato")
async def api_film_stato(request: Request, tmdb_id: int):
    return await servizi(request).automatici.stato_film(tmdb_id)


@app.post("/api/serie/apri")
async def api_serie_apri(request: Request, dati: RichiestaSerie):
    """La serie in Sonarr con stagioni e puntate, per scegliere cosa scaricare."""
    if in_kids(request):
        return JSONResponse({"errore": "In modalità Kids non si aggiungono serie."}, status_code=403)
    s = servizi(request)
    serie = await s.serie.prepara(tvdb=dati.tvdb, tmdb=dati.tmdb, imdb=dati.imdb, titolo=dati.titolo)
    return {"serie_id": serie["id"], "stagioni": await s.serie.puntate(serie["id"])}


@app.get("/api/serie/{serie_id}/puntate")
async def api_serie_puntate(request: Request, serie_id: int):
    return {"serie_id": serie_id, "stagioni": await servizi(request).serie.puntate(serie_id)}


class SceltaPuntate(BaseModel):
    puntate: list[int] = []
    stagione: int | None = None
    tutte: bool = False


@app.post("/api/serie/{serie_id}/scarica")
async def api_serie_scarica_puntate(request: Request, serie_id: int, dati: SceltaPuntate):
    s = servizi(request)
    if dati.tutte:
        await s.serie.scarica_tutte(serie_id)
        messaggio = "Scarico tutte le puntate, e anche quelle nuove appena escono."
    elif dati.stagione is not None:
        await s.serie.scarica_tutte(serie_id, dati.stagione)
        messaggio = f"Scarico la stagione {dati.stagione}."
    else:
        await s.serie.scarica_puntate(dati.puntate)
        messaggio = "Scarico la puntata." if len(dati.puntate) == 1 else f"Scarico {len(dati.puntate)} puntate."
    return {"messaggio": messaggio}


@app.post("/api/serie/{serie_id}/annulla")
async def api_serie_annulla_puntate(request: Request, serie_id: int, dati: SceltaPuntate):
    s = servizi(request)
    puntate = dati.puntate
    if dati.stagione is not None or dati.tutte:
        stagioni = await s.serie.puntate(serie_id)
        puntate = [p["id"] for st in stagioni if dati.tutte or st["numero"] == dati.stagione for p in st["puntate"]]
    await s.serie.annulla_puntate(puntate)
    return {"messaggio": "Annullato: non le cerco e non le scarico più (quelle già in Jellyfin restano)."}


@app.post("/api/download/{id_download}/associa")
async def api_associa(request: Request, id_download: int, dati: RichiestaAssocia):
    s = servizi(request)
    film = await s.tmdb.dettagli(dati.tmdb_id)
    await s.download.associa(id_download, film)
    return {"messaggio": f"Associato a {film['titolo']}."}


@app.post("/api/download/{id_download}/riprova")
async def api_riprova(request: Request, id_download: int):
    await servizi(request).download.riprova(id_download)
    return {"messaggio": "Riprovo."}


@app.post("/api/download/{id_download}/annulla")
async def api_annulla(request: Request, id_download: int):
    await servizi(request).download.annulla(id_download)
    return {"messaggio": "Download tolto dall'elenco."}


@app.post("/api/avvisami/{tmdb_id}")
async def api_avvisami(request: Request, tmdb_id: int):
    s = servizi(request)
    if s.archivio.avvisami_presente(tmdb_id):
        s.archivio.avvisami_togli(tmdb_id)
        return {"attivo": False, "messaggio": "Non ti avviserò più per questo film."}
    film = await s.tmdb.dettagli(tmdb_id)
    s.archivio.avvisami_aggiungi(tmdb_id, film["titolo"], film["anno"], film["poster"])
    provider = await s.tmdb.provider(tmdb_id)
    if provider["gratis"]:
        s.archivio.avvisami_imposta(tmdb_id, time.time(), provider["gratis"], False)
        return {"attivo": True, "messaggio": "È già gratis adesso! Lo trovi in evidenza nella pagina iniziale."}
    return {"attivo": True, "messaggio": "Ok! Controllo ogni giorno e ti avviso quando diventa gratis."}
