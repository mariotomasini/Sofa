"""Test della home in stile Netflix: generi, Top 10, appena usciti, continua a guardare."""

import asyncio
import datetime as dt

import httpx

from app import home
from app.config import Config
from app.jellyfin import Jellyfin
from app.rete import ErroreServizio
from app.tmdb import TMDB


class ArchivioFinto:
    def cache_leggi(self, chiave):
        return None

    def cache_scrivi(self, chiave, valore, durata):
        pass


def film(i, generi=(35,), poster=True):
    return {"id": i, "title": f"Film {i}", "release_date": "2026-11-01", "genre_ids": list(generi),
            "poster_path": f"/{i}.jpg" if poster else None, "popularity": 100 - i}


GENERI_FILM = {
    "it-IT": [{"id": 28, "name": "Azione"}, {"id": 35, "name": "Commedia"}, {"id": 27, "name": "Horror"},
              {"id": 16, "name": "Animazione"}, {"id": 10751, "name": "Famiglia"}, {"id": 878, "name": "Fantascienza"}],
    "en-US": [{"id": 28, "name": "Action"}, {"id": 35, "name": "Comedy"}, {"id": 27, "name": "Horror"},
              {"id": 16, "name": "Animation"}, {"id": 10751, "name": "Family"}, {"id": 878, "name": "Science Fiction"}],
}
GENERI_TV = {
    "it-IT": [{"id": 10759, "name": "Action & Adventure"}, {"id": 18, "name": "Dramma"}, {"id": 80, "name": "Crime"},
              {"id": 10765, "name": "Sci-Fi & Fantasy"}],
    "en-US": [{"id": 10759, "name": "Action & Adventure"}, {"id": 18, "name": "Drama"}, {"id": 80, "name": "Crime"},
              {"id": 10765, "name": "Sci-Fi & Fantasy"}],
}


def crea_tmdb(gestore_extra=None, richieste=None):
    def gestore(r: httpx.Request) -> httpx.Response:
        percorso = r.url.path.removeprefix("/3")
        if richieste is not None:
            richieste.append((percorso, dict(r.url.params)))
        if percorso == "/genre/movie/list":
            return httpx.Response(200, json={"genres": GENERI_FILM[r.url.params["language"]]})
        if percorso == "/genre/tv/list":
            return httpx.Response(200, json={"genres": GENERI_TV[r.url.params["language"]]})
        if gestore_extra:
            return gestore_extra(percorso, r.url.params)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(gestore))
    return TMDB(Config(tmdb_chiave="chiave-corta", tmdb_api="https://api.themoviedb.org/3"), ArchivioFinto(), client)


def test_scegli_generi_prima_i_tuoi_poi_quelli_di_partenza():
    assert home.scegli_generi([27, 878, 35], "movie") == [(27, True), (878, True)]
    assert home.scegli_generi([27], "movie") == [(27, True), (35, False)]
    assert home.scegli_generi([], "tv") == [(18, False), (80, False)]
    assert home.scegli_generi([35], "movie") == [(35, True), (28, False)]  # niente doppioni


def test_nomi_dei_generi_di_jellyfin_in_italiano_e_inglese():
    async def prova():
        tmdb = crea_tmdb()
        film_ = await tmdb.id_genere("movie", ["Horror", "Comedy", "Fantascienza", "Genere inventato"])
        serie = await tmdb.id_genere("tv", ["Azione", "Dramma", "Horror", "Fantascienza"])
        return film_, serie

    film_, serie = asyncio.run(prova())
    assert film_ == {"Horror": 27, "Comedy": 35, "Fantascienza": 878}
    # sulle serie "Azione" diventa "Action & Adventure"; "Horror" non esiste tra i generi delle serie
    assert serie == {"Azione": 10759, "Dramma": 18, "Fantascienza": 10765}


def test_scopri_e_kids():
    richieste = []

    def extra(percorso, parametri):
        return httpx.Response(200, json={"results": [
            film(1, (16, 10751)), film(2, (16, 27)), film(3, (16,), poster=False), film(4, (12,)),
        ]})

    async def prova():
        tmdb = crea_tmdb(extra, richieste)
        normale = await tmdb.scopri("movie", 16)
        kids = await tmdb.scopri("movie", 16, kids=True)
        return normale, kids

    normale, kids = asyncio.run(prova())
    assert [f["id"] for f in normale] == [1, 2, 4]  # senza locandina no
    assert [f["id"] for f in kids] == [1]  # niente horror e solo titoli per bambini
    assert "watch_region" not in richieste[0][1]
    assert richieste[1][1]["certification.lte"] == "PG"


def test_top10_e_appena_usciti():
    richieste = []

    def extra(percorso, parametri):
        return httpx.Response(200, json={"results": [film(i) for i in range(1, 16)]})

    async def prova():
        tmdb = crea_tmdb(extra, richieste)
        return await tmdb.top_settimana("tv"), await tmdb.appena_usciti("movie", oggi=dt.date(2026, 11, 19))

    top, nuovi = asyncio.run(prova())
    assert len(top) == 10 and top[0]["serie"]
    assert len(nuovi) == 10
    percorso, parametri = richieste[1]
    assert percorso == "/discover/movie"
    assert parametri["primary_release_date.gte"] == "2026-09-20"
    assert parametri["primary_release_date.lte"] == "2026-11-19"
    assert parametri["region"] == "IT"


class JellyfinFinto:
    def __init__(self, generi=(), libreria=(), continua=(), rotto=False):
        self._generi, self._libreria, self._continua, self.rotto = list(generi), set(libreria), list(continua), rotto

    async def generi_preferiti(self, tipo):
        if self.rotto:
            raise httpx.ConnectError("giù")
        return self._generi

    async def tmdb_in_libreria(self, tipo):
        if self.rotto:
            raise httpx.ConnectError("giù")
        return self._libreria

    async def continua(self, tipo):
        if self.rotto:
            raise httpx.ConnectError("giù")
        return self._continua


def tmdb_per_home():
    def extra(percorso, parametri):
        if percorso == "/discover/movie" and "with_genres" in parametri:
            base = int(parametri["with_genres"]) * 100
            # il film 1 compare in tutti i generi: deve comparire una volta sola
            return httpx.Response(200, json={"results": [film(1)] + [film(base + i) for i in range(1, 5)]})
        if percorso == "/discover/movie":
            return httpx.Response(200, json={"results": [film(i) for i in range(900, 912)]})
        if percorso == "/trending/movie/week":
            return httpx.Response(200, json={"results": [film(i) for i in range(500, 515)]})
        return httpx.Response(404)

    return crea_tmdb(extra)


def test_righe_della_home_nell_ordine_chiesto():
    jf = JellyfinFinto(generi=["Horror", "Commedia"], libreria={2701},
                       continua=[{"id": "x", "titolo": "A metà", "percentuale": 40}])
    risposta = asyncio.run(home.righe(tmdb_per_home(), jf, "movie"))
    tipi = [r["tipo"] for r in risposta["righe"]]
    assert tipi == ["suggeriti", "suggeriti", "top10", "nuovi", "continua"]
    horror, commedia = risposta["righe"][0], risposta["righe"][1]
    assert horror["titolo"] == "Perché ti piace: Horror" and horror["genere"] == 27
    assert 2701 not in [f["id"] for f in horror["elementi"]]  # già in libreria
    assert 1 in [f["id"] for f in horror["elementi"]]
    assert 1 not in [f["id"] for f in commedia["elementi"]]  # già nella riga sopra
    assert len(risposta["righe"][2]["elementi"]) == 10
    assert risposta["righe"][2]["titolo"] == "Top 10 film della settimana"
    assert "errore" not in risposta


def test_home_senza_jellyfin_usa_i_generi_di_partenza():
    risposta = asyncio.run(home.righe(tmdb_per_home(), JellyfinFinto(rotto=True), "movie"))
    titoli = [r["titolo"] for r in risposta["righe"]]
    assert titoli == ["Da non perdere: Commedia", "Da non perdere: Azione", "Top 10 film della settimana", "Appena usciti"]


def test_home_senza_tmdb_mostra_almeno_continua():
    tmdb = crea_tmdb()

    async def generi_rotti(*_a, **_k):
        raise ErroreServizio("TMDB rifiuta la chiave API.", "Controlla la chiave.")

    tmdb.generi = generi_rotti
    jf = JellyfinFinto(continua=[{"id": "x", "titolo": "A metà", "percentuale": 40}])
    risposta = asyncio.run(home.righe(tmdb, jf, "movie"))
    assert [r["tipo"] for r in risposta["righe"]] == ["continua"]
    assert risposta["errore"]["errore"] == "TMDB rifiuta la chiave API."


def test_jellyfin_generi_continua_e_libreria():
    def gestore(r: httpx.Request) -> httpx.Response:
        p, q = r.url.path.removeprefix("/jellyfin"), r.url.params
        if p == "/Users/AuthenticateByName":
            return httpx.Response(200, json={"AccessToken": "t", "User": {"Id": "u1"}})
        if p == "/Users/u1/Items" and q.get("IncludeItemTypes") == "Movie" and q.get("Filters") == "IsPlayed":
            return httpx.Response(200, json={"Items": [{"Genres": ["Horror", "Commedia"]}, {"Genres": ["Horror"]}]})
        if p == "/Users/u1/Items" and q.get("IncludeItemTypes") == "Movie" and q.get("Filters") == "IsFavorite":
            return httpx.Response(200, json={"Items": [{"Genres": ["Commedia"]}]})
        if p == "/Users/u1/Items" and q.get("IncludeItemTypes") == "Movie":
            return httpx.Response(200, json={"Items": [{"ProviderIds": {"Tmdb": "550"}}, {"ProviderIds": {}}]})
        if p == "/Users/u1/Items/Resume":
            return httpx.Response(200, json={"Items": [
                {"Id": "e1", "Type": "Episode", "SeriesId": "s1", "SeriesName": "Dark", "Name": "Segreti",
                 "ParentIndexNumber": 1, "IndexNumber": 2, "UserData": {"PlayedPercentage": 37.6}}]})
        if p == "/Shows/NextUp":
            return httpx.Response(200, json={"Items": [
                {"Id": "e9", "Type": "Episode", "SeriesId": "s1", "SeriesName": "Dark", "Name": "Altro"},
                {"Id": "e5", "Type": "Episode", "SeriesId": "s2", "SeriesName": "Fargo", "Name": "Pilota",
                 "ParentIndexNumber": 2, "IndexNumber": 1}]})
        return httpx.Response(404)

    async def prova():
        async with httpx.AsyncClient(transport=httpx.MockTransport(gestore)) as client:
            jf = Jellyfin(Config(jellyfin_url="http://jellyfin:8096/jellyfin", utente="a", password="b"), client)
            return await jf.generi_preferiti("movie"), await jf.tmdb_in_libreria("movie"), await jf.continua("tv")

    generi, libreria, continua = asyncio.run(prova())
    assert generi == ["Commedia", "Horror"]  # Commedia: 1 visto + preferito (3) > Horror: 2 visti
    assert libreria == {550}
    assert [c["titolo"] for c in continua] == ["Dark", "Fargo"]  # Dark una volta sola
    assert continua[0]["sottotitolo"] == "S1 E2 · Segreti"
    assert continua[0]["percentuale"] == 38
    assert continua[0]["poster"].startswith("/jellyfin/Items/s1/Images/Primary")
    assert continua[0]["link"] == "/jellyfin/web/#/details?id=e1"


def test_chiave_inclusa_rifiutata_spiega_cosa_fare():
    import pytest

    def rifiuta(r: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    async def prova(pubblica):
        async with httpx.AsyncClient(transport=httpx.MockTransport(rifiuta)) as client:
            config = Config(tmdb_chiave="x", tmdb_api="https://api.themoviedb.org/3", tmdb_chiave_pubblica=pubblica)
            await TMDB(config, ArchivioFinto(), client).tendenze()

    with pytest.raises(ErroreServizio) as exc:
        asyncio.run(prova(True))
    assert "inclusa in mediabox" in exc.value.cosa and "mediabox update" in exc.value.cosa_fare
    with pytest.raises(ErroreServizio) as exc:
        asyncio.run(prova(False))
    assert "Impostazioni" in exc.value.cosa_fare
