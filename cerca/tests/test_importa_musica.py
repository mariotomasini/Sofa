"""Importare le playlist di Apple Music: CSV, download pochi alla volta, playlist e preferiti in Navidrome."""

import asyncio
import json

import httpx
import pytest

from app.config import Config
from app.importa_musica import ImportaMusica, leggi_csv, tipo_playlist
from app.navidrome import credenziali, stesso_brano
from app.rete import ErroreServizio

TUNEMYMUSIC = (
    "﻿Track name,Artist name,Album,Playlist name,Type,ISRC\n"
    "Hello,Adele,25,Estate,Playlist,GBBKS1500214\n"
    "Bohemian Rhapsody,Queen,A Night at the Opera,Estate,Playlist,\n"
    "Hello,Adele,25,Estate,Playlist,GBBKS1500214\n"
    "Vita spericolata,Vasco Rossi,Bollicine,Brani preferiti,Playlist,\n"
)
SOUNDIIZ = "title;artist;album;isrc\nBlinding Lights;The Weeknd, Daft Punk;After Hours;USUG11904206\n"


def test_leggi_csv_tunemymusic_e_soundiiz():
    pl = leggi_csv(TUNEMYMUSIC, "export.csv")
    assert list(pl) == ["Estate", "Brani preferiti"]
    assert pl["Estate"][1] == {"titolo": "Bohemian Rhapsody", "artista": "Queen", "album": "A Night at the Opera"}
    # senza colonna della playlist: il nome del file; per cercare basta il primo artista
    assert leggi_csv(SOUNDIIZ, "Corsa.csv") == {
        "Corsa": [{"titolo": "Blinding Lights", "artista": "The Weeknd", "album": "After Hours"}]}
    with pytest.raises(ErroreServizio):
        leggi_csv("a,b\n1,2\n", "strano.csv")


def test_tipo_playlist():
    assert tipo_playlist("Brani preferiti") == "preferiti" and tipo_playlist("Favourite Songs") == "preferiti"
    assert tipo_playlist("Libreria") == "libreria"
    assert tipo_playlist("Estate 2024") == "playlist"


def test_credenziali_e_stesso_brano():
    config = Config(utente="admin", password="segreta")
    assert credenziali(config, {"nome": "Valentina", "admin": False}) == ("Valentina", "valentina")
    assert credenziali(config, {"nome": "Mario", "admin": True}) == ("admin", "segreta")
    assert stesso_brano({"title": "Hello", "artist": "Adele"}, "Adele", "Hello")
    assert stesso_brano({"title": "Hello (Live)", "artist": "Adele"}, "Adele", "Hello")
    assert not stesso_brano({"title": "Hello", "artist": "Lionel Richie"}, "Adele", "Hello")


class Archivio:
    def __init__(self):
        self.stato, self.download_righe = {}, {}

    def stato_leggi(self, k):
        return self.stato.get(k)

    def stato_scrivi(self, k, v):
        self.stato[k] = v

    def stato_togli(self, k):
        self.stato.pop(k, None)

    def stato_con_prefisso(self, p):
        return {k: v for k, v in self.stato.items() if k.startswith(p)}

    def download(self, i):
        return self.download_righe.get(i)

    def elimina_download(self, i):
        self.download_righe.pop(i, None)


class NavidromeFinto:
    """L'API Subsonic: la libreria, le playlist e i preferiti, per utente."""

    def __init__(self):
        self.libreria = {"nd-hello": ("Hello", "Adele")}
        self.playlist: dict[str, dict] = {}
        self.preferiti: dict[str, list] = {}
        self.scansioni = 0

    def gestore(self, r: httpx.Request) -> httpx.Response:
        assert r.url.path.startswith("/navidrome/rest/")
        metodo = r.url.path.rsplit("/", 1)[-1]
        p = r.url.params
        utente = p["u"]
        ok = {"status": "ok"}
        if metodo == "search3":
            parole = p["query"].lower().split()
            canzoni = [{"id": i, "title": t, "artist": a} for i, (t, a) in self.libreria.items()
                       if all(w in f"{t} {a}".lower() for w in parole)]
            ok["searchResult3"] = {"song": canzoni}
        elif metodo == "getPlaylists":
            ok["playlists"] = {"playlist": [{"id": i, "name": x["nome"], "owner": x["utente"]}
                                            for i, x in self.playlist.items()]}
        elif metodo == "createPlaylist":
            i = f"pl{len(self.playlist) + 1}"
            self.playlist[i] = {"nome": p["name"], "utente": utente, "brani": p.get_list("songId")}
            ok["playlist"] = {"id": i}
        elif metodo == "updatePlaylist":
            self.playlist[p["playlistId"]]["brani"] += p.get_list("songIdToAdd")
        elif metodo == "star":
            self.preferiti.setdefault(utente, []).extend(p.get_list("id"))
        elif metodo == "startScan":
            self.scansioni += 1
        return httpx.Response(200, json={"subsonic-response": ok})


class DeezerFinto:
    async def _get(self, percorso, parametri=None, durata=0):
        q = parametri["q"]
        if "Queen" in q:
            return {"data": [{"id": 77, "title": "Bohemian Rhapsody", "artist": {"name": "Queen"}}]}
        if "Vasco" in q:
            return {"data": [{"id": 78, "title_short": "Vita spericolata", "artist": {"name": "Vasco Rossi"}}]}
        return {"data": []}


class ScaricaFinta:
    def __init__(self, archivio):
        self.archivio, self.avviati = archivio, []

    async def brano(self, id_brano, fonte="prowlarr"):
        assert fonte == "importazione"
        self.avviati.append(id_brano)
        id_download = 499 + len(self.avviati)
        self.archivio.download_righe[id_download] = {"id": id_download, "stato": "in_corso"}
        return {"id": id_download}


def prepara():
    archivio = Archivio()
    nd = NavidromeFinto()
    avvisi = []

    async def notifica(titolo, messaggio, link=""):
        avvisi.append(messaggio)

    imp = ImportaMusica(Config(navidrome_url="http://navidrome:4533", utente="admin", password="x"), archivio,
                        httpx.AsyncClient(transport=httpx.MockTransport(nd.gestore)), DeezerFinto(),
                        ScaricaFinta(archivio), notifica)
    return imp, archivio, nd, avvisi


def test_importazione_completa():
    imp, archivio, nd, avvisi = prepara()
    valentina = {"id": "u2", "nome": "Valentina", "admin": False}
    creati = imp.nuova(valentina, [("export.csv", TUNEMYMUSIC + "Canzone,Sconosciuto,,Estate,Playlist,\n")])
    assert [(c["nome"], c["totale"]) for c in creati] == [("Estate", 3), ("Brani preferiti", 1)]

    asyncio.run(imp.giro())
    # Hello c'era già: subito nella playlist di Valentina; Queen in download; lo sconosciuto non c'è su Deezer
    assert nd.playlist["pl1"] == {"nome": "Estate", "utente": "Valentina", "brani": ["nd-hello"]}
    assert imp.scarica.avviati == [77, 78]
    estate = next(x for x in imp.elenco(valentina) if x["nome"] == "Estate")
    assert (estate["pronti"], estate["in_corso"], estate["non_trovati"]) == (1, 1, 1)
    assert not avvisi

    # finisce il download, Navidrome lo trova: va nella stessa playlist e il download sparisce dall'elenco
    archivio.download_righe[500]["stato"] = "importato"
    archivio.download_righe[501]["stato"] = "importato"
    nd.libreria["nd-queen"] = ("Bohemian Rhapsody", "Queen")
    nd.libreria["nd-vasco"] = ("Vita spericolata", "Vasco Rossi")
    asyncio.run(imp.giro())
    assert nd.playlist["pl1"]["brani"] == ["nd-hello", "nd-queen"] and len(nd.playlist) == 1
    assert archivio.download_righe == {}
    assert nd.preferiti == {"Valentina": ["nd-vasco"]}
    assert sorted(avvisi) == ["\"Brani preferiti\": 1 brano nei preferiti.",
                              "\"Estate\": 2 brani nella playlist, 1 non trovato."]
    asyncio.run(imp.giro())
    assert len(avvisi) == 2  # un avviso solo per importazione

    # gli altri utenti non vedono le importazioni di Valentina, l'amministratore sì
    assert imp.elenco({"id": "u3", "nome": "Luca", "admin": False}) == []
    assert len(imp.elenco({"id": "u1", "nome": "Mario", "admin": True})) == 2


def test_scaricato_ma_non_ancora_in_navidrome_chiede_una_scansione():
    imp, archivio, nd, _ = prepara()
    imp.nuova(None, [("Rock.csv", "title,artist\nBohemian Rhapsody,Queen\n")])
    asyncio.run(imp.giro())
    archivio.download_righe[500]["stato"] = "importato"
    asyncio.run(imp.giro())
    assert nd.scansioni == 1
    lavoro = json.loads(next(iter(archivio.stato.values())))
    assert lavoro["brani"][0]["stato"] == "scaricato"


def test_senza_navidrome():
    imp = ImportaMusica(Config(navidrome_url=""), Archivio(), None, None, None)
    with pytest.raises(ErroreServizio):
        imp.nuova(None, [("a.csv", SOUNDIIZ)])
