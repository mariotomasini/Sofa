"""Configurazione di mediabox dal browser: procedura guidata, avanzamento e impostazioni.

Durante l'installazione gira da solo sulla porta del sito (l'installer lo avvia
prima di tutto il resto); dopo è il servizio "impostazioni", dietro Caddy su
/impostazioni. Le pagine sono le stesse.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..impostazioni import CARTELLA_MEDIABOX
from . import procedura as p

BASE = "/impostazioni"
COOKIE = "mediabox_impostazioni"
QUI = Path(__file__).parent
TMDB_API = os.environ.get("TMDB_API_URL", "https://api.themoviedb.org/3")


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


def crea_app(cartella: Path = CARTELLA_MEDIABOX) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount(f"{BASE}/static", StaticFiles(directory=QUI / "static"), name="static")
    modelli = Jinja2Templates(directory=QUI / "templates")
    modelli.env.globals["v"] = impronta_statici(QUI / "static")

    @app.middleware("http")
    async def intestazioni_cache(request: Request, call_next):
        risposta = await call_next(request)
        if "cache-control" not in risposta.headers:
            risposta.headers["Cache-Control"] = regole_cache(request.url.path, "v" in request.query_params)
        return risposta

    @app.exception_handler(PermissionError)
    async def senza_permessi(request: Request, exc: PermissionError):
        testo = ("Non ho il permesso di leggere o scrivere i file di mediabox. "
                 "Rilancia l'installer sul PC: rimette a posto i permessi.")
        if request.url.path.startswith(f"{BASE}/api/"):
            return JSONResponse({"errore": testo}, status_code=500)
        return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>mediabox</title><p>{testo}</p>", status_code=500)

    def connesso(request: Request) -> bool:
        return p.gettone_valido(cartella, request.cookies.get(COOKIE))

    def con_cookie(risposta: JSONResponse) -> JSONResponse:
        risposta.set_cookie(COOKIE, p.gettone(cartella), max_age=30 * 86400, httponly=True,
                            samesite="strict", path=BASE)
        return risposta

    def errore(exc: p.DatiNonValidi, stato: int = 400) -> JSONResponse:
        return JSONResponse({"errore": str(exc), "campo": exc.campo}, status_code=stato)

    def stato_pagina(request: Request) -> dict:
        v = p.valori(cartella)
        a = p.avanzamento(cartella)
        stato = {
            "configurato": p.configurato(cartella),
            "connesso": connesso(request),
            "applicazione": a.__dict__,
            "indirizzi": p.indirizzi(v),
            "windows": p.windows(v),
        }
        if not stato["configurato"]:
            stato["scelte"] = p.scelte_attuali(v)
            stato["dischi"] = p.dischi(cartella, v)
            stato["ntfy_suggerito"] = p.suggerisci_ntfy()
        elif stato["connesso"]:
            stato["scelte"] = p.scelte_attuali(v)
            stato["dischi"] = p.dischi(cartella, v)
        return stato

    @app.get("/", include_in_schema=False)
    async def radice():
        return RedirectResponse(f"{BASE}/", status_code=303)

    @app.get(BASE, include_in_schema=False)
    async def senza_barra():
        return RedirectResponse(f"{BASE}/", status_code=303)

    @app.get(f"{BASE}/", response_class=HTMLResponse)
    async def pagina(request: Request):
        return modelli.TemplateResponse(request, "pagina.html", {"base": BASE, "stato": stato_pagina(request)})

    @app.get(f"{BASE}/salute")
    async def salute():
        return {"stato": "ok"}

    @app.get(f"{BASE}/api/stato")
    async def api_stato(request: Request):
        return stato_pagina(request)

    @app.post(f"{BASE}/api/verifica-tmdb")
    async def verifica_tmdb(request: Request):
        chiave = "".join(str((await request.json()).get("chiave") or "").split())
        if not chiave:
            return {"ok": False, "messaggio": "Incolla la chiave di TMDB."}
        if len(chiave) > 40:  # il token lungo va nell'intestazione, la chiave corta nei parametri
            intestazioni, parametri = {"Authorization": f"Bearer {chiave}"}, {}
        else:
            intestazioni, parametri = {}, {"api_key": chiave}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{TMDB_API}/configuration", headers=intestazioni, params=parametri)
        except httpx.HTTPError:
            return {"ok": False, "sconosciuto": True,
                    "messaggio": "Non riesco a raggiungere TMDB (manca Internet?). Puoi andare avanti lo stesso."}
        if r.status_code == 200:
            return {"ok": True, "messaggio": "Chiave valida."}
        return {"ok": False, "messaggio": "TMDB non accetta questa chiave. Controlla di averla copiata tutta."}

    @app.post(f"{BASE}/api/prova-notifica")
    async def prova_notifica(request: Request):
        argomento = p.pulisci_ntfy((await request.json()).get("argomento", ""))
        if not argomento:
            return JSONResponse({"errore": "Scrivi prima il nome delle notifiche."}, status_code=400)
        server = p.valori(cartella).get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(f"{server}/{argomento}", content=b"Funziona! Qui arriveranno gli avvisi di mediabox.",
                                      headers={"Title": "mediabox", "Tags": "tv"})
            r.raise_for_status()
        except httpx.HTTPError:
            return JSONResponse({"errore": "Non riesco a mandare la notifica: controlla la connessione a Internet."},
                                status_code=502)
        return {"messaggio": "Notifica mandata: dovrebbe arrivarti sul telefono tra pochi secondi."}

    @app.post(f"{BASE}/api/installa")
    async def installa(request: Request):
        try:
            p.salva_installazione(cartella, await request.json())
        except p.DatiNonValidi as exc:
            return errore(exc, 409 if not exc.campo else 400)
        return con_cookie(JSONResponse({"ok": True}))

    @app.post(f"{BASE}/api/accedi")
    async def accedi(request: Request):
        if not p.password_giusta(cartella, str((await request.json()).get("password") or "")):
            return JSONResponse({"errore": "Password sbagliata. È quella scelta durante l'installazione."}, status_code=401)
        return con_cookie(JSONResponse({"ok": True}))

    @app.post(f"{BASE}/api/esci")
    async def esci():
        risposta = JSONResponse({"ok": True})
        risposta.delete_cookie(COOKIE, path=BASE)
        return risposta

    @app.post(f"{BASE}/api/impostazioni")
    async def salva(request: Request):
        if not connesso(request):
            return JSONResponse({"errore": "Accedi di nuovo con la password di amministrazione."}, status_code=401)
        try:
            cambiato = p.salva_impostazioni(cartella, await request.json())
        except p.DatiNonValidi as exc:
            return errore(exc)
        if not cambiato:
            return {"ok": True, "cambiato": False, "messaggio": "Non hai cambiato nulla."}
        return {"ok": True, "cambiato": True}

    # --- aggiornamenti ---------------------------------------------------------------------------
    def non_connesso() -> JSONResponse:
        return JSONResponse({"errore": "Accedi di nuovo con la password di amministrazione."}, status_code=401)

    @app.get(f"{BASE}/api/aggiornamento")
    async def stato_aggiornamento(request: Request):
        if not connesso(request):
            return non_connesso()
        return p.stato_aggiornamenti(cartella)

    @app.post(f"{BASE}/api/aggiornamento/controlla")
    async def controlla_aggiornamento(request: Request):
        if not connesso(request):
            return non_connesso()
        from starlette.concurrency import run_in_threadpool

        from .. import aggiornamenti

        return await run_in_threadpool(aggiornamenti.controlla, cartella)

    @app.post(f"{BASE}/api/aggiornamento/installa")
    async def installa_aggiornamento(request: Request):
        if not connesso(request):
            return non_connesso()
        if p.stato_aggiornamenti(cartella)["in_corso"]:
            return {"ok": True, "messaggio": "L'aggiornamento è già in corso."}
        p.richiedi_aggiornamento(cartella)
        return {"ok": True}

    return app
