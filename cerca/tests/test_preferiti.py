"""Brani preferiti: la musica scaricata, da Jellyfin, ascoltata intera passando da Cerca."""

import asyncio
import dataclasses
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from app.config import Config
from app.jellyfin import Jellyfin
from app.main import app

ID = "a" * 32
AUDIO = bytes(range(256)) * 40


def jellyfin_finto(richieste):
    def gestore(r: httpx.Request) -> httpx.Response:
        richieste.append(r)
        if r.url.path.endswith("/Users/AuthenticateByName"):
            return httpx.Response(200, json={"AccessToken": "t", "User": {"Id": "u1"}})
        if r.url.path == "/Users/u1/Items":
            return httpx.Response(200, json={"Items": [
                {"Id": ID, "Name": "Sabbie mobili", "Artists": ["Marracash"], "Album": "Status",
                 "RunTimeTicks": 2_000_000_000, "AlbumId": "b" * 32, "AlbumPrimaryImageTag": "x",
                 "DateCreated": "2026-11-21T10:00:00Z"},
                {"Id": "c" * 32, "Name": "Senza copertina", "AlbumArtist": "Qualcuno"},
            ]})
        if r.url.path == f"/Audio/{ID}/stream":
            inizio = int(r.headers["range"].split("=")[1].split("-")[0]) if "range" in r.headers else 0
            pezzo = AUDIO[inizio:]
            intestazioni = {"content-type": "audio/mpeg", "accept-ranges": "bytes", "content-length": str(len(pezzo))}
            if inizio:
                intestazioni["content-range"] = f"bytes {inizio}-{len(AUDIO) - 1}/{len(AUDIO)}"
            return httpx.Response(206 if inizio else 200, stream=httpx.ByteStream(pezzo), headers=intestazioni)
        return httpx.Response(404)
    return gestore


def nuovo_jellyfin(richieste):
    config = dataclasses.replace(Config(), jellyfin_url="http://jellyfin:8096")
    return Jellyfin(config, httpx.AsyncClient(transport=httpx.MockTransport(jellyfin_finto(richieste))))


def test_brani_dal_piu_recente_con_la_copertina_dell_album():
    brani = asyncio.run(nuovo_jellyfin([]).brani())
    assert brani[0] == {"id": ID, "titolo": "Sabbie mobili", "artista": "Marracash", "album": "Status",
                        "durata": 200, "copertina_jf": "b" * 32, "aggiunto": "2026-11-21"}
    assert brani[1]["artista"] == "Qualcuno" and brani[1]["copertina_jf"] == ""


def test_ascolto_intero_con_i_salti():
    richieste = []
    app.state.servizi = SimpleNamespace(jellyfin=nuovo_jellyfin(richieste))
    client = TestClient(app)
    tutto = client.get(f"/api/musica/ascolta/{ID}")
    assert tutto.status_code == 200 and tutto.content == AUDIO and tutto.headers["accept-ranges"] == "bytes"
    # il salto in avanti dell'iPhone: chiede un pezzo con "Range" e Jellyfin risponde 206
    pezzo = client.get(f"/api/musica/ascolta/{ID}", headers={"Range": "bytes=1000-"})
    assert pezzo.status_code == 206 and pezzo.content == AUDIO[1000:]
    assert pezzo.headers["content-range"] == f"bytes 1000-{len(AUDIO) - 1}/{len(AUDIO)}"
    assert all("Token=" in r.headers.get("authorization", "") for r in richieste if "/Audio/" in r.url.path)
    # solo id di Jellyfin: niente percorsi inventati
    assert client.get("/api/musica/ascolta/..%2F..%2FSystem").status_code in (404, 422)
    assert client.get("/api/musica/copertina/" + "d" * 32).status_code == 404
