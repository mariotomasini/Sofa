"""Test dei suggerimenti musicali, del controllo del disco e della pulizia."""

import datetime as dt
import os
import time
from pathlib import Path

from app.musica import interpreta_tendenze, normalizza, unisci_simili
from app.pulizia import leggi_data, percorso_radarr, protetto, scegli_visti, trova_abbandonati, unisci_dati_utente
from app.stato import messaggio_disco

# --- musica ---------------------------------------------------------------------------


def test_normalizza_nomi_artisti():
    assert normalizza("The Beatles") == "beatles"
    assert normalizza("Måneskin") == "maneskin"
    assert normalizza("AC/DC") == "ac dc"


def test_simili_a_piu_artisti_salgono_e_quelli_in_libreria_spariscono():
    risposte = {
        "Nirvana": [
            {"artist_mbid": "a", "name": "Pearl Jam", "score": 100},
            {"artist_mbid": "b", "name": "Soundgarden", "score": 80},
            {"artist_mbid": "c", "name": "Foo Fighters", "score": 60},
        ],
        "Alice in Chains": [
            {"artist_mbid": "b", "name": "Soundgarden", "score": 50},
            {"artist_mbid": "d", "name": "Stone Temple Pilots", "score": 40},
        ],
    }
    risultato = unisci_simili(risposte, da_escludere={"foo fighters"})
    nomi = [r["nome"] for r in risultato]
    assert nomi[0] == "Soundgarden"  # simile a entrambi
    assert "Foo Fighters" not in nomi  # già in libreria
    assert risultato[0]["simile_a"] == ["Nirvana", "Alice in Chains"]


def test_tendenze_segna_quelli_gia_in_libreria():
    dati = {"payload": {"artists": [
        {"artist_mbid": "x", "artist_name": "BTS", "listen_count": 500},
        {"artist_mbid": "y", "artist_name": "Måneskin", "listen_count": 300},
        {"artist_mbid": None, "artist_name": "Senza id", "listen_count": 1},
    ]}}
    risultato = interpreta_tendenze(dati, {"maneskin"})
    assert [(r["nome"], r["in_libreria"]) for r in risultato] == [("BTS", False), ("Måneskin", True)]


# --- disco -------------------------------------------------------------------------------


def test_messaggio_disco():
    msg = messaggio_disco({"libero": 50 * 1024**3, "libero_percento": 4.6})
    assert "restano 50 GB (5%)" in msg and "mediabox impostazioni" in msg


# --- pulizia -------------------------------------------------------------------------------


def _vecchio(percorso, giorni):
    t = time.time() - giorni * 86400
    os.utime(percorso, (t, t))


def test_trova_abbandonati_rispetta_download_in_uso_e_recenti(tmp_path):
    dl = tmp_path / "downloads"
    (dl / "cerca" / "Vecchio (1990)").mkdir(parents=True)
    (dl / "cerca" / "Vecchio (1990)" / "film.mkv.part").write_bytes(b"x" * 100)
    (dl / "manuale").mkdir()
    (dl / "manuale" / "in_uso.mkv").write_bytes(b"y" * 10)
    (dl / "manuale" / "recente.mkv").write_bytes(b"z" * 10)
    vecchia = dl / "cerca" / "Vecchio (1990)"
    for p in (vecchia / "film.mkv.part", vecchia, dl / "manuale" / "in_uso.mkv"):
        _vecchio(p, 30)
    in_uso = {str(dl / "manuale" / "in_uso.mkv")}
    trovati = trova_abbandonati(dl, in_uso, time.time())
    assert [t["nome"] for t in trovati] == ["Vecchio (1990)"]
    assert trovati[0]["dimensione"] == 100 and trovati[0]["giorni"] >= 29


def test_protetto():
    in_uso = {"/data/downloads/cerca/A/film.mkv"}
    assert protetto(Path("/data/downloads/cerca/A"), in_uso)  # contiene un file in uso
    assert not protetto(Path("/data/downloads/cerca/B"), in_uso)


def test_leggi_data_jellyfin():
    d = leggi_data("2026-10-01T20:15:00.1234567Z")
    assert d == dt.datetime(2026, 10, 1, 20, 15, 0, 123456, tzinfo=dt.UTC)
    assert leggi_data(None) is None and leggi_data("boh") is None


def test_scegli_visti_rispetta_preferiti_meta_e_tempo():
    elementi: dict = {}
    voci = [
        # (id, utente, dati)
        ("vecchio", {"Played": True, "LastPlayedDate": "2026-08-01T10:00:00Z"}),
        ("recente", {"Played": True, "LastPlayedDate": "2026-11-15T10:00:00Z"}),
        ("preferito", {"Played": True, "IsFavorite": True, "LastPlayedDate": "2026-07-01T10:00:00Z"}),
        ("mai_visto", {"Played": False}),
        ("piu_vecchio", {"Played": True, "LastPlayedDate": "2026-06-01T10:00:00Z"}),
    ]
    for ident, dati in voci:
        unisci_dati_utente(elementi, {"Id": ident, "Type": "Movie", "Name": ident, "Path": f"/data/film/{ident}.mkv",
                                      "ProviderIds": {"Tmdb": "1"}, "UserData": dati})
    # un secondo utente sta guardando "vecchio" a metà: non si tocca
    unisci_dati_utente(elementi, {"Id": "vecchio", "Type": "Movie", "UserData": {"PlaybackPositionTicks": 5}})
    adesso = dt.datetime(2026, 11, 19, tzinfo=dt.UTC)
    scelti = scegli_visti(elementi, adesso, giorni=30)
    assert [s["id"] for s in scelti] == ["piu_vecchio"]


def test_percorso_radarr():
    assert percorso_radarr("/data/film/X (2000)/x.mkv") == "/data/media/film/X (2000)/x.mkv"
