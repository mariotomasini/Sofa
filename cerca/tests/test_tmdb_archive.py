"""Test di TMDB (provider) e del filtro dei risultati di Internet Archive."""

from app.archive import normalizza, pertinente, query_ricerca
from app.tmdb import interpreta_provider, riassunto_film

RISPOSTA_PROVIDER = {
    "id": 10331,
    "results": {
        "IT": {
            "link": "https://www.themoviedb.org/movie/10331/watch?locale=IT",
            "ads": [{"provider_name": "Pluto TV", "logo_path": "/pluto.jpg", "provider_id": 300, "display_priority": 2}],
            "flatrate": [
                {"provider_name": "Prime Video", "logo_path": "/prime.jpg", "provider_id": 119, "display_priority": 1}
            ],
            "rent": [{"provider_name": "Apple TV", "logo_path": "/apple.jpg", "provider_id": 2, "display_priority": 3}],
        },
        "US": {"link": "x", "free": [{"provider_name": "Tubi", "provider_id": 73}]},
    },
}


def test_provider_italiani_raggruppati_nell_ordine_giusto():
    p = interpreta_provider(RISPOSTA_PROVIDER, "IT")
    assert [g["tipo"] for g in p["gruppi"]] == ["ads", "flatrate", "rent"]
    assert p["gruppi"][0]["etichetta"] == "Gratis con pubblicità"
    assert p["gruppi"][0]["provider"][0]["logo"] == "https://image.tmdb.org/t/p/w92/pluto.jpg"
    assert [x["nome"] for x in p["gratis"]] == ["Pluto TV"]
    assert p["link"].endswith("locale=IT")


def test_provider_paese_assente():
    p = interpreta_provider(RISPOSTA_PROVIDER, "FR")
    assert p == {"link": "", "gruppi": [], "gratis": []}


def test_riassunto_film():
    f = riassunto_film(
        {"id": 1, "title": "La notte dei morti viventi", "original_title": "Night of the Living Dead",
         "release_date": "1968-10-04", "poster_path": "/p.jpg", "vote_average": 7.456}
    )
    assert f["anno"] == "1968"
    assert f["poster"] == "https://image.tmdb.org/t/p/w342/p.jpg"
    assert f["voto"] == 7.5


def test_normalizza():
    assert normalizza("La Notte dei Morti Viventi!") == "la notte dei morti viventi"
    assert normalizza("Amélie") == "amelie"


def test_query_ricerca_con_due_titoli():
    q = query_ricerca(["Night of the Living Dead", "La notte dei morti viventi"])
    assert q == '(title:("Night of the Living Dead") OR title:("La notte dei morti viventi")) AND mediatype:(movies)'


def test_pertinente_scarta_trailer_anni_sbagliati_e_altri_titoli():
    titoli = ["Night of the Living Dead"]
    assert pertinente({"title": "Night of the Living Dead", "year": 1968}, titoli, "1968")
    assert pertinente({"title": "Night Of The Living Dead 720p 1968"}, titoli, "1968")
    assert not pertinente({"title": "Night of the living dead Trailer"}, titoli, "1968")
    assert not pertinente({"title": "Night of the Living Dead", "year": 1990}, titoli, "1968")
    assert not pertinente({"title": "Night of the Living Dead 1990 remake"}, titoli, "1968")
    assert not pertinente({"title": "Dawn of the Dead"}, titoli, "1968")


def test_novita_tendenze_e_cinema():
    import asyncio

    from app.config import Config
    from app.tmdb import TMDB

    class ArchivioFinto:
        def cache_leggi(self, chiave):
            return None

        def cache_scrivi(self, chiave, valore, durata):
            self.ultima = chiave

    risposte = {
        "/trending/movie/week": {"results": [
            {"id": 1, "title": "A", "release_date": "2026-01-01", "poster_path": "/a.jpg", "popularity": 5},
            {"id": 2, "title": "Senza locandina", "release_date": "2026-01-01", "poster_path": None},
        ]},
        "/movie/now_playing": {"results": [
            {"id": 3, "title": "Poco visto", "release_date": "2026-11-01", "poster_path": "/c.jpg", "popularity": 1},
            {"id": 4, "title": "Molto visto", "release_date": "2026-11-05", "poster_path": "/d.jpg", "popularity": 90},
        ]},
    }

    import httpx

    def gestore(richiesta: httpx.Request) -> httpx.Response:
        percorso = richiesta.url.path.removeprefix("/3")
        if percorso == "/movie/now_playing":
            assert richiesta.url.params["region"] == "IT"
        return httpx.Response(200, json=risposte[percorso])

    async def prova():
        async with httpx.AsyncClient(transport=httpx.MockTransport(gestore)) as client:
            config = Config(tmdb_chiave="chiave-corta", tmdb_api="https://api.themoviedb.org/3")
            tmdb = TMDB(config, ArchivioFinto(), client)
            return await tmdb.tendenze(), await tmdb.al_cinema()

    tendenze, cinema = asyncio.run(prova())
    assert [f["id"] for f in tendenze] == [1]  # i film senza locandina non si mostrano
    assert [f["titolo"] for f in cinema] == ["Molto visto", "Poco visto"]  # i più visti prima


def test_filtro_kids():
    from app.tmdb import adatto_ai_bambini

    assert adatto_ai_bambini({"genre_ids": [16, 10751]})
    assert not adatto_ai_bambini({"genre_ids": [16, 27]})  # animazione horror: no
    assert not adatto_ai_bambini({"genre_ids": [28]})  # azione: no
    assert not adatto_ai_bambini({"genre_ids": [16], "adult": True})


def test_calendario_sonarr_un_episodio_per_serie():
    from app.serie import interpreta_calendario

    episodi = [
        {"seriesId": 1, "seasonNumber": 2, "episodeNumber": 3, "airDateUtc": "2026-11-10T20:00:00Z", "hasFile": True,
         "series": {"id": 1, "title": "Serie A", "year": 2024, "images": [{"coverType": "poster", "remoteUrl": "http://p/a.jpg"}]}},
        {"seriesId": 1, "seasonNumber": 2, "episodeNumber": 4, "airDateUtc": "2026-11-17T20:00:00Z", "hasFile": False,
         "series": {"id": 1, "title": "Serie A", "images": []}},
        {"seriesId": 2, "seasonNumber": 1, "episodeNumber": 1, "airDateUtc": "2026-11-12T20:00:00Z", "hasFile": True,
         "series": {"id": 2, "title": "Serie B", "images": []}},
    ]
    r = interpreta_calendario(episodi)
    assert [(x["titolo"], x["sottotitolo"], x["pronto"]) for x in r] == [
        ("Serie A", "St. 2 · Ep. 4", False), ("Serie B", "St. 1 · Ep. 1", True)]


def test_pertinente_titoli_di_una_parola():
    titoli = ["The Matrix", "Matrix"]
    assert pertinente({"title": "The Matrix (1999) 720p"}, titoli, "1999")
    assert not pertinente(
        {"title": "NPTEL Lectures: Mathematics - Advanced Matrix Theory and Linear Algebra for Engineers"}, titoli, "1999"
    )
    assert pertinente({"title": "George Romero's Night of the Living Dead"}, ["Night of the Living Dead"], "1968")
