"""Chi guarda: ogni persona entra con il suo utente di Jellyfin, l'amministratore solo per configurare."""

import asyncio
import dataclasses
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from app import accesso as modulo_accesso
from app.accesso import COOKIE, Accesso, destinazione_sicura, iniziali, percorso_libero
from app.config import Config
from app.jellyfin import Jellyfin
from app.main import app

ADMIN = "a" * 32
MARIO = "b" * 32
ANNA = "c" * 32
UTENTI = [
    {"Id": ADMIN, "Name": "admin", "HasPassword": True, "Policy": {"IsAdministrator": True}},
    {"Id": MARIO, "Name": "Mario", "HasPassword": False, "Policy": {"IsAdministrator": False}},
    {"Id": ANNA, "Name": "Anna", "HasPassword": True, "Policy": {"IsAdministrator": False}},
    {"Id": "d" * 32, "Name": "Vecchio", "Policy": {"IsDisabled": True}},
]
PASSWORD = {"admin": "segreta1", "Mario": "", "Anna": "anna1234"}


def jellyfin_finto(richieste, spento=False):
    def gestore(r: httpx.Request) -> httpx.Response:
        richieste.append(r)
        if spento:
            raise httpx.ConnectError("spento")
        if r.url.path == "/Users/AuthenticateByName":
            import json
            corpo = json.loads(r.content)
            if PASSWORD.get(corpo["Username"]) != corpo["Pw"]:
                return httpx.Response(401)
            u = next(x for x in UTENTI if x["Name"] == corpo["Username"])
            return httpx.Response(200, json={"AccessToken": "t", "User": u})
        if r.url.path == "/Users":
            return httpx.Response(200, json=UTENTI)
        if r.url.path.startswith("/Users/") and r.url.path.endswith("/Items/Resume"):
            return httpx.Response(200, json={"Items": []})
        return httpx.Response(404)
    return gestore


def servizi_finti(tmp_path, richieste, spento=False):
    config = dataclasses.replace(Config(), jellyfin_url="http://jellyfin:8096", password="segreta1")
    client = httpx.AsyncClient(transport=httpx.MockTransport(jellyfin_finto(richieste, spento)))
    archivio = SimpleNamespace(elenco_download=lambda n: [])
    return SimpleNamespace(config=config, accesso=Accesso(tmp_path), jellyfin=Jellyfin(config, client),
                           archivio=archivio, disco=SimpleNamespace(stato=lambda: {}),
                           foto_profili=SimpleNamespace(in_sottofondo=lambda: None))


def cliente(tmp_path, richieste=None, spento=False):
    app.state.servizi = servizi_finti(tmp_path, [] if richieste is None else richieste, spento)
    return TestClient(app, follow_redirects=False)


def test_cookie_firmato(tmp_path):
    a = Accesso(tmp_path)
    valore = a.cookie({"id": MARIO, "nome": "Mario", "admin": False})
    assert a.leggi(valore) == {"id": MARIO, "nome": "Mario", "admin": False}
    # chi prova a diventare amministratore cambiando il cookie viene respinto
    dati, firma = valore.rsplit(".", 1)
    falso = Accesso(tmp_path / "altro").cookie({"id": MARIO, "nome": "Mario", "admin": True})
    assert a.leggi(falso) is None
    assert a.leggi(falso.rsplit(".", 1)[0] + "." + firma) is None
    assert a.leggi("") is None and a.leggi("niente") is None
    # il segreto resta lo stesso dopo un riavvio
    assert Accesso(tmp_path).leggi(valore) is not None


def test_iniziali_e_percorsi():
    assert iniziali("Mario Rossi") == "MR" and iniziali("anna") == "A" and iniziali("") == "?"
    assert percorso_libero("/chi-guarda") and percorso_libero("/static/style.css") and percorso_libero("/salute")
    assert not percorso_libero("/") and not percorso_libero("/api/home") and not percorso_libero("/chi-guardaX")
    assert destinazione_sicura("/serie?x=1") == "/serie?x=1"
    assert destinazione_sicura("//sito.com") == "/" and destinazione_sicura("https://sito.com") == "/"
    assert destinazione_sicura("/chi-guarda") == "/"


def test_senza_utente_si_sceglie_chi_guarda(tmp_path):
    c = cliente(tmp_path)
    r = c.get("/serie")
    assert r.status_code == 303 and r.headers["location"] == "/chi-guarda?dopo=%2Fserie"
    r = c.get("/api/home")
    assert r.status_code == 401 and r.json()["accesso"] is True
    assert c.get("/salute").status_code == 200


def test_pagina_chi_guarda_mostra_solo_gli_utenti_di_casa(tmp_path):
    r = cliente(tmp_path).get("/chi-guarda")
    assert r.status_code == 200
    assert "Mario" in r.text and "Anna" in r.text and "Vecchio" not in r.text
    # l'amministratore non è una faccia: si entra dal riquadro in basso, con la password
    assert r.text.count('class="faccia"') == 2
    assert "Entra come amministratore" in r.text
    assert "Pannello" not in r.text


def test_utente_senza_password_entra_e_non_vede_il_pannello(tmp_path):
    c = cliente(tmp_path)
    r = c.post("/chi-guarda", data={"id": MARIO, "dopo": "/serie"})
    assert r.status_code == 303 and r.headers["location"] == "/serie"
    utente = app.state.servizi.accesso.leggi(r.cookies[COOKIE])
    assert utente == {"id": MARIO, "nome": "Mario", "admin": False}
    c.cookies.set(COOKIE, r.cookies[COOKIE])
    pagina = c.get("/chi-guarda").text
    assert ":8081" not in pagina


def test_utenti_di_casa_sempre_senza_password_solo_admin_la_chiede(tmp_path):
    c = cliente(tmp_path)
    # Anna in Jellyfin ha una password, ma in Cerca gli utenti di casa entrano senza
    assert "Password di Anna" not in c.get("/chi-guarda").text
    r = c.post("/chi-guarda", data={"id": ANNA})
    assert r.status_code == 303
    assert app.state.servizi.accesso.leggi(r.cookies[COOKIE])["nome"] == "Anna"
    # l'amministratore invece sì
    r = c.post("/chi-guarda", data={"id": ADMIN, "password": "no"})
    assert r.status_code == 401 and "Password sbagliata" in r.text and COOKIE not in r.cookies
    r = c.post("/chi-guarda", data={"id": ADMIN, "password": "no"}, headers={"X-Cerca": "accesso"})
    assert r.status_code == 401 and r.json()["errore"].startswith("Password sbagliata")
    r = c.post("/chi-guarda", data={"id": MARIO}, headers={"X-Cerca": "accesso"})
    assert r.json() == {"vai": "/"} and COOKIE in r.cookies


def test_admin_vede_il_pannello(tmp_path):
    c = cliente(tmp_path)
    r = c.post("/chi-guarda", data={"id": ADMIN, "password": "segreta1"})
    utente = app.state.servizi.accesso.leggi(r.cookies[COOKIE])
    assert utente["admin"] is True
    from app.main import pagina
    richiesta = SimpleNamespace(state=SimpleNamespace(utente=utente), url=SimpleNamespace(hostname="mediabox.local"),
                                cookies={}, app=app)
    contesto = {}

    def finto(request, nome, ctx):
        contesto.update(ctx)
        return SimpleNamespace()
    vero = modulo_main().modelli.TemplateResponse
    modulo_main().modelli.TemplateResponse = finto
    try:
        pagina(richiesta, "home.html")
    finally:
        modulo_main().modelli.TemplateResponse = vero
    assert contesto["pannello"] == "//mediabox.local:8081/"
    richiesta.state.utente = {"id": MARIO, "nome": "Mario", "admin": False}
    modulo_main().modelli.TemplateResponse = finto
    try:
        pagina(richiesta, "home.html")
    finally:
        modulo_main().modelli.TemplateResponse = vero
    assert contesto["pannello"] == ""


def modulo_main():
    import app.main
    return app.main


def test_admin_entra_anche_con_jellyfin_spento(tmp_path):
    c = cliente(tmp_path, spento=True)
    assert "Entra come amministratore" in c.get("/chi-guarda").text
    assert c.post("/chi-guarda", data={"nome": "admin", "password": "sbagliata"}).status_code == 503
    r = c.post("/chi-guarda", data={"nome": "admin", "password": "segreta1"})
    assert r.status_code == 303 and app.state.servizi.accesso.leggi(r.cookies[COOKIE])["admin"] is True


def test_continua_a_guardare_segue_chi_guarda(tmp_path):
    richieste = []
    s = servizi_finti(tmp_path, richieste)
    segno = modulo_accesso.utente_corrente.set({"id": ANNA, "nome": "Anna", "admin": False})
    try:
        asyncio.run(s.jellyfin.continua("movie"))
    finally:
        modulo_accesso.utente_corrente.reset(segno)
    asyncio.run(s.jellyfin.continua("movie"))
    percorsi = [r.url.path for r in richieste if r.url.path.endswith("/Items/Resume")]
    # prima Anna, poi senza nessuno l'utente di Cerca (l'amministratore)
    assert percorsi == [f"/Users/{ANNA}/Items/Resume", f"/Users/{ADMIN}/Items/Resume"]


def test_niente_giri_infiniti_con_il_root_path_di_uvicorn(tmp_path):
    """Nel container uvicorn parte con --root-path /cerca: "Chi guarda?" deve aprirsi lo stesso."""
    app.state.servizi = servizi_finti(tmp_path, [])
    c = TestClient(app, follow_redirects=False, root_path="/cerca")
    r = c.get("/chi-guarda")
    assert r.status_code == 200 and "Chi guarda?" in r.text
    r = c.get("/serie")
    assert r.status_code == 303 and r.headers["location"] == "/chi-guarda?dopo=%2Fserie"
    assert c.get("/static/style.css").status_code not in (303, 401)  # gli statici non chiedono chi guarda
    assert c.get("/api/home").status_code == 401
