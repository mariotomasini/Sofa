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
