"""Scoperte per te: la playlist settimanale di musica nuova, scaricata da sola."""

import asyncio
import datetime as dt
import time

import httpx

from app.config import Config
from app.scoperte import Scoperte, artisti_preferiti, nome_playlist, scegli_brani


def test_nome_e_artisti_preferiti():
    assert nome_playlist(dt.date(2026, 11, 30)) == "Scoperte · 30 nov"
    nomi = ["Adele", "The Weeknd", "adele", "Weeknd", "Queen", "ADELE"]
    assert artisti_preferiti(nomi) == ["Adele", "The Weeknd", "Queen"]


def test_scegli_brani_un_giro_per_artista_senza_ripetere():
    per_artista = [
        ("A", [{"id": 1, "titolo": "a1", "artista": "A"}, {"id": 2, "titolo": "a2", "artista": "A"},
               {"id": 3, "titolo": "a3", "artista": "A"}]),
        ("B", [{"id": 4, "titolo": "b1", "artista": "B"}, {"id": 5, "titolo": "b2", "artista": "B"}]),
    ]
    scelti = scegli_brani(per_artista, proposti={1}, quanti=3)
    # il primo di A era già stato proposto: prima a2 e b1, poi il secondo giro
    assert [s["id_deezer"] for s in scelti] == [2, 4, 3]


class Archivio:
    def __init__(self):
        self.stato = {}

    def stato_leggi(self, k):
        return self.stato.get(k)

    def stato_scrivi(self, k, v):
        self.stato[k] = v

    def stato_togli(self, k):
        self.stato.pop(k, None)


def navidrome(r: httpx.Request) -> httpx.Response:
    metodo = r.url.path.rsplit("/", 1)[-1]
    ok = {"status": "ok"}
    if metodo == "getStarred2":
        ok["starred2"] = {"song": [{"artist": "Adele"}, {"artist": "Adele"}]}
    elif metodo == "getAlbumList2":
        ok["albumList2"] = {"album": [{"artist": "Queen"}]}
    elif metodo == "getPlaylists":
        ok["playlists"] = {"playlist": []}
    return httpx.Response(200, json={"subsonic-response": ok})


class Musica:
    async def mbid_da_nome(self, nome):
        return {"Adele": "m-adele", "Queen": "m-queen"}.get(nome, "")

    async def simili(self, mbid):
        if mbid == "m-adele":
            return [{"artist_mbid": "m-duffy", "name": "Duffy", "score": 10},
                    {"artist_mbid": "m-queen", "name": "Queen", "score": 5}]
        return [{"artist_mbid": "m-bowie", "name": "David Bowie", "score": 8}]

    async def _artisti_in_libreria(self):
        return set()


class Deezer:
    async def trova_artista(self, nome):
        return {"id": {"Duffy": 10, "David Bowie": 20}[nome], "nome": nome}

    async def _get(self, percorso, parametri=None, durata=0):
        base = 100 if percorso == "artist/10/top" else 200
        nome = "Duffy" if base == 100 else "David Bowie"
        return {"data": [{"id": base + i, "title": f"{nome} {i}", "artist": {"name": nome}, "album": {"title": "X"}}
                         for i in range(3)]}


class Jellyfin:
    async def _get(self, percorso, parametri):
        assert percorso == "/Users"
        return [{"Id": "u1", "Name": "Mario", "Policy": {"IsAdministrator": True}},
                {"Id": "u2", "Name": "Valentina", "Policy": {}},
                {"Id": "u3", "Name": "Vecchio", "Policy": {"IsDisabled": True}}]


class Importa:
    def __init__(self):
        self.create = []

    def nuova_da_brani(self, utente, nome, brani, origine="importazione", indice=0):
        self.create.append((utente["nome"], nome, [b["id_deezer"] for b in brani], origine))
        return {"id": str(len(self.create))}


def prepara():
    archivio, importa = Archivio(), Importa()
    sc = Scoperte(Config(navidrome_url="http://navidrome:4533", utente="admin", password="x"), archivio,
                  httpx.AsyncClient(transport=httpx.MockTransport(navidrome)), Jellyfin(), Musica(), Deezer(), importa)
    return sc, archivio, importa


def test_crea_la_playlist_della_settimana_e_non_ripete():
    sc, archivio, importa = prepara()
    utente = {"id": "u2", "nome": "Valentina", "admin": False}
    asyncio.run(sc.crea(utente, oggi=dt.date(2026, 11, 30)))
    nome, playlist, brani, origine = importa.create[0][0], importa.create[0][1], importa.create[0][2], importa.create[0][3]
    # Queen la ama già: si propongono solo Duffy e David Bowie, due brani a testa
    assert (nome, playlist, origine) == ("Valentina", "Scoperte · 30 nov", "scoperte")
    assert sorted(brani) == [100, 101, 200, 201]
    asyncio.run(sc.crea(utente))
    assert sorted(importa.create[1][2]) == [102, 202]  # la settimana dopo, brani mai proposti


def test_giro_settimanale_per_ogni_persona_e_spegnibile():
    sc, archivio, importa = prepara()
    sc.imposta({"id": "u1"}, accese=False)
    assert asyncio.run(sc.giro()) == 1
    assert [c[0] for c in importa.create] == ["Valentina"]  # Mario le ha spente, Vecchio è disattivato
    assert asyncio.run(sc.giro()) == 0  # una settimana non è ancora passata
    archivio.stato["scoperte:ultima:u2"] = str(time.time() - 8 * 86400)
    assert asyncio.run(sc.giro()) == 1
    stato = sc.stato({"id": "u2"})
    assert stato["accese"] and stato["prossima"] > time.time() and not sc.stato({"id": "u1"})["accese"]
