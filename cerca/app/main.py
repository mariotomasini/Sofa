"""Cerca: ricerca unificata dei film per mediabox (TMDB + Internet Archive + Prowlarr)."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import mimetypes
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi import Path as FastPath
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from . import diagnosi
from . import home as home_netflix
from .accesso import COOKIE as COOKIE_UTENTE
from .accesso import DURATA as DURATA_ACCESSO
from .accesso import Accesso, destinazione_sicura, iniziali, percorso_libero, utente_corrente
from .animeunity import (
    NOMI_FONTI,
    TUTTE_LE_STAGIONI,
    AnimeDiretti,
    AnimeUnity,
    con_download_cerca,
    indovina_stagione,
    ordina_risultati,
)
from .animeworld import AnimeWorld
from .archive import InternetArchive
from .automatici import Automatici
from .avvisami import Avvisami
from .catalogo_libero import CatalogoLibero
from .config import Config
from .db import Archivio
from .deezer import Deezer
from .download import GestoreDownload, e_magnet, nome_cartella, nome_da_magnet
from .foto_profili import FotoProfili
from .gruppi_download import con_coda_qbit, raggruppa
from .jellyfin import Jellyfin
from .justwatch import CODICI as CODICI_PIATTAFORME
from .libreria import Libreria
from .musica import Musica
from .musica_download import ScaricaMusica
from .ordine_download import OrdineDownload, posizioni
from .prowlarr import Prowlarr, contenuto
from .pulizia import Pulizia
from .puntate_italiane import PuntateItaliane, ricorda_titoli
from .qbittorrent import QBittorrent
from .radarr import Radarr
from .rete import ErroreServizio, non_raggiungibile, nuovo_client
from .sentinella import Sentinella, ultimo_blocco
from .serie import Serie, e_anime
from .stato import ControlloDisco
from .streamingcommunity import StreamingCommunity
from .streamingcommunity import id_puntata as id_puntata_sc
from .tmdb import TMDB, tipo_tmdb
from .trailer import trova as trova_trailer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cerca")
# una riga per ogni richiesta fatta da Cerca: troppe, e sui PC lenti costano processore
logging.getLogger("httpx").setLevel(logging.WARNING)

CARTELLA = Path(__file__).parent
MAX_TORRENT = 10 * 1024 * 1024

STATI = {
    "ricerca": "Lo cerco sui tuoi indexer…",
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
        self.accesso = Accesso(config.cartella_dati)
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
        self.ordine = OrdineDownload(self.archivio, self.qbit)
        self.puntate_italiane = PuntateItaliane(config, self.archivio, self.serie, self.prowlarr, self.download)
        # le fonti dirette degli anime: link HTTP, preferiti ai torrent
        self.siti_anime = {"animeunity": AnimeUnity(config), "animeworld": AnimeWorld(config),
                           "streamingcommunity": StreamingCommunity(config)}
        self.anime = AnimeDiretti(self.siti_anime, self.archivio, self.serie, self.download, config)
        for fonte in self.siti_anime:
            self.download.risolutori[fonte] = self._link_diretto
        self.musica = Musica(config, self.archivio, client, self.jellyfin)
        self.deezer = Deezer(self.archivio, client)
        self.scarica_musica = ScaricaMusica(config, self.deezer, self.prowlarr, self.download)
        self.pulizia = Pulizia(config, self.archivio, client, self.qbit, self.jellyfin, self.avvisami)
        self.disco = ControlloDisco(config, self.archivio, self.avvisami, self.pulizia)
        self.libreria = Libreria(config, self.archivio, client, self.qbit, self.jellyfin, self.download, self.serie)
        self.foto_profili = FotoProfili(self.archivio, client, self.jellyfin)

    async def _link_diretto(self, voce: dict) -> tuple[str, dict]:
        return await self.anime.link(json.loads(str(voce["url"]).removeprefix("diretto:")))


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
    try:
        await asyncio.sleep(secondi)
    except asyncio.CancelledError:
        coro.close()  # Cerca si spegne prima della partenza: niente "coroutine never awaited"
        raise
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
            asyncio.create_task(dopo(45, servizi.ordine.ciclo())),
            asyncio.create_task(dopo(180, servizi.automatici.ciclo_tracce())),
            asyncio.create_task(dopo(600, servizi.puntate_italiane.ciclo())),
            asyncio.create_task(dopo(240, servizi.anime.ciclo())),
            asyncio.create_task(dopo(30, servizi.foto_profili.assegna())),
        ]
        log.info("Cerca è pronto")
        yield
        sentinella.ferma()
        for c in compiti:
            c.cancel()
        for c in compiti:
            with contextlib.suppress(asyncio.CancelledError):
                await c
        for sito in servizi.siti_anime.values():
            await sito.chiudi()


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


ID_UTENTE_JELLYFIN = re.compile(r"^[0-9a-f]{32}$")


@app.middleware("http")
async def chi_guarda(request: Request, call_next):
    """Ogni pagina sa chi sta guardando; chi non ha ancora scelto va a "Chi guarda?"."""
    s = request.app.state.servizi
    base = s.config.root_path
    utente = s.accesso.leggi(request.cookies.get(COOKIE_UTENTE))
    request.state.utente = utente
    # il percorso dentro Cerca: uvicorn parte con --root-path /cerca (vecchi indirizzi), e allora
    # request.url.path comincia con /cerca anche se Caddy lo ha già tolto
    percorso = request.url.path
    prefisso = request.scope.get("root_path") or ""
    if prefisso and percorso.startswith(prefisso):
        percorso = percorso[len(prefisso):] or "/"
    if utente is None and not percorso_libero(percorso):
        if percorso.startswith("/api/"):
            return JSONResponse({"errore": "Scegli prima chi sta guardando.", "cosa_fare": "Ricarica la pagina.",
                                 "accesso": True}, status_code=401)
        dopo = base + percorso + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"{base}/chi-guarda?{urlencode({'dopo': dopo})}", status_code=303)
    # "Continua a guardare" e i consigli seguono l'utente di Jellyfin scelto
    segno = utente_corrente.set(utente if utente and ID_UTENTE_JELLYFIN.match(utente["id"]) else None)
    try:
        return await call_next(request)
    finally:
        utente_corrente.reset(segno)


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
    "film": {"nome": "Film", "segnaposto": "Titolo del film", "url": "/"},
    "serie": {"nome": "Serie TV", "segnaposto": "Titolo della serie", "url": "/serie"},
    "kids": {"nome": "Kids", "segnaposto": "Titolo del cartone", "url": "/kids"},
    "musica": {"nome": "Musica", "segnaposto": "Artista, album o brano", "url": "/musica"},
}
COOKIE_KIDS = "mediabox_kids"


def in_kids(request: Request) -> bool:
    return request.cookies.get(COOKIE_KIDS) == "si"


PAGINE_INIZIALI = {"home.html", "mondo.html", "musica.html"}


def indirizzo_pannello(request: Request, config: Config) -> str:
    """Il Pannello (la vecchia pagina iniziale) sta sullo stesso PC, sulla sua porta."""
    return f"//{request.url.hostname or 'mediabox.local'}:{config.pannello_porta}/"


def utente_di(request: Request) -> dict | None:
    return getattr(request.state, "utente", None)


def e_admin(request: Request) -> bool:
    utente = utente_di(request)
    return bool(utente and utente["admin"])


def pagina(request: Request, nome: str, **contesto) -> HTMLResponse:
    s = servizi(request)
    utente = utente_di(request)
    contesto.setdefault("errore", None)
    contesto.setdefault("mondo", "kids" if in_kids(request) else "film")
    contesto.setdefault("sezione", "cerca")
    # il tasto "‹ Indietro" in alto a sinistra: ovunque tranne nelle pagine iniziali dei mondi
    contesto.setdefault("indietro", nome not in PAGINE_INIZIALI)
    attivi = [d for d in s.archivio.elenco_download(60)
              if d["stato"] in ("ricerca", "in_corso", "completato", "importazione")]
    return modelli.TemplateResponse(
        request,
        nome,
        {"base": s.config.root_path, "ntfy": bool(s.config.ntfy_topic), "disco": s.disco.stato(),
         # il Pannello è solo per l'amministratore: gli altri non lo vedono
         "pannello": indirizzo_pannello(request, s.config) if e_admin(request) else "",
         "utente": utente, "iniziali_utente": iniziali(utente["nome"]) if utente else "",
         "foto_utente": foto_utente(utente.get("id", ""), larghezza=96) if utente else "",
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
    # ogni piattaforma ha i suoi colori e il suo logo
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


@app.get("/musica/artista/{id_artista}", response_class=HTMLResponse)
async def pagina_artista(request: Request, id_artista: int):
    return solo_adulti(request) or pagina(request, "musica_artista.html", mondo="musica", id_artista=id_artista)


@app.get("/musica/album/{id_album}", response_class=HTMLResponse)
async def pagina_album_musica(request: Request, id_album: int):
    return solo_adulti(request) or pagina(request, "musica_album.html", mondo="musica", id_album=id_album)


@app.get("/musica/preferiti", response_class=HTMLResponse)
async def pagina_preferiti_musica(request: Request):
    return solo_adulti(request) or pagina(request, "musica_preferiti.html", mondo="musica")


@app.get("/musica/cerca", response_class=HTMLResponse)
async def pagina_cerca_musica(request: Request, q: str = ""):
    return solo_adulti(request) or pagina(request, "musica_cerca.html", mondo="musica", q=q.strip())


@app.get("/spazio", response_class=HTMLResponse)
async def pagina_spazio(request: Request):
    return solo_adulti(request) or pagina(request, "spazio.html", sezione="spazio")


def foto_utente(id_utente: str, etichetta: str | None = None, larghezza: int = 264) -> str:
    """L'immagine dell'utente presa da Jellyfin (che la serve a tutti, come le locandine)."""
    if not id_utente or id_utente == "amministratore":
        return ""
    if etichetta is not None and not etichetta:
        return ""
    extra = f"&tag={etichetta}" if etichetta else ""
    return f"/jellyfin/Users/{id_utente}/Images/Primary?width={larghezza}&quality=90{extra}"


@app.get("/chi-guarda", response_class=HTMLResponse)
async def pagina_chi_guarda(request: Request, dopo: str = ""):
    return await _chi_guarda(request, dopo)


async def _chi_guarda(request: Request, dopo: str, errore: str = "", scelto: str = "", status: int = 200):
    s = servizi(request)
    try:
        profili = await s.jellyfin.profili()
        problema = None
    except ErroreServizio as exc:
        profili, problema = None, exc
    if profili and any(not p.get("immagine") for p in profili):
        s.foto_profili.in_sottofondo()  # a chi è nuovo una foto: si vede alla prossima apertura
    di_casa = [{**p, "iniziali": iniziali(p["nome"]), "foto": foto_utente(p["id"], p.get("immagine"))}
               for p in profili or [] if not p["admin"]]
    amministratori = [p for p in profili or [] if p["admin"]]
    risposta = pagina(
        request, "chi_guarda.html", senza_menu=True, indietro=False, profili=di_casa,
        amministratori=amministratori, jellyfin_spento=problema is not None, errore=problema,
        errore_accesso=errore, scelto=scelto, nome_admin=s.config.utente,
        dopo=destinazione_sicura(dopo, s.config.root_path),
    )
    risposta.status_code = status
    return risposta


@app.post("/chi-guarda")
async def entra(request: Request, id: str = Form(""), nome: str = Form(""), password: str = Form(""),
                dopo: str = Form("")):
    s = servizi(request)
    animata = request.headers.get("x-cerca") == "accesso"

    async def rifiuta(messaggio: str, scelto: str, status: int):
        if animata:  # la pagina resta lì: scuote il campo e mostra il messaggio
            return JSONResponse({"errore": messaggio}, status_code=status)
        return await _chi_guarda(request, dopo, messaggio, scelto, status)

    utente = None
    if id:
        try:
            trovato = next((p for p in await s.jellyfin.profili() if p["id"] == id), None)
        except ErroreServizio:
            trovato = None
        if trovato is None:
            return await rifiuta("Questo utente non c'è più: scegline un altro.", "", 400)
        nome = trovato["nome"]
        if not trovato["admin"]:
            # gli utenti di casa entrano senza password: la password ce l'ha solo l'amministratore
            utente = {"id": trovato["id"], "nome": trovato["nome"], "admin": False}
    nome = nome.strip()
    if not nome:
        return await rifiuta("Scegli chi sta guardando.", "", 400)
    try:
        if utente is None:
            utente = await s.jellyfin.verifica(nome, password)
    except ErroreServizio:
        # Jellyfin spento: l'amministratore entra lo stesso con la password di mediabox
        if (nome == s.config.utente and password and s.config.password
                and hmac.compare_digest(password.encode(), s.config.password.encode())):
            utente = {"id": "amministratore", "nome": nome, "admin": True}
        else:
            return await rifiuta("Jellyfin non risponde: riprova tra poco.", id or nome, 503)
    if utente is None or not utente.get("id"):
        return await rifiuta("Password sbagliata: riprova.", id or nome, 401)
    vai = destinazione_sicura(dopo, s.config.root_path)
    risposta = JSONResponse({"vai": vai}) if animata else RedirectResponse(vai, status_code=303)
    risposta.set_cookie(COOKIE_UTENTE, s.accesso.cookie(utente), max_age=DURATA_ACCESSO, httponly=True,
                        samesite="lax", path="/")
    return risposta


@app.get("/esci")
async def esci(request: Request):
    """Cambia utente: si torna a "Chi guarda?"."""
    risposta = RedirectResponse(f"{servizi(request).config.root_path}/chi-guarda", status_code=303)
    risposta.delete_cookie(COOKIE_UTENTE, path="/")
    return risposta


@app.get("/salute")
async def salute():
    return {"stato": "ok"}


# --- API usate dalle pagine ----------------------------------------------------------

@app.get("/api/tmdb")
async def api_tmdb(request: Request, q: str):
    return {"film": await servizi(request).tmdb.cerca(q)}


@app.get("/api/trailer")
async def api_trailer(request: Request, id: int, tipo: str = "movie"):
    """Il trailer YouTube per l'anteprima al passaggio sulle locandine ("" se non c'è)."""
    return {"youtube": await trova_trailer(servizi(request), tipo, id)}


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
    scelte = piattaforme_scelte(s, utente_di(request))
    chiave = ((utente_di(request) or {}).get("id", ""), mondo, tuple(scelte or ()))
    pronta = HOME_PRONTE.get(chiave)
    if pronta and pronta[0] > time.monotonic():
        return pronta[1]
    risposta = await home_netflix.righe(s.tmdb, s.jellyfin, tipo_tmdb(mondo), scelte)
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


def chiave_piattaforme(utente: dict | None) -> str:
    return f"piattaforme:{utente['id']}" if utente else "piattaforme"


def piattaforme_scelte(s: Servizi, utente: dict | None = None) -> list[int] | None:
    """Le piattaforme scelte nella home da questo utente (None = quelle di partenza).

    Chi non ha ancora scelto le sue vede quelle scelte prima che ci fossero gli utenti."""
    try:
        valore = json.loads(s.archivio.stato_leggi(chiave_piattaforme(utente))
                            or s.archivio.stato_leggi("piattaforme") or "null")
    except ValueError:
        return None
    return [int(x) for x in valore if str(x).isdigit()] if isinstance(valore, list) else None


@app.get("/api/piattaforme")
async def api_piattaforme(request: Request, mondo: str = "film"):
    s = servizi(request)
    tutte = await s.tmdb.piattaforme(tipo_tmdb(mondo))
    scelte = piattaforme_scelte(s, utente_di(request))
    if scelte is None:
        scelte = list(home_netflix.PIATTAFORME_INIZIALI)
    # il codice (nfx, prv…) dà a ogni piattaforma i suoi colori quando la apri
    tutte = [{**p, "codice": CODICI_PIATTAFORME.get(p["id"], "")} for p in tutte[:40]]
    return {"tutte": tutte, "scelte": scelte, "massimo": home_netflix.MASSIMO_PIATTAFORME}


class SceltaPiattaforme(BaseModel):
    scelte: list[int]


@app.post("/api/piattaforme")
async def api_piattaforme_salva(request: Request, dati: SceltaPiattaforme):
    s = servizi(request)
    if in_kids(request):
        return JSONResponse({"errore": "In Kids non si cambiano le piattaforme."}, status_code=403)
    scelte = list(dict.fromkeys(dati.scelte))[: home_netflix.MASSIMO_PIATTAFORME]
    s.archivio.stato_scrivi(chiave_piattaforme(utente_di(request)), json.dumps(scelte))
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


@app.get("/api/musica/home")
async def api_musica_home(request: Request):
    s = servizi(request)
    return await s.musica.home(s.deezer)


@app.get("/api/musica/artista/{id_artista}")
async def api_musica_artista(request: Request, id_artista: int):
    return await servizi(request).deezer.pagina_artista(id_artista)


@app.get("/api/musica/album/{id_album}")
async def api_musica_album(request: Request, id_album: int):
    return await servizi(request).deezer.pagina_album(id_album)


@app.get("/api/musica/cerca")
async def api_musica_cerca(request: Request, q: str):
    return await servizi(request).deezer.cerca(q.strip())


@app.get("/api/musica/anteprima/{id_brano}")
async def api_musica_anteprima(request: Request, id_brano: int):
    return await servizi(request).deezer.anteprima(id_brano)


ID_JELLYFIN = r"^[0-9a-f]{32}$"


@app.get("/api/musica/preferiti")
async def api_musica_preferiti(request: Request):
    """I brani preferiti: quelli scaricati, nella Musica di Jellyfin, dal più recente."""
    return {"brani": await servizi(request).jellyfin.brani()}


async def _da_jellyfin(request: Request, percorso: str, passa: tuple[str, ...], cache: str = "") -> StreamingResponse:
    intestazioni = {k: v for k in ("range", "if-range") if (v := request.headers.get(k))}
    try:
        r = await servizi(request).jellyfin.apri(percorso, intestazioni)
    except httpx.HTTPError as exc:
        raise non_raggiungibile("Jellyfin", "/jellyfin") from exc
    if r.status_code >= 400:
        await r.aclose()
        return Response(status_code=404 if r.status_code == 404 else 502)
    risposta = {k: r.headers[k] for k in passa if k in r.headers}
    if cache:
        risposta["Cache-Control"] = cache
    return StreamingResponse(r.aiter_raw(), status_code=r.status_code, headers=risposta,
                             background=BackgroundTask(r.aclose))


@app.get("/api/musica/ascolta/{id_brano}")
async def api_musica_ascolta(request: Request, id_brano: str = FastPath(pattern=ID_JELLYFIN)):
    """Il brano intero, da Jellyfin (con i salti avanti e indietro: le richieste "Range" passano)."""
    return await _da_jellyfin(request, f"/Audio/{id_brano}/stream?static=true",
                              ("content-type", "content-length", "content-range", "accept-ranges"))


@app.get("/api/musica/copertina/{id_voce}")
async def api_musica_copertina(request: Request, id_voce: str = FastPath(pattern=ID_JELLYFIN)):
    return await _da_jellyfin(request, f"/Items/{id_voce}/Images/Primary?maxWidth=400&quality=85",
                              ("content-type", "content-length"), cache="public, max-age=604800")


class RichiestaSeguiArtista(BaseModel):
    nome: str


@app.post("/api/musica/segui")
async def api_musica_segui(request: Request, dati: RichiestaSeguiArtista):
    """"Segui" un artista: Lidarr scarica i suoi album."""
    return {"messaggio": await servizi(request).musica.segui_per_nome(dati.nome.strip())}


@app.get("/api/musica/tendenze")
async def api_musica_tendenze(request: Request):
    return {"artisti": await servizi(request).musica.tendenze()}


class RichiestaSerie(BaseModel):
    titolo: str = ""
    nome: str = ""  # il titolo italiano (titolo è quello originale, per Sonarr)
    tvdb: int | None = None
    tmdb: int | None = None
    imdb: str = ""


@app.post("/api/serie/segui")
async def api_serie_segui(request: Request, dati: RichiestaSerie):
    """"Segui la serie": la aggiunge a Sonarr, che scarica tutte le puntate e poi quelle nuove."""
    if in_kids(request):
        return JSONResponse({"errore": "In modalità Kids non si aggiungono serie."}, status_code=403)
    s = servizi(request)
    risposta = await s.serie.segui(tvdb=dati.tvdb, tmdb=dati.tmdb, imdb=dati.imdb, titolo=dati.titolo)
    await asyncio.to_thread(ricorda_titoli, s.archivio, risposta.get("serie_id") or 0, [dati.nome, dati.titolo])
    return risposta


@app.get("/api/serie/{serie_id}/torrent")
async def api_serie_torrent(request: Request, serie_id: int, nome: str = "", originale: str = ""):
    """Le puntate da scaricare: cercate con il titolo italiano, quello originale e quelli di Sonarr."""
    s = servizi(request)
    await asyncio.to_thread(ricorda_titoli, s.archivio, serie_id, [nome, originale])
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


class RichiestaMusica(BaseModel):
    tipo: str = Field(pattern="^(brano|album)$")
    id: int


@app.post("/api/musica/scarica")
async def api_musica_scarica(request: Request, dati: RichiestaMusica):
    """Scarica la canzone completa (o l'album) dagli indexer e la mette nella Musica di Jellyfin."""
    s = servizi(request)
    return await (s.scarica_musica.brano(dati.id) if dati.tipo == "brano" else s.scarica_musica.album(dati.id))


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


@app.get("/api/libreria")
async def api_libreria(request: Request):
    return await servizi(request).libreria.elenco()


@app.get("/api/libreria/serie/{serie_id}")
async def api_libreria_serie(request: Request, serie_id: int):
    return {"stagioni": await servizi(request).libreria.puntate(serie_id)}


class RichiestaElimina(BaseModel):
    tipo: str = Field(pattern="^(film|serie|stagione|puntata|altro)$")
    id: int | None = None
    stagione: int | None = None
    file_id: int | None = None
    percorso: str = ""


@app.post("/api/libreria/elimina")
async def api_libreria_elimina(request: Request, dati: RichiestaElimina):
    if in_kids(request):
        return JSONResponse({"errore": "Da Kids non si elimina niente."}, status_code=403)
    lib = servizi(request).libreria
    mancano = JSONResponse({"errore": "Richiesta incompleta.", "cosa_fare": "Ricarica la pagina."}, status_code=400)
    if dati.tipo == "altro":
        return await lib.elimina_altro(dati.percorso) if dati.percorso else mancano
    if dati.id is None:
        return mancano
    if dati.tipo == "film":
        return await lib.elimina_film(dati.id)
    if dati.tipo == "serie":
        return await lib.elimina_serie(dati.id)
    if dati.tipo == "stagione":
        return await lib.elimina_stagione(dati.id, dati.stagione) if dati.stagione is not None else mancano
    return await lib.elimina_puntata(dati.id, dati.file_id) if dati.file_id is not None else mancano


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
    diretto = await _film_diretto(s, dati.tmdb_id)
    if diretto:
        return diretto
    film = await s.radarr.scarica_da_solo(dati.tmdb_id)
    gia = " (il file c'è già: cerco solo una versione migliore)" if film.get("hasFile") else ""
    return {"messaggio": f"Fatto! Radarr cerca la versione migliore, prima in italiano, e la mette in Jellyfin{gia}."}


async def _film_diretto(s, tmdb_id: int) -> dict | None:
    """I link diretti prima di tutto: se il film è su StreamingCommunity lo prendo da lì
    (se non riesce, il download passa da solo a Radarr). None: niente link diretto, ci pensa Radarr."""
    sito = s.siti_anime["streamingcommunity"]
    if not sito.attivo:
        return None
    try:
        nel_catalogo = await s.radarr._r("GET", "movie", params={"tmdbId": tmdb_id}) or []
        if any(f.get("hasFile") for f in nel_catalogo):
            return None  # c'è già: Radarr cerca solo una versione migliore, come sempre
        film = await s.tmdb.dettagli(tmdb_id)
        voce = await sito.trova_film(film)
    except ErroreServizio as exc:
        log.info("StreamingCommunity non disponibile per il film %s: %s", tmdb_id, exc.cosa)
        return None
    if not voce:
        return None
    for d in s.archivio.download_per_stato("in_corso", "completato", "importazione"):
        if d.get("tmdb_id") == tmdb_id:
            return {"messaggio": "Lo sto già scaricando: lo trovi nella pagina Download."}
    nome = nome_cartella(film["titolo"], film.get("anno", "")) + (" ITA" if voce["doppiato"] else "") + ".mkv"
    await s.download.da_diretto_film(film, [["streamingcommunity", id_puntata_sc(voce["id"])]], nome)
    lingua = "in italiano" if voce["doppiato"] else "sottotitolato in italiano"
    return {"messaggio": f"Fatto! Lo scarico {lingua} con un link diretto (StreamingCommunity) e lo metto in "
                         "Jellyfin. Se non riesce, passa da solo ai torrent."}


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
    try:
        torrent = await s.qbit.tutti()
        cerca = con_coda_qbit(cerca, torrent, {h.lower(): p for h, p in posizioni(torrent).items()})
    except Exception:  # noqa: BLE001 - senza qBittorrent si mostra lo stato salvato
        pass
    return {"gruppi": raggruppa(coda, attesa, cerca, s.automatici.preferita), "lingua_preferita": s.automatici.preferita}


class Prima(BaseModel):
    hash: str = Field(pattern=r"^[0-9a-fA-F]{40}$")


@app.post("/api/download/prima")
async def api_download_prima(request: Request, corpo: Prima):
    """Mette il torrent in cima alla coda: parte appena si libera un posto."""
    await servizi(request).ordine.scarica_prima(corpo.hash)
    return {"messaggio": "Fatto: questa parte per prima, appena finisce uno dei download in corso."}


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


@app.get("/api/download/{id_download}")
async def api_download_voce(request: Request, id_download: int):
    """Lo stato di un download solo: lo segue l'avviso della musica mentre la cerco."""
    voce = servizi(request).archivio.download(id_download)
    if not voce:
        raise ErroreServizio("Questo download non c'è più.", "Forse è stato annullato.")
    return {"id": voce["id"], "stato": voce["stato"], "messaggio": voce.get("messaggio") or "",
            "titolo": voce.get("titolo") or ""}


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
    # per cercare da soli le puntate nuove anche con il titolo italiano (puntate_italiane.py)
    await asyncio.to_thread(ricorda_titoli, s.archivio, serie["id"], [dati.nome, dati.titolo])
    if s.anime.attivo:
        # gli anime si collegano da soli ai siti diretti (AnimeUnity, AnimeWorld), preferiti ai torrent
        try:
            await asyncio.wait_for(s.anime.collega_da_solo(serie["id"], serie, await s.serie.episodi(serie["id"])), 20)
        except (TimeoutError, ErroreServizio):
            pass
    risposta = await _puntate_e_anime(s, serie["id"])
    anime = serie.get("seriesType") == "anime" or e_anime(serie)
    # la sezione dei link diretti: per gli anime, e per tutte le serie se c'è StreamingCommunity
    risposta["diretti"]["anime"] = anime or any(not s.siti_anime[f].solo_anime for f in s.anime.siti_attivi())
    return risposta


async def _puntate_e_anime(s, serie_id: int) -> dict:
    stagioni = con_download_cerca(await s.serie.puntate(serie_id), s.archivio.download_puntate(serie_id))
    stato = s.anime.leggi(serie_id)
    return {"serie_id": serie_id, "stagioni": stagioni,
            "diretti": {"attivo": s.anime.attivo, "collegamenti": s.anime.collegamenti(serie_id), "da": stato["da"],
                        "siti": [NOMI_FONTI[f] for f in s.anime.siti_attivi()]}}


@app.get("/api/serie/{serie_id}/puntate")
async def api_serie_puntate(request: Request, serie_id: int):
    return await _puntate_e_anime(servizi(request), serie_id)


class SceltaPuntate(BaseModel):
    puntate: list[int] = []
    stagione: int | None = None
    tutte: bool = False
    da_puntata: int | None = None  # id della puntata di Sonarr: da questa in avanti, anche le nuove


def _uscita(e: dict) -> bool:
    quando = e.get("airDateUtc") or ""
    return bool(quando) and quando[:19] <= time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


@app.post("/api/serie/{serie_id}/scarica")
async def api_serie_scarica_puntate(request: Request, serie_id: int, dati: SceltaPuntate):
    """Le puntate scelte: prima dai siti diretti (AnimeUnity, AnimeWorld: preferiti), il resto lo cerca Sonarr."""
    s = servizi(request)
    episodi = await s.serie.episodi(serie_id)
    da = None
    if dati.da_puntata is not None:
        scelta = next((e for e in episodi if e["id"] == dati.da_puntata), None)
        if scelta is None:
            raise ErroreServizio("Non trovo questa puntata in Sonarr.", "Chiudi e riapri la serie, poi riprova.")
        da = (scelta.get("seasonNumber", 0), scelta.get("episodeNumber", 0))
        volute = [e for e in episodi if (e.get("seasonNumber", 0), e.get("episodeNumber", 0)) >= da
                  and e.get("seasonNumber", 0) > 0]
    elif dati.tutte:
        da = (1, 0)
        volute = [e for e in episodi if e.get("seasonNumber", 0) > 0]
    elif dati.stagione is not None:
        volute = [e for e in episodi if e.get("seasonNumber") == dati.stagione]
    else:
        volute = [e for e in episodi if e["id"] in set(dati.puntate)]

    avviate: list[int] = []
    if s.anime.attivo:
        if da is not None:
            s.anime.segui_da(serie_id, da)
        try:
            esito = await s.anime.scarica(serie_id, solo=[e["id"] for e in volute])
            avviate = esito["avviate"]
        except ErroreServizio as exc:
            log.info("Siti diretti non disponibili: %s", exc.cosa)
    if not s.anime.collegamenti(serie_id):
        # nessuna fonte diretta: come sempre, ci pensa Sonarr
        if dati.da_puntata is not None:
            quante = await s.serie.scarica_da(serie_id, dati.da_puntata)
            return {"messaggio": f"Scarico da questa puntata in avanti ({quante} puntate), e le nuove appena escono."}
        if dati.tutte:
            await s.serie.scarica_tutte(serie_id)
            return {"messaggio": "Scarico tutte le puntate, e anche quelle nuove appena escono."}
        if dati.stagione is not None:
            await s.serie.scarica_tutte(serie_id, dati.stagione)
            return {"messaggio": f"Scarico la stagione {dati.stagione}."}
        await s.serie.scarica_puntate(dati.puntate)
        return {"messaggio": "Scarico la puntata." if len(dati.puntate) == 1 else f"Scarico {len(dati.puntate)} puntate."}

    gia = set(avviate) | s.archivio.puntate_in_download(serie_id)
    resto = [e["id"] for e in volute if e["id"] not in gia and not e.get("hasFile") and _uscita(e)]
    if resto:
        await s.serie.scarica_puntate(resto)
    parti = []
    if avviate:
        parti.append(f"{len(avviate)} con link diretto")
    if resto:
        parti.append(f"{len(resto)} le cerco come torrent (i siti diretti non le hanno)")
    if da is not None:
        parti.append("le nuove le scarico appena escono")
    if not parti:
        return {"messaggio": "Niente da scaricare: le puntate scelte ci sono già (o sono già in download)."}
    testo = "Scarico: " + "; ".join(parti) + "."
    return {"messaggio": testo}


@app.post("/api/serie/{serie_id}/annulla")
async def api_serie_annulla_puntate(request: Request, serie_id: int, dati: SceltaPuntate):
    s = servizi(request)
    puntate = dati.puntate
    if dati.stagione is not None or dati.tutte:
        stagioni = await s.serie.puntate(serie_id)
        puntate = [p["id"] for st in stagioni if dati.tutte or st["numero"] == dati.stagione for p in st["puntate"]]
    if dati.tutte:
        s.anime.segui_da(serie_id, None)
    await s.serie.annulla_puntate(puntate)
    # e i download diretti (AnimeUnity, AnimeWorld) di quelle puntate
    per_puntata = s.archivio.download_puntate(serie_id)
    for id_download in {per_puntata[p]["id"] for p in puntate if p in per_puntata}:
        await s.download.annulla(id_download)
    return {"messaggio": "Annullato: non le cerco e non le scarico più (quelle già in Jellyfin restano)."}


# --- Siti diretti degli anime: le voci collegate a una serie --------------------------

@app.get("/api/serie/{serie_id}/diretti/cerca")
async def api_diretti_cerca(request: Request, serie_id: int, q: str = ""):
    """Le voci di AnimeUnity e AnimeWorld per collegarle a mano, con la stagione che sembra giusta."""
    s = servizi(request)
    episodi = await s.serie.episodi(serie_id)
    q = q.strip() or (await s.serie.dati(serie_id)).get("title", "")
    voci = await s.anime.cerca(q)
    stagioni: dict[int, int] = {}
    for e in episodi:
        stagioni[e.get("seasonNumber", 0)] = stagioni.get(e.get("seasonNumber", 0), 0) + 1
    assoluta = any(e.get("absoluteEpisodeNumber") for e in episodi)
    voci = ordina_risultati(voci, [q], s.anime.italiano)[:24]
    voci = [v for v in voci if v["tipo"] != "Movie"]
    return {"risultati": [{**v, "nome_fonte": NOMI_FONTI.get(v["fonte"], v["fonte"]),
                           "stagione": TUTTE_LE_STAGIONI if v.get("stagioni_proprie")
                           else indovina_stagione(v, voci, stagioni, assoluta)} for v in voci],
            "stagioni": sorted(n for n in stagioni if n > 0), "assoluta": assoluta}


class VoceDiretta(BaseModel):
    fonte: str = Field(pattern="^(animeunity|animeworld|streamingcommunity)$")
    id: int | str
    slug: str = ""
    titolo: str = ""
    doppiato: bool = False
    immagine: str = ""
    anno: str = ""
    puntate: int = 0
    stagione: int = 1


@app.post("/api/serie/{serie_id}/diretti/collega")
async def api_diretti_collega(request: Request, serie_id: int, voce: VoceDiretta):
    s = servizi(request)
    s.anime.collega(serie_id, voce.model_dump(), voce.stagione)
    esito = await s.anime.scarica(serie_id)
    quante = len(esito["avviate"])
    extra = f" Ho messo in coda {quante} puntate." if quante else ""
    sito = NOMI_FONTI[voce.fonte]
    return {"messaggio": f"Collegato \"{voce.titolo}\" ({sito}): da ora quelle puntate arrivano prima da lì.{extra}"}


class ScollegaDiretta(BaseModel):
    fonte: str = "animeunity"
    id: int | str


@app.post("/api/serie/{serie_id}/diretti/scollega")
async def api_diretti_scollega(request: Request, serie_id: int, dati: ScollegaDiretta):
    servizi(request).anime.scollega(serie_id, dati.fonte, dati.id)
    return {"messaggio": "Scollegato: quelle puntate le prende un altro sito diretto, o le cerca Sonarr."}


@app.post("/api/download/{id_download}/associa")
async def api_associa(request: Request, id_download: int, dati: RichiestaAssocia):
    s = servizi(request)
    film = await s.tmdb.dettagli(dati.tmdb_id)
    await s.download.associa(id_download, film)
    return {"messaggio": f"Associato a {film['titolo']}."}


@app.post("/api/download/{id_download}/riprova")
async def api_riprova(request: Request, id_download: int):
    s = servizi(request)
    voce = s.archivio.download(id_download)
    # la musica rimasta senza torrent (non trovata, o ricerca interrotta): si rifà la ricerca
    if voce and voce.get("categoria") == "musica" and voce["stato"] == "errore" and await s.scarica_musica.riprova(voce):
        return {"messaggio": "Lo cerco di nuovo sui tuoi indexer."}
    await s.download.riprova(id_download)
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
        # l'utente lo vede già adesso nella pagina: niente notifica sul telefono dopo
        s.archivio.avvisami_imposta(tmdb_id, time.time(), provider["gratis"], True)
        return {"attivo": True, "messaggio": "È già gratis adesso! Lo trovi in evidenza nella pagina iniziale."}
    return {"attivo": True, "messaggio": "Ok! Controllo ogni giorno e ti avviso quando diventa gratis."}
