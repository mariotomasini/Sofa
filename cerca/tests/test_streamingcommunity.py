"""StreamingCommunity: lettura delle pagine, scelta dei flussi HLS, collegamento alle serie, ffmpeg."""

import asyncio
import html
import json
import shutil
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.animeunity import TUTTE_LE_STAGIONI, puntata_sonarr, scegli_collegamenti
from app.config import Config
from app.streamingcommunity import (
    StreamingCommunity,
    durata_playlist,
    estrai_data_page,
    estrai_iframe,
    leggi_id_puntata,
    normalizza,
    playlist_vixcloud,
    scegli_flussi,
)

LETTORE = """<script>
window.video = {"id":123,"name":"Dark"};
window.masterPlaylist = {
    params: {
        'token': 'abc',
        'expires': '1790000000',
    },
    url: 'https://vixcloud.test/playlist/123?b=1',
}
window.canPlayFHD = true
</script>"""

MASTER = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="English",LANGUAGE="eng",DEFAULT=YES,URI="/playlist/123/audio/eng"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Italiano",LANGUAGE="ita",URI="/playlist/123/audio/ita"
#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=854x480,AUDIO="audio"
/playlist/123/480p
#EXT-X-STREAM-INF:BANDWIDTH=4500000,RESOLUTION=1920x1080,AUDIO="audio"
/playlist/123/1080p
"""


def test_lettura_delle_pagine():
    pagina = f'<div id="app" data-page="{html.escape(json.dumps({"version": "v9", "props": {}}))}"></div>'
    assert estrai_data_page(pagina)["version"] == "v9"
    assert estrai_iframe('<iframe src="https://vixcloud.test/embed/123?token=x&amp;b=1">') == \
        "https://vixcloud.test/embed/123?token=x&b=1"
    assert playlist_vixcloud(LETTORE) == "https://vixcloud.test/playlist/123?b=1&token=abc&expires=1790000000&h=1"
    flussi = scegli_flussi(MASTER, "https://vixcloud.test/playlist/123?b=1")
    assert flussi == {"video": "https://vixcloud.test/playlist/123/1080p",
                      "audio": "https://vixcloud.test/playlist/123/audio/ita", "banda": 4500000}
    assert durata_playlist("#EXTINF:4.0,\na.ts\n#EXTINF:3.5,\nb.ts") == 7.5
    assert leggi_id_puntata("t12e34") == (12, 34) and leggi_id_puntata("t12") == (12, None)


def test_collegamento_serie_con_le_stagioni_di_sonarr():
    voci = [normalize for normalize in map(normalizza, [
        {"id": 1, "slug": "dark", "name": "Dark", "type": "tv", "last_air_date": "2020-06-27"},
        {"id": 2, "slug": "dark-matter", "name": "Dark Matter", "type": "tv", "last_air_date": "2024-05-08"},
        {"id": 3, "slug": "dark-film", "name": "Dark", "type": "movie", "release_date": "2017-01-01"},
    ])]
    scelti = scegli_collegamenti(voci, ["Dark"], {1: 10, 2: 8, 3: 8}, assoluta=False, italiano=True, anno="2017")
    assert [(v["id"], v["stagione"]) for v in scelti] == [(1, TUTTE_LE_STAGIONI)]
    episodi = [{"id": 9, "seasonNumber": 2, "episodeNumber": 3}]
    assert puntata_sonarr(episodi, TUTTE_LE_STAGIONI, 3, 2)["id"] == 9
    assert puntata_sonarr(episodi, TUTTE_LE_STAGIONI, 3, 1) is None


def sito_finto():
    data_page = html.escape(json.dumps({"version": "v9", "props": {}}))

    def gestore(r: httpx.Request) -> httpx.Response:
        p = r.url.path
        if r.url.host == "sc.test":
            if p in ("/", "/it"):
                if p == "/":
                    return httpx.Response(302, headers={"Location": "https://sc.test/it"})
                return httpx.Response(200, text=f'<div id="app" data-page="{data_page}"></div>')
            if p == "/it/api/search":
                return httpx.Response(200, json={"data": [
                    {"id": 1, "slug": "dark", "name": "Dark", "type": "tv", "last_air_date": "2020-06-27"},
                    {"id": 5, "slug": "nosferatu", "name": "Nosferatu", "type": "movie", "release_date": "2024-12-25",
                     "tmdb_id": 426063}]})
            if p == "/it/titles/1-dark":
                assert r.headers["X-Inertia-Version"] == "v9"
                return httpx.Response(200, json={"props": {"title": {"slug": "dark", "seasons": [{"number": 1}]}}},
                                      headers={"content-type": "application/json"})
            if p == "/it/titles/1-dark/season-1":
                return httpx.Response(200, json={"props": {"loadedSeason": {"episodes": [
                    {"id": 101, "number": 1}, {"id": 102, "number": 2}]}}}, headers={"content-type": "application/json"})
            if p == "/it/iframe/1":
                assert r.url.params["episode_id"] == "102"
                return httpx.Response(200, text='<iframe src="https://vixcloud.test/embed/123?token=x">')
        if r.url.host == "vixcloud.test":
            if p == "/embed/123":
                return httpx.Response(200, text=LETTORE)
            if p == "/playlist/123":
                assert r.url.params["token"] == "abc"
                return httpx.Response(200, text=MASTER)
            if p == "/playlist/123/1080p":
                return httpx.Response(200, text="#EXTM3U\n#EXTINF:600.0,\na.ts\n#EXTINF:600.0,\nb.ts\n")
        return httpx.Response(404)

    sc = StreamingCommunity(Config(streamingcommunity_url="https://sc.test"))
    sc._client = httpx.AsyncClient(transport=httpx.MockTransport(gestore), follow_redirects=True)
    return sc


def test_il_sito_con_la_lingua_nell_indirizzo():
    sc = sito_finto()

    async def prova():
        voci = await sc.cerca("Dark")
        assert [(v["id"], v["tipo"], v["doppiato"]) for v in voci] == [(1, "TV", True), (5, "Movie", True)]
        assert await sc.puntate(1) == [{"id": "t1e101", "numero": "1", "stagione": 1},
                                       {"id": "t1e102", "numero": "2", "stagione": 1}]
        url, intestazioni = await sc.link_video("t1e102")
        flussi = json.loads(url.removeprefix("hls:"))
        assert flussi["audio"].endswith("/audio/ita") and flussi["durata"] == 1200.0
        assert intestazioni["Referer"] == "https://vixcloud.test/embed/123?token=x"
        film = await sc.trova_film({"id": 426063, "titolo": "Nosferatu", "titolo_originale": "Nosferatu", "anno": "2024"})
        assert film["id"] == 5

    asyncio.run(prova())


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="serve ffmpeg")
def test_ffmpeg_scarica_video_e_audio_italiano(tmp_path):
    """Uno stream HLS vero (generato qui) con l'audio a parte: ne esce un MKV con video e audio."""
    from app.db import Archivio
    from app.download import GestoreDownload

    sito = tmp_path / "sito"
    sito.mkdir()
    base = ["ffmpeg", "-loglevel", "error", "-f", "lavfi"]
    subprocess.run([*base, "-i", "testsrc=size=160x90:rate=10", "-t", "4", "-c:v", "libx264", "-f", "hls",
                    "-hls_time", "2", "-hls_playlist_type", "vod", str(sito / "video.m3u8")], check=True)
    subprocess.run([*base, "-i", "sine=frequency=440", "-t", "4", "-c:a", "aac", "-f", "hls",
                    "-hls_time", "2", "-hls_playlist_type", "vod", str(sito / "ita.m3u8")], check=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(sito)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    indirizzo = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        archivio = Archivio(tmp_path / "a.db")
        g = GestoreDownload(Config(cartella_download=tmp_path), archivio, None, None, None, None, None)
        id_d = archivio.nuovo_download(titolo="Prova", fonte="streamingcommunity", tipo="http", url="", totale=0)
        parziale = tmp_path / "prova.mkv.part"
        asyncio.run(g._scarica_hls(id_d, parziale, {"video": f"{indirizzo}/video.m3u8", "audio": f"{indirizzo}/ita.m3u8",
                                                    "durata": 4, "banda": 100_000}, {"Referer": "https://x/"}))
    finally:
        server.shutdown()
    tracce = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                             str(parziale)], capture_output=True, text=True, check=True).stdout.split()
    assert sorted(tracce) == ["audio", "video"]
    assert archivio.download(id_d)["scaricati"] == parziale.stat().st_size > 0


def test_film_prima_il_link_diretto_poi_radarr(tmp_path):
    from types import SimpleNamespace

    from app.db import Archivio
    from app.main import _film_diretto

    avviati = []

    class Radarr:
        def __init__(self, con_file):
            self.con_file = con_file

        async def _r(self, metodo, percorso, **kw):
            return [{"hasFile": self.con_file}] if self.con_file is not None else []

    class Tmdb:
        async def dettagli(self, tmdb_id):
            return {"id": tmdb_id, "titolo": "Nosferatu", "titolo_originale": "Nosferatu", "anno": "2024", "poster": ""}

    class Download:
        async def da_diretto_film(self, film, fonti, nome):
            avviati.append((film["id"], fonti, nome))

    def servizi(con_file):
        return SimpleNamespace(siti_anime={"streamingcommunity": sito_finto()}, radarr=Radarr(con_file), tmdb=Tmdb(),
                               download=Download(), archivio=Archivio(tmp_path / "a.db"))

    r = asyncio.run(_film_diretto(servizi(None), 426063))
    assert "link diretto" in r["messaggio"]
    assert avviati == [(426063, [["streamingcommunity", "t5"]], "Nosferatu (2024) ITA.mkv")]
    # c'è già in Radarr con il file: niente link diretto, Radarr cerca solo di meglio
    assert asyncio.run(_film_diretto(servizi(True), 426063)) is None
    spento = servizi(None)
    spento.siti_anime["streamingcommunity"].config = Config(streamingcommunity_url="")
    assert asyncio.run(_film_diretto(spento, 426063)) is None
