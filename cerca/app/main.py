"""Cerca: ricerca unificata dei film per mediabox (TMDB + Internet Archive + Prowlarr)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .archive import InternetArchive
from .avvisami import Avvisami
from .config import Config
from .db import Archivio
from .download import GestoreDownload, e_magnet, nome_da_magnet
from .jellyfin import Jellyfin
from .prowlarr import Prowlarr
from .qbittorrent import QBittorrent
from .radarr import Radarr
from .rete import ErroreServizio, nuovo_client
from .tmdb import TMDB

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cerca")

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
        self.tmdb = TMDB(config, self.archivio, client)
        self.archive = InternetArchive(self.archivio, client)
        self.prowlarr = Prowlarr(config, self.archivio, client)
        self.qbit = QBittorrent(config, client)
        self.radarr = Radarr(config, client)
        self.jellyfin = Jellyfin(config, client)
        self.download = GestoreDownload(
            config, self.archivio, client, self.qbit, self.radarr, self.tmdb, self.jellyfin
        )
        self.avvisami = Avvisami(config, self.archivio, self.tmdb, client)


@asynccontextmanager
async def ciclo_di_vita(app: FastAPI):
    config = Config()
    async with nuovo_client() as client:
        servizi = Servizi(config, client)
        app.state.servizi = servizi
        compiti = [
            asyncio.create_task(servizi.download.ciclo()),
            asyncio.create_task(servizi.avvisami.ciclo()),
        ]
        log.info("Cerca è pronto")
        yield
        for c in compiti:
            c.cancel()
        for c in compiti:
            with contextlib.suppress(asyncio.CancelledError):
                await c


app = FastAPI(title="Cerca", lifespan=ciclo_di_vita, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=CARTELLA / "static"), name="static")
modelli = Jinja2Templates(directory=CARTELLA / "templates")


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


def pagina(request: Request, nome: str, **contesto) -> HTMLResponse:
    s = servizi(request)
    contesto.setdefault("errore", None)
    return modelli.TemplateResponse(
        request, nome, {"base": s.config.root_path, "ntfy": bool(s.config.ntfy_topic), **contesto}
    )


@app.exception_handler(ErroreServizio)
async def gestisci_errore(request: Request, exc: ErroreServizio):
    return JSONResponse(exc.come_dizionario(), status_code=502)


# --- pagine ----------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    s = servizi(request)
    avvisi = s.archivio.avvisami_elenco()
    return pagina(
        request,
        "home.html",
        ricerche=s.archivio.ultime_ricerche(8),
        gratis=[a for a in avvisi if a["gratis_dal"]],
        in_attesa=[a for a in avvisi if not a["gratis_dal"]],
        scaricati=[d for d in s.archivio.elenco_download(12) if d["stato"] == "importato"][:6],
    )


@app.get("/cerca", response_class=HTMLResponse)
async def risultati(request: Request, q: str = ""):
    s = servizi(request)
    q = q.strip()
    if not q:
        return pagina(request, "risultati.html", q="", film=[])
    try:
        film = await s.tmdb.cerca(q)
        s.archivio.aggiungi_ricerca(q)
    except ErroreServizio as exc:
        return pagina(request, "risultati.html", q=q, film=[], errore=exc)
    return pagina(request, "risultati.html", q=q, film=film)


@app.get("/film/{tmdb_id}", response_class=HTMLResponse)
async def scheda_film(request: Request, tmdb_id: int):
    s = servizi(request)
    try:
        film = await s.tmdb.dettagli(tmdb_id)
    except ErroreServizio as exc:
        return pagina(request, "film.html", film=None, errore=exc)
    return pagina(request, "film.html", film=film, avvisami=s.archivio.avvisami_presente(tmdb_id))


@app.get("/torrent", response_class=HTMLResponse)
async def pagina_torrent(request: Request):
    return pagina(request, "torrent.html")


@app.get("/download", response_class=HTMLResponse)
async def pagina_download(request: Request):
    return pagina(request, "download.html")


@app.get("/salute")
async def salute():
    return {"stato": "ok"}


# --- API usate dalle pagine ----------------------------------------------------------

@app.get("/api/tmdb")
async def api_tmdb(request: Request, q: str):
    return {"film": await servizi(request).tmdb.cerca(q)}


@app.get("/api/novita")
async def api_novita(request: Request):
    """Film di tendenza e nuove uscite: se una delle due fallisce, l'altra si vede lo stesso."""
    s = servizi(request)
    tendenze, cinema = await asyncio.gather(s.tmdb.tendenze(), s.tmdb.al_cinema(), return_exceptions=True)
    risposta: dict = {"tendenze": [], "cinema": []}
    for chiave, valore in (("tendenze", tendenze), ("cinema", cinema)):
        if isinstance(valore, ErroreServizio):
            risposta["errore"] = valore.come_dizionario()
        elif isinstance(valore, list):
            risposta[chiave] = valore
    return risposta


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


class RichiestaProwlarr(BaseModel):
    tmdb_id: int
    id: str


@app.post("/api/scarica/prowlarr")
async def api_scarica_prowlarr(request: Request, dati: RichiestaProwlarr):
    s = servizi(request)
    film = await s.tmdb.dettagli(dati.tmdb_id)
    originale = s.prowlarr.risultato_originale(dati.tmdb_id, dati.id)
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
