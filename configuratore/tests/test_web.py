"""Test della configurazione dal browser."""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from configuratore.impostazioni import leggi_env
from configuratore.web import procedura as p
from configuratore.web.app import crea_app

RADICE = Path(__file__).resolve().parents[2]


@pytest.fixture
def cartella(tmp_path):
    shutil.copy(RADICE / ".env.example", tmp_path / ".env.example")
    shutil.copy(RADICE / ".env.example", tmp_path / ".env")
    (tmp_path / "dischi.json").write_text(json.dumps([
        {"percorso": "/srv/mediabox/dati", "nome": "Disco del sistema", "libero_gb": 80, "totale_gb": 240},
        {"percorso": "/mnt/grande/mediabox", "nome": "Disco /mnt/grande", "libero_gb": 1800, "totale_gb": 2000,
         "consigliato": True},
    ]))
    return tmp_path


def dati_validi(**altri):
    return {"tmdb": "chiave-corta-123", "password": "unaPassword9", "password2": "unaPassword9",
            "voci": {"WITH_SONARR": False, "WITH_ANIME": True, "WITH_BAZARR": True}, "lingue": "it, en,ja",
            "ntfy": "mediabox-casa!7f", "cartella": "/mnt/grande/mediabox", **altri}


def test_prima_installazione_scrive_env_e_segnale(cartella):
    p.salva_installazione(cartella, dati_validi())
    env = leggi_env(cartella / ".env")
    assert env["TMDB_API_KEY"] == "chiave-corta-123"
    assert env["ADMIN_PASSWORD"] == "unaPassword9"
    assert env["WITH_ANIME"] == "si" and env["WITH_SONARR"] == "si"  # gli anime accendono Sonarr
    assert env["SONARR_API_KEY"]  # chiave generata
    assert env["LINGUE_SOTTOTITOLI"] == "it,en,ja"
    assert env["NTFY_TOPIC"] == "mediabox-casa7f"
    assert env["MEDIA_DIR"] == "/mnt/grande/mediabox"
    assert len(env["IMPOSTAZIONI_SEGRETO"]) == 64
    assert (cartella / p.FLAG_PRONTA).exists()
    assert oct((cartella / ".env").stat().st_mode)[-3:] == "600"
    # i commenti del file restano
    assert "# Chiave API di TMDB" in (cartella / ".env").read_text()
    with pytest.raises(p.DatiNonValidi):
        p.salva_installazione(cartella, dati_validi())  # già configurato


@pytest.mark.parametrize(("modifica", "campo"), [
    ({"password": "corta", "password2": "corta"}, "password"),
    ({"password2": "diversa123"}, "password2"),
    ({"password": 'con"virgolette', "password2": 'con"virgolette'}, "password"),
    ({"cartella": "relativa/film"}, "cartella"),
    ({"lingue": "italiano"}, "lingue"),
])
def test_dati_sbagliati(cartella, modifica, campo):
    with pytest.raises(p.DatiNonValidi) as exc:
        p.salva_installazione(cartella, dati_validi(**modifica))
    assert exc.value.campo == campo
    assert not (cartella / p.FLAG_PRONTA).exists()


def test_dischi_e_consigliato(cartella):
    d = p.dischi(cartella, p.valori(cartella))
    assert [x["percorso"] for x in d] == ["/srv/mediabox/dati", "/mnt/grande/mediabox"]
    assert [x["consigliato"] for x in d] == [False, True]
    (cartella / "dischi.json").write_text("rotto")
    assert p.dischi(cartella, p.valori(cartella))[0]["consigliato"]


LOG = """
== Scarico i programmi (la prima volta può richiedere diversi minuti) ==
  · Scarico jellyfin:12.4 (3 di 12)
== Avvio i servizi ==
Container mediabox-beszel-1 Removed
\x1b[32m  ✔ Servizi avviati\x1b[0m

== Configuro qBittorrent ==
  ✔ Upload limitato
== Configuro Bazarr ==
  ✘ Bazarr non risponde.
    Cosa fare: Aspetta un minuto e riprova.
== Configuro Jellyfin ==
  · Creo le librerie
== Indirizzi di mediabox ==
  Apri http://mediabox.local
"""


def test_lettura_del_registro():
    a = p.leggi_log(LOG)
    assert [x["testo"] for x in a.passi] == [
        "Scarico i programmi", "Avvio i servizi", "Configuro qBittorrent", "Configuro Bazarr", "Configuro Jellyfin"]
    assert [x["stato"] for x in a.passi] == ["fatto", "fatto", "fatto", "errore", "in_corso"]
    assert a.scarico is None  # i download sono finiti
    assert p.leggi_log("== Scarico i programmi ==\n  · Scarico jellyfin:12.4 (3 di 12)\n").scarico == {
        "nome": "jellyfin:12.4", "n": 3, "di": 12}
    assert a.problemi == [
        {"nome": "Configuro Bazarr", "cosa": "Bazarr non risponde.", "cosa_fare": "Aspetta un minuto e riprova."}]
    assert a.ultima_riga == "Creo le librerie"


def test_avanzamento_della_configurazione():
    a = p.leggi_log(
        "== Scarico i programmi ==\n  · Scarico radarr:7 (12 di 12)\n== Avvio i servizi ==\n"
        "== Configuro qBittorrent (1 di 9) ==\n  √ Pronto\n== Configuro Radarr (2 di 9) ==\n"
        "  · Attendo che Radarr sia pronto… 1:40 (al massimo 10:00)\n"
    )
    assert a.configurazione == {"nome": "Radarr", "n": 2, "di": 9}
    assert a.scarico is None
    assert [x["testo"] for x in a.passi][-1] == "Configuro Radarr"
    assert a.ultima_riga == "Attendo che Radarr sia pronto… 1:40 (al massimo 10:00)"


def test_registro_di_windows():
    a = p.leggi_log("== Configuro Bazarr ==\n  √ Pronto\n== Configuro Jellyfin ==\n  × Jellyfin non risponde.\n")
    assert [x["stato"] for x in a.passi] == ["fatto", "errore"]
    assert a.problemi[0]["cosa"] == "Jellyfin non risponde."


def test_avanzamento_finito_legge_esito(cartella):
    (cartella / p.LOG).write_text(LOG)
    (cartella / p.STATO).write_text("fatto\n")
    (cartella / "generato").mkdir()
    (cartella / p.ESITO).write_text(json.dumps({"falliti": [], "da_fare": ["Aggiungi gli indexer"], "note": []}))
    a = p.avanzamento(cartella)
    assert a.stato == "fatto"
    assert all(x["stato"] in ("fatto", "errore") for x in a.passi)
    assert a.problemi == [] and a.da_fare == ["Aggiungi gli indexer"]


def test_pagina_api_e_accesso(cartella):
    client = TestClient(crea_app(cartella))
    r = client.get("/impostazioni/")
    assert r.status_code == 200 and "Configuriamo mediabox" in r.text
    stato = client.get("/impostazioni/api/stato").json()
    assert not stato["configurato"] and stato["scelte"]["voci"] and stato["dischi"]
    assert stato["ntfy_suggerito"].startswith("mediabox-")

    r = client.post("/impostazioni/api/installa", json=dati_validi(password2="altra"))
    assert r.status_code == 400 and r.json()["campo"] == "password2"
    r = client.post("/impostazioni/api/installa", json=dati_validi())
    assert r.status_code == 200
    stato = client.get("/impostazioni/api/stato").json()
    assert stato["configurato"] and stato["connesso"]
    assert stato["applicazione"]["stato"] == "attesa"
    assert "TMDB" not in json.dumps(stato.get("scelte", {})).upper().replace("TMDB_IMPOSTATA", "")

    # un altro browser: deve accedere con la password
    altro = TestClient(crea_app(cartella))
    assert not altro.get("/impostazioni/api/stato").json()["connesso"]
    assert altro.post("/impostazioni/api/impostazioni", json={}).status_code == 401
    assert altro.post("/impostazioni/api/accedi", json={"password": "sbagliata"}).status_code == 401
    assert altro.post("/impostazioni/api/accedi", json={"password": "unaPassword9"}).status_code == 200
    assert altro.get("/impostazioni/api/stato").json()["connesso"]

    # salvataggio dopo l'installazione: segnale per lo script, con la cartella di prima
    (cartella / p.FLAG_PRONTA).unlink()
    r = altro.post("/impostazioni/api/impostazioni", json={"voci": {"WITH_LIDARR": True}, "cartella": "/mnt/altro"})
    assert r.json()["cambiato"]
    assert (cartella / p.FLAG_APPLICA).read_text() == "/mnt/grande/mediabox"
    env = leggi_env(cartella / ".env")
    assert env["WITH_LIDARR"] == "si" and env["LIDARR_API_KEY"] and env["MEDIA_DIR"] == "/mnt/altro"
    assert env["WITH_ANIME"] == "si"  # le voci non mandate restano come sono
    (cartella / p.FLAG_APPLICA).unlink()
    assert not altro.post("/impostazioni/api/impostazioni", json={"voci": {"WITH_LIDARR": True}}).json()["cambiato"]

    # cambiando la password il vecchio accesso non vale più
    p.aggiorna_env(cartella / ".env", {"ADMIN_PASSWORD": "nuovaPassword1"})
    assert not altro.get("/impostazioni/api/stato").json()["connesso"]


def test_verifica_tmdb_senza_rete(cartella, monkeypatch):
    monkeypatch.setattr("configuratore.web.app.TMDB_API", "http://127.0.0.1:9")
    client = TestClient(crea_app(cartella))
    r = client.post("/impostazioni/api/verifica-tmdb", json={"chiave": "abc"}).json()
    assert not r["ok"] and r["sconosciuto"]



def test_chiave_tmdb_facoltativa(cartella):
    # la procedura non la chiede e mediabox è configurato lo stesso
    p.salva_installazione(cartella, dati_validi(tmdb=""))
    assert leggi_env(cartella / ".env")["TMDB_API_KEY"] == ""
    assert p.configurato(cartella)
    (cartella / p.FLAG_PRONTA).unlink()
    # dalle impostazioni si aggiunge e si toglie
    assert p.salva_impostazioni(cartella, {"tmdb": " la-mia-chiave "})
    assert leggi_env(cartella / ".env")["TMDB_API_KEY"] == "la-mia-chiave"
    assert p.salva_impostazioni(cartella, {"tmdb_togli": True})
    assert leggi_env(cartella / ".env")["TMDB_API_KEY"] == ""


def test_modalita_leggera(cartella):
    from configuratore import leggero

    p.aggiorna_env(cartella / ".env", {"MEMORIA_GB": "3.9", "WITH_LIDARR": "si", "WITH_SONARR": "si"})
    spenti = leggero.applica(cartella)
    env = leggi_env(cartella / ".env")
    assert spenti == ["Aiuto per i siti protetti (FlareSolverr)", "Musica da scaricare (Lidarr)",
                      "Sottotitoli automatici (Bazarr)", "Telegram come fonte (tgarr)"]
    assert env["WITH_FLARESOLVERR"] == env["WITH_LIDARR"] == env["WITH_BAZARR"] == env["WITH_BESZEL"] == "no"
    assert env["WITH_TGARR"] == "no"
    assert env["WITH_SONARR"] == "si"  # le serie restano come le hai scelte
    assert env["MODALITA_LEGGERA"] == "si"
    # una volta sola: se poi riaccendi qualcosa, resta acceso
    p.aggiorna_env(cartella / ".env", {"WITH_BAZARR": "si"})
    assert leggero.applica(cartella) == []
    assert leggi_env(cartella / ".env")["WITH_BAZARR"] == "si"
    # la pagina lo sa e mostra quanta memoria usa ogni programma
    s = p.scelte_attuali(p.valori(cartella))
    assert s["poca_memoria"] and s["memoria_gb"] == 3.9
    assert {v["chiave"]: v["memoria_mb"] for v in s["voci"]}["WITH_FLARESOLVERR"] == 400
    pagina = TestClient(crea_app(cartella)).get("/impostazioni/").text
    assert "Questo PC ha 3,9 GB di memoria" in pagina and "circa 400 MB" in pagina


def test_con_tanta_memoria_non_cambia_niente(cartella):
    from configuratore import leggero

    p.aggiorna_env(cartella / ".env", {"MEMORIA_GB": "16", "WITH_BAZARR": "si"})
    assert leggero.applica(cartella) == []
    assert leggi_env(cartella / ".env")["WITH_BAZARR"] == "si"
    assert not p.scelte_attuali(p.valori(cartella))["poca_memoria"]


def test_aggiornamenti_dalla_pagina(cartella, monkeypatch):
    from configuratore import aggiornamenti

    (cartella / "VERSION").write_text("1.3.0\n")
    client = TestClient(crea_app(cartella))
    assert client.post("/impostazioni/api/installa", json=dati_validi()).status_code == 200
    (cartella / ".da-applicare").unlink(missing_ok=True)
    (cartella / p.STATO).write_text("fatto")

    senza = TestClient(crea_app(cartella))
    assert senza.get("/impostazioni/api/aggiornamento").status_code == 401
    assert senza.post("/impostazioni/api/aggiornamento/installa", json={}).status_code == 401

    d = client.get("/impostazioni/api/aggiornamento").json()
    assert d["attuale"]["versione"] == "1.3.0" and not d["in_corso"] and d["automatici"]

    monkeypatch.setattr(aggiornamenti, "controlla", lambda c: {"disponibile": True, "ultima": {"fonte": "main"}})
    assert client.post("/impostazioni/api/aggiornamento/controlla", json={}).json()["disponibile"]

    assert client.post("/impostazioni/api/aggiornamento/installa", json={}).json()["ok"]
    assert (cartella / ".da-aggiornare").exists() and (cartella / ".da-applicare").exists()
    d = client.get("/impostazioni/api/aggiornamento").json()
    assert d["in_corso"] and d["stato"]["fase"] == "in_attesa"
    r = client.post("/impostazioni/api/aggiornamento/installa", json={}).json()
    assert "già in corso" in r["messaggio"]

    # il registro mostra solo l'ultimo aggiornamento
    (cartella / "aggiornamenti.log").write_text("== 19/11 ==\nvecchio\n\n== 20/11 ==\n  · Scarico cerca\n")
    assert client.get("/impostazioni/api/aggiornamento").json()["registro"] == ["== 20/11 ==", "  · Scarico cerca"]
    pagina = client.get("/impostazioni/").text
    assert 'id="agg-verifica"' in pagina and 'id="agg-installa"' in pagina


def test_cache_del_browser(cartella):
    client = TestClient(crea_app(cartella))
    pagina = client.get("/impostazioni/")
    assert pagina.headers["cache-control"] == "no-cache"
    import re

    v = re.search(r"procedura\.js\?v=(\w+)", pagina.text).group(1)
    assert f"stile.css?v={v}" in pagina.text
    statico = client.get(f"/impostazioni/static/procedura.js?v={v}")
    assert statico.status_code == 200 and "immutable" in statico.headers["cache-control"]
    assert client.get("/impostazioni/static/logo.svg").headers["cache-control"] == "public, max-age=604800"


def test_impostazioni_download_e_telegram(cartella):
    assert p.salva_impostazioni(cartella, {
        "lingue_audio": "es, it", "dimensione_max_gb": "8,5",
        "telegram": {"api_id": "12345", "api_hash": "A" * 32, "telefono": "+39 333 123 4567"},
    })
    env = leggi_env(cartella / ".env")
    assert env["LINGUE_AUDIO"] == "es,it" and env["DIMENSIONE_MAX_GB"] == "8.5"
    assert env["TG_API_ID"] == "12345" and env["TG_API_HASH"] == "a" * 32 and env["TG_PHONE"] == "+393331234567"
    # hash vuoto: resta quello di prima
    p.salva_impostazioni(cartella, {"telegram": {"api_id": "12345", "api_hash": "", "telefono": ""}})
    assert leggi_env(cartella / ".env")["TG_API_HASH"] == "a" * 32
    with pytest.raises(p.DatiNonValidi):
        p.salva_impostazioni(cartella, {"lingue_audio": "klingon"})
    with pytest.raises(p.DatiNonValidi):
        p.salva_impostazioni(cartella, {"dimensione_max_gb": "500"})
    with pytest.raises(p.DatiNonValidi):
        p.salva_impostazioni(cartella, {"telegram": {"api_id": "abc"}})
    scelte = p.scelte_attuali(leggi_env(cartella / ".env"))
    assert scelte["telegram"]["hash_impostato"] and "api_hash" not in scelte["telegram"]
