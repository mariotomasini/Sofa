"""Test del controllo servizi e delle attese brevi quando un servizio è spento."""

import asyncio
import time

import httpx
import pytest

from app import catalogo_libero, diagnosi
from app.catalogo_libero import CatalogoLibero
from app.config import Config
from app.rete import ErroreServizio


class ArchivioFinto:
    def cache_leggi(self, chiave):
        return None

    def cache_scrivi(self, chiave, valore, durata):
        pass


def config(**altro):
    return Config(radarr_url="http://radarr:7878/radarr", radarr_chiave="r", prowlarr_url="http://prowlarr:9696/prowlarr",
                  prowlarr_chiave="p", qbit_url="http://qbittorrent:8080", jellyfin_url="http://jellyfin:8096/jellyfin",
                  sonarr_url="", lidarr_url="", utente="admin", password="giusta", tmdb_chiave="", **altro)


def test_controllo_servizi():
    def gestore(r: httpx.Request) -> httpx.Response:
        host, percorso = r.url.host, r.url.path
        if host == "radarr":
            raise httpx.ConnectError("spento")
        if host == "prowlarr":
            return httpx.Response(401)
        if host == "jellyfin" and percorso.endswith("/Users/AuthenticateByName"):
            return httpx.Response(401)
        return httpx.Response(200, json={})

    async def prova():
        async with httpx.AsyncClient(transport=httpx.MockTransport(gestore)) as client:
            return await diagnosi.controlla(config(), client)

    d = asyncio.run(prova())
    per_nome = {r["nome"]: r for r in d["risultati"]}
    assert not d["tutto_ok"]
    assert per_nome["Radarr"]["stato"] == "errore" and "mediabox logs radarr" in per_nome["Radarr"]["cosa_fare"]
    assert per_nome["qBittorrent"]["stato"] == "ok"
    assert per_nome["Prowlarr"]["stato"] == "errore" and "chiave" in per_nome["Prowlarr"]["messaggio"]
    assert per_nome["Jellyfin"]["stato"] == "errore" and "azzera-jellyfin" in per_nome["Jellyfin"]["cosa_fare"]
    assert {"Wikidata", "TVmaze", "Internet Archive"} <= set(per_nome)  # senza chiave TMDB
    assert "TMDB" not in per_nome and "Sonarr" not in per_nome


def test_radarr_spento_non_fa_aspettare_ogni_volta():
    chiamate = []

    def gestore(r: httpx.Request) -> httpx.Response:
        chiamate.append(r.url.path)
        raise httpx.ConnectError("spento")

    async def prova():
        async with httpx.AsyncClient(transport=httpx.MockTransport(gestore)) as client:
            c = CatalogoLibero(config(), ArchivioFinto(), client)
            with pytest.raises(ErroreServizio) as primo:
                await c.cerca("matrix")
            with pytest.raises(ErroreServizio):
                await c.dettagli(603)  # Radarr è appena risultato spento: non lo richiamiamo
            return primo.value

    errore = asyncio.run(prova())
    assert len(chiamate) == 1
    assert "Radarr non risponde" in errore.cosa and "Controllo servizi" in errore.cosa_fare


def test_dopo_la_pausa_si_riprova(monkeypatch):
    adesso = [1000.0]
    monkeypatch.setattr(catalogo_libero.time, "monotonic", lambda: adesso[0])
    chiamate = []

    def gestore(r: httpx.Request) -> httpx.Response:
        chiamate.append(1)
        raise httpx.ConnectError("spento")

    async def prova():
        async with httpx.AsyncClient(transport=httpx.MockTransport(gestore)) as client:
            c = CatalogoLibero(config(), ArchivioFinto(), client)
            for _ in range(2):
                with pytest.raises(ErroreServizio):
                    await c.cerca("x")
                adesso[0] += catalogo_libero.PAUSA_DOPO_ERRORE + 1

    asyncio.run(prova())
    assert len(chiamate) == 2


def test_jellyfin_lento_non_blocca_la_home():
    from app import home

    async def lentissimo():
        await asyncio.sleep(30)

    inizio = time.monotonic()
    risultato = asyncio.run(home._senza_errori(lentissimo(), "vuoto", secondi=0.2))
    assert risultato == "vuoto" and time.monotonic() - inizio < 2
