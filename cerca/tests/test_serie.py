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


def test_cosa_contiene_il_torrent():
    from app.prowlarr import contenuto

    assert contenuto("Mare.Fuori.S04E03.iTA.WEB-DL.1080p") == "Stagione 4 · Episodio 3"
    assert contenuto("Mare Fuori S02 COMPLETA iTA") == "Stagione 2"
    assert contenuto("Mare Fuori - Stagione 3 [ITA]") == "Stagione 3"
    assert contenuto("Dark Season 1 1080p") == "Stagione 1"
    assert contenuto("Mare Fuori serie completa iTA") == "Serie completa"
    assert contenuto("Mare Fuori iTA") == ""


def test_importa_le_puntate_dicendo_a_sonarr_la_serie(tmp_path):
    comandi = []
    cartella_serie = tmp_path / "serie" / "The Sea Beyond (2020)"
    cartella = "/data/downloads/cerca/Mare.Fuori.S01"

    def gestore(r: httpx.Request) -> httpx.Response:
        percorso = r.url.path.removeprefix("/sonarr/api/v3/")
        if percorso == "series/7":
            return httpx.Response(200, json={"id": 7, "path": str(cartella_serie)})
        if percorso == "manualimport":
            assert "seriesId" not in r.url.params  # con seriesId Sonarr guarderebbe la cartella della serie
            return httpx.Response(200, json=[
                # titolo italiano: Sonarr non sa di che serie è
                {"path": f"{cartella}/Mare.Fuori.S01E01.iTA.mkv", "series": None, "episodes": [], "quality": {"q": 1}},
                {"path": f"{cartella}/Mare.Fuori.S01E02.iTA.mkv", "series": None, "episodes": [], "quality": {"q": 1}},
                # titolo riconosciuto da Sonarr
                {"path": f"{cartella}/The.Sea.Beyond.S01E03.mkv", "series": {"id": 7}, "episodes": [{"id": 103}]},
                {"path": f"{cartella}/leggimi.txt", "series": None, "episodes": []},
                {"path": f"{cartella}/sample.mkv", "rejections": [{"reason": "Sample"}]},
                {"path": "/data/downloads/cerca/Altro/E01.mkv", "series": {"id": 7}, "episodes": [{"id": 999}]},
            ])
        if percorso == "episode":
            return httpx.Response(200, json=[{"id": 100 + n, "seasonNumber": 1, "episodeNumber": n} for n in (1, 2, 3)])
        if percorso == "parse":
            n = int(r.url.params["title"].split("S01E")[1][:2])
            return httpx.Response(200, json={"parsedEpisodeInfo": {"seriesTitle": "Mare Fuori", "seasonNumber": 1,
                                                                   "episodeNumbers": [n]}, "series": None})
        if percorso == "command" and r.method == "POST":
            comandi.append(json.loads(r.content))
            return httpx.Response(201, json={"id": 5})
        if percorso == "command/5":
            return httpx.Response(200, json={"status": "completed"})
        return httpx.Response(404)

    s = Serie(Config(sonarr_url="http://sonarr:8989/sonarr", sonarr_chiave="chiave"),
              httpx.AsyncClient(transport=httpx.MockTransport(gestore)))
    assert asyncio.run(s.importa(cartella, 7)) == (3, str(cartella_serie))
    assert cartella_serie.is_dir()  # la cartella della serie c'è prima di importare
    file = comandi[0]["files"]
    assert comandi[0]["name"] == "ManualImport" and [f["episodeIds"] for f in file] == [[101], [102], [103]]
    assert all(f["seriesId"] == 7 for f in file)


def test_ricerca_delle_puntate_titolo_giusto_e_italiano_prima(tmp_path):
    from app.db import Archivio
    from app.prowlarr import Prowlarr

    richieste = []

    def gestore(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/indexer"):
            return httpx.Response(200, json=[{"id": 1, "enable": True}])
        richieste.append(dict(r.url.params.multi_items()))
        return httpx.Response(200, json=[
            {"title": "Mare Fuori S04E03 1080p", "protocol": "torrent", "seeders": 90, "guid": "a", "indexerId": 1},
            {"title": "Mare.Fuori.S04E03.iTA.WEB-DL", "protocol": "torrent", "seeders": 5, "guid": "b", "indexerId": 1},
            {"title": "Il Mare d'Inverno S01", "protocol": "torrent", "seeders": 50, "guid": "c", "indexerId": 1},
        ])

    config = Config(prowlarr_url="http://prowlarr:9696/prowlarr", prowlarr_chiave="p")
    p = Prowlarr(config, Archivio(tmp_path / "c.db"), httpx.AsyncClient(transport=httpx.MockTransport(gestore)))
    r = asyncio.run(p.cerca_serie(7, ["Mare fuori", "The Sea Beyond", "Mare fuori"]))
    assert [x["titolo"] for x in r["risultati"]] == ["Mare.Fuori.S04E03.iTA.WEB-DL", "Mare Fuori S04E03 1080p"]
    assert r["risultati"][0]["contenuto"] == "Stagione 4 · Episodio 3" and r["risultati"][0]["italiano"]
    assert {q["query"] for q in richieste} == {"Mare fuori", "The Sea Beyond"}
    assert all(q["categories"] == "5000" for q in richieste)
    assert p.risultato_originale(7, r["risultati"][0]["id"], "serie")["guid"] == "b"


def test_a_download_finito_le_puntate_vanno_a_sonarr_non_a_radarr(tmp_path):
    from app.db import Archivio
    from app.download import GestoreDownload

    class Finto:
        def __init__(self):
            self.chiamate = []

        async def importa(self, percorso, serie_id):
            self.chiamate.append((percorso, serie_id))
            return 3, "/data/media/serie/Mare Fuori (2020)"

        async def avvisa_nuovo(self, cartella):
            self.chiamate.append(("jellyfin", cartella))

        async def elimina(self, hash_torrent, con_file):
            self.chiamate.append(("elimina", hash_torrent))

    a = Archivio(tmp_path / "c.db")
    serie, qbit, radarr = Finto(), Finto(), Finto()
    notifiche = []

    async def notifica(titolo, testo, link):
        notifiche.append(testo)

    jellyfin = Finto()
    g = GestoreDownload(Config(), a, None, qbit, radarr, None, jellyfin)
    g.serie, g.notifica = serie, notifica
    id_d = a.nuovo_download(tmdb_id=None, titolo="Mare fuori · Stagione 1", fonte="prowlarr", tipo="torrent",
                            categoria="cerca", serie=7, hash="abc", percorso="/data/downloads/cerca/MF.S01",
                            stato="completato")
    asyncio.run(g._passa_a_radarr(id_d))
    assert serie.chiamate == [("/data/downloads/cerca/MF.S01", 7)] and radarr.chiamate == []
    assert qbit.chiamate == [("elimina", "abc")]
    assert jellyfin.chiamate == [("jellyfin", "/data/media/serie/Mare Fuori (2020)")]
    assert a.download(id_d)["stato"] == "importato" and notifiche == ["Mare fuori · Stagione 1: 3 puntate in Jellyfin."]


def test_archivio_vecchio_riceve_la_colonna_serie(tmp_path):
    import sqlite3

    from app.db import Archivio

    vecchio = sqlite3.connect(tmp_path / "c.db")
    vecchio.execute("CREATE TABLE download (id INTEGER PRIMARY KEY, tmdb_id INTEGER, titolo TEXT NOT NULL, anno TEXT, "
                    "poster TEXT, fonte TEXT NOT NULL, tipo TEXT NOT NULL, url TEXT, nome_file TEXT, percorso TEXT, "
                    "categoria TEXT, hash TEXT, stato TEXT NOT NULL, scaricati INTEGER DEFAULT 0, totale INTEGER DEFAULT 0, "
                    "velocita INTEGER DEFAULT 0, messaggio TEXT, creato REAL NOT NULL, aggiornato REAL NOT NULL)")
    vecchio.commit()
    vecchio.close()
    a = Archivio(tmp_path / "c.db")
    id_d = a.nuovo_download(titolo="x", fonte="manuale", tipo="torrent", serie=3)
    assert a.download(id_d)["serie"] == 3



def test_jellyfin_si_aggiorna_quando_sonarr_importa_da_solo():
    date = iter(["2026-11-20T10:00:00Z", "2026-11-20T10:00:00Z", "2026-11-20T11:00:00Z", "2026-11-20T11:00:00Z"])

    def gestore(r: httpx.Request) -> httpx.Response:
        assert r.url.params["eventType"] == "3"  # solo le importazioni
        return httpx.Response(200, json={"records": [{"date": next(date)}]})

    class JellyfinFinto:
        volte = 0

        async def aggiorna_libreria(self):
            JellyfinFinto.volte += 1

    s = Serie(Config(sonarr_url="http://sonarr:8989/sonarr", sonarr_chiave="chiave"),
              httpx.AsyncClient(transport=httpx.MockTransport(gestore)))

    async def prova():
        compito = asyncio.create_task(s.sorveglia_importazioni(JellyfinFinto(), ogni=0.01))
        await asyncio.sleep(0.2)
        compito.cancel()

    try:
        asyncio.run(prova())
    except StopIteration:
        pass
    assert JellyfinFinto.volte == 1  # non all'avvio, una volta quando arriva una puntata nuova
