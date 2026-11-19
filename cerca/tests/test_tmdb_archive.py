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
