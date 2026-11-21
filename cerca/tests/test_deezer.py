"""Musica: dati di Deezer (classifiche, artisti, album, anteprime) e home in stile app musicale."""

import asyncio
import datetime as dt

import httpx

from app import deezer as modulo
from app.config import Config
from app.deezer import Deezer
from app.musica import Musica

modulo._limite.intervallo = 0


class ArchivioFinto:
    def __init__(self):
        self.dati = {}

    def cache_leggi(self, chiave, scadute=False):
        return self.dati.get(chiave)

    def cache_scrivi(self, chiave, valore, durata):
        self.dati[chiave] = valore


def t(i, titolo, artista="Marracash", id_artista=75618):
    return {"id": i, "title": titolo, "duration": 200, "preview": f"https://cdn/{i}.mp3",
            "artist": {"id": id_artista, "name": artista}, "album": {"id": 9, "title": "Album", "cover_medium": "c.jpg"}}


def a(i, nome):
    return {"id": i, "name": nome, "picture_medium": f"{i}.jpg", "picture_xl": f"{i}xl.jpg", "nb_fan": 1000}


def al(i, titolo, data, tipo="album"):
    return {"id": i, "title": titolo, "release_date": data, "record_type": tipo, "cover_medium": f"a{i}.jpg",
            "artist": {"id": 75618, "name": "Marracash"}}


def deezer(richieste=None):
    def gestore(r: httpx.Request) -> httpx.Response:
        p = r.url.path
        if richieste is not None:
            richieste.append(p)
        if p == f"/playlist/{modulo.TOP_ITALIA}/tracks":
            return httpx.Response(200, json={"data": [t(1, "Margarita"), t(2, "NEMESI")]})
        if p == "/chart/0/albums":
            return httpx.Response(200, json={"data": [al(5, "È FINITA LA PACE", "2024-12-13")]})
        if p == "/chart/0/artists":
            return httpx.Response(200, json={"data": [a(1, "Annalisa")]})
        if p == "/search/artist":
            q = r.url.params["q"]
            return httpx.Response(200, json={"data": [a(75618, "Marracash")] if q == "Marracash" else
                                             [a(2, "Altro"), a(3, q)]})
        if p == "/artist/75618":
            return httpx.Response(200, json=a(75618, "Marracash"))
        if p == "/artist/75618/top":
            return httpx.Response(200, json={"data": [t(1, "Margarita")]})
        if p == "/artist/75618/albums":
            return httpx.Response(200, json={"data": [al(4, "Vecchio", "2019-01-01"), al(5, "Nuovo", "2026-10-01"),
                                                      al(6, "Singolo", "2026-11-01", "single")]})
        if p == "/artist/75618/related":
            return httpx.Response(200, json={"data": [a(7, "Salmo")]})
        if p == "/track/1":
            return httpx.Response(200, json=t(1, "Margarita"))
        if p == "/track/2":
            return httpx.Response(200, json={**t(2, "Senza"), "preview": ""})
        return httpx.Response(200, json={"error": {"message": "no"}})

    return Deezer(ArchivioFinto(), httpx.AsyncClient(transport=httpx.MockTransport(gestore)))


def test_artista_classifiche_e_anteprime():
    d = deezer()
    pagina = asyncio.run(d.pagina_artista(75618))
    assert pagina["nome"] == "Marracash" and pagina["foto_grande"] == "75618xl.jpg"
    assert [x["titolo"] for x in pagina["album"]] == ["Nuovo", "Vecchio"]  # dal più recente
    assert [x["titolo"] for x in pagina["singoli"]] == ["Singolo"] and pagina["simili"][0]["nome"] == "Salmo"
    assert pagina["popolari"][0] == {"id": 1, "titolo": "Margarita", "durata": 200, "artista": "Marracash",
                                     "artista_id": 75618, "album": "Album", "album_id": 9, "copertina": "c.jpg",
                                     "anteprima": True}
    assert asyncio.run(d.trova_artista("Rare"))["nome"] == "Rare"  # il nome uguale prima del primo risultato
    assert asyncio.run(d.anteprima(1))["url"] == "https://cdn/1.mp3"
    try:
        asyncio.run(d.anteprima(2))
        raise AssertionError("doveva fallire")
    except Exception as exc:  # noqa: BLE001
        assert "anteprima" in str(exc)


def test_home_della_musica():
    class JellyfinFinto:
        async def brani(self, limite):
            return [{"id": str(i) * 32, "titolo": f"Brano {i}", "artista": "Marracash", "album": "", "durata": 180,
                     "copertina_jf": "", "aggiunto": ""} for i in range(7)]

        async def artisti_piu_ascoltati(self, limite):
            return [{"nome": "Marracash", "mbid": "x"}]

        async def artisti(self, limite):
            return [{"nome": "Marracash", "mbid": "x"}]

    m = Musica(Config(), ArchivioFinto(), httpx.AsyncClient(), JellyfinFinto())

    async def per_te():
        return {"suggerimenti": [{"nome": "Salmo"}], "partenza": ["Marracash"]}

    m.per_te = per_te
    home = asyncio.run(m.home(deezer(), oggi=dt.date(2026, 11, 21)))
    titoli = [r["titolo"] for r in home["righe"]]
    assert home["preferiti"] == 7 and home["righe"][0]["quanti"] == 7 and len(home["righe"][0]["elementi"]) == 5
    assert titoli == ["Brani preferiti", "I tuoi artisti", "Consigliati per te", "Top 50 Italia", "Uscite recenti per te",
                      "Album di tendenza", "Artisti di tendenza"]
    assert home["scorciatoie"][0]["nome"] == "Marracash"
    uscite = next(r for r in home["righe"] if r["titolo"] == "Uscite recenti per te")["elementi"]
    assert [x["titolo"] for x in uscite] == ["Singolo", "Nuovo"]  # solo gli ultimi 4 mesi


def test_home_senza_jellyfin_e_senza_internet():
    class JellyfinSpento:
        async def brani(self, limite):
            raise RuntimeError("spento")

        async def artisti_piu_ascoltati(self, limite):
            raise RuntimeError("spento")

    def gestore(r):
        raise httpx.ConnectError("niente rete")

    m = Musica(Config(), ArchivioFinto(), httpx.AsyncClient(), JellyfinSpento())

    async def per_te():
        raise RuntimeError("niente")

    m.per_te = per_te
    d = Deezer(ArchivioFinto(), httpx.AsyncClient(transport=httpx.MockTransport(gestore)))
    home = asyncio.run(m.home(d))
    assert home["righe"] == [] and home["scorciatoie"] == []  # la pagina si apre lo stesso
