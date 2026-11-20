"""Serie TV: "Segui la serie" aggiunge a Sonarr la serie giusta, con le scelte giuste."""

import asyncio
import json

import httpx
import pytest

from app.config import Config
from app.rete import ErroreServizio
from app.serie import Serie, e_anime, scegli_cartella, scegli_profilo

CARTELLE = [{"id": 1, "path": "/data/media/serie"}, {"id": 2, "path": "/data/media/anime"}]
PROFILI = [{"id": 1, "name": "Any"}, {"id": 4, "name": "HD-1080p"}, {"id": 6, "name": "HD - 720p/1080p"}]


def sonarr(serie_per_termine, aggiunte, gia_seguite=()):
    def gestore(r: httpx.Request) -> httpx.Response:
        assert r.headers["X-Api-Key"] == "chiave"
        percorso = r.url.path.removeprefix("/sonarr/api/v3/")
        if percorso == "series/lookup":
            termine = r.url.params["term"]
            trovate = [dict(s, id=(7 if s["tvdbId"] in gia_seguite else 0)) for s in serie_per_termine.get(termine, [])]
            return httpx.Response(200, json=trovate)
        if percorso == "qualityprofile":
            return httpx.Response(200, json=PROFILI)
        if percorso == "rootfolder":
            return httpx.Response(200, json=CARTELLE)
        if percorso == "series" and r.method == "POST":
            aggiunte.append(json.loads(r.content))
            return httpx.Response(201, json={"id": 99})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(gestore))
    return Serie(Config(sonarr_url="http://sonarr:8989/sonarr", sonarr_chiave="chiave"), client)


DARK = {"title": "Dark", "tvdbId": 334824, "genres": ["Drama", "Mystery"], "year": 2017}
FRIEREN = {"title": "Frieren", "tvdbId": 424536, "genres": ["Animation", "Anime"], "year": 2023}


def test_scelte():
    assert scegli_profilo(PROFILI) == 6
    assert scegli_profilo([{"id": 3, "name": "Altro"}]) == 3
    assert scegli_cartella(CARTELLE, anime=False) == "/data/media/serie"
    assert scegli_cartella(CARTELLE, anime=True) == "/data/media/anime"
    assert scegli_cartella(CARTELLE[:1], anime=True) == "/data/media/serie"
    assert e_anime(FRIEREN) and not e_anime(DARK)


def test_segui_con_i_codici_prima_del_titolo():
    aggiunte = []
    s = sonarr({"tvdb:334824": [DARK], "Dark": [{"title": "Altra serie", "tvdbId": 1}]}, aggiunte)
    r = asyncio.run(s.segui(tvdb=334824, titolo="Dark"))
    assert r["nuova"] and "Dark" in r["messaggio"]
    a = aggiunte[0]
    assert a["tvdbId"] == 334824 and a["rootFolderPath"] == "/data/media/serie" and a["seriesType"] == "standard"
    assert a["qualityProfileId"] == 6 and a["monitored"] and a["seasonFolder"]
    assert a["addOptions"] == {"monitor": "all", "searchForMissingEpisodes": True, "searchForCutoffUnmetEpisodes": False}


def test_anime_nella_cartella_degli_anime_e_titolo_come_ultima_risorsa():
    aggiunte = []
    s = sonarr({"tmdb:209867": [], "imdb:tt22248376": [], "Frieren": [FRIEREN]}, aggiunte)
    asyncio.run(s.segui(tmdb=209867, imdb="tt22248376", titolo="Frieren"))
    assert aggiunte[0]["rootFolderPath"] == "/data/media/anime" and aggiunte[0]["seriesType"] == "anime"


def test_gia_seguita_e_errori():
    aggiunte = []
    s = sonarr({"tvdb:334824": [DARK]}, aggiunte, gia_seguite={334824})
    assert not asyncio.run(s.segui(tvdb=334824))["nuova"] and aggiunte == []
    with pytest.raises(ErroreServizio, match="non trova"):
        asyncio.run(sonarr({}, []).segui(titolo="Inesistente"))
    spenta = Serie(Config(sonarr_url="", sonarr_chiave=""), httpx.AsyncClient())
    with pytest.raises(ErroreServizio, match="non sono attive"):
        asyncio.run(spenta.segui(titolo="Dark"))


def test_le_serie_di_tvmaze_portano_i_codici_per_sonarr():
    from app.catalogo_libero import riassunto_tvmaze, snellisci_serie

    show = {"id": 17861, "name": "Dark", "externals": {"thetvdb": 334824, "imdb": "tt5753856"}, "genres": ["Drama"]}
    r = riassunto_tvmaze(snellisci_serie(show))
    assert r["tvdb"] == 334824 and r["imdb_id"] == "tt5753856" and r["serie"]
