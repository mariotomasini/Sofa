"""Cache di Cerca: riserva quando un servizio non risponde, limite di spazio, cache del browser."""

import asyncio
import time

import httpx

from app import catalogo_libero
from app.catalogo_libero import CatalogoLibero
from app.config import Config
from app.db import Archivio
from app.main import CARTELLA, impronta_statici, regole_cache

catalogo_libero._justwatch.intervallo = 0


def test_riserva_pulizia_e_limite(tmp_path):
    a = Archivio(tmp_path / "cerca.db")
    a.cache_scrivi("fresca", {"x": 1}, 3600)
    a.cache_scrivi("scaduta", {"x": 2}, -60)  # scaduta da un minuto
    a.cache_scrivi("vecchissima", {"x": 3}, -8 * 24 * 3600)  # scaduta da più di una settimana
    assert a.cache_leggi("scaduta") is None
    assert a.cache_leggi("scaduta", scadute=True) == {"x": 2}  # la riserva
    assert a.cache_pulisci() == 1
    assert a.cache_leggi("vecchissima", scadute=True) is None and a.cache_leggi("fresca") == {"x": 1}

    for i in range(20):
        a.cache_scrivi(f"grande{i}", "x" * 100_000, 3600 + i)  # ~2 MB in tutto
    prima = a.cache_dimensione()
    tolte = a.cache_limita(mb=1)
    assert tolte > 0 and a.cache_dimensione() <= 1024 * 1024 < prima
    assert a.cache_leggi("grande19") is not None  # restano quelle che scadono più tardi
    assert a.cache_limita(mb=1) == 0


def test_se_il_servizio_non_risponde_si_usa_la_risposta_di_ieri(tmp_path):
    a = Archivio(tmp_path / "cerca.db")
    giu = {"si": False}

    def gestore(r: httpx.Request) -> httpx.Response:
        if giu["si"]:
            raise httpx.ConnectError("niente rete")
        nodo = {"node": {"objectType": "MOVIE", "content": {
            "title": "Film di ieri", "originalTitle": "Yesterday", "originalReleaseYear": 2026, "runtime": 100,
            "posterUrl": "/poster/1/{profile}/x.{format}", "externalIds": {"tmdbId": "1", "imdbId": "tt1"}}}}
        return httpx.Response(200, json={"data": {"popularTitles": {"edges": [nodo]}}})

    async def prova():
        c = CatalogoLibero(Config(radarr_url="http://radarr", radarr_chiave="x", tmdb_chiave=""), a,
                           httpx.AsyncClient(transport=httpx.MockTransport(gestore)))
        assert [f["titolo"] for f in await c.top_settimana("movie")] == ["Film di ieri"]
        # la risposta scade, la memoria si svuota e JustWatch smette di rispondere
        a._esegui("UPDATE cache SET scade = ?", (time.time() - 3600,))
        c._memoria.clear()
        giu["si"] = True
        return await c.top_settimana("movie")

    assert [f["titolo"] for f in asyncio.run(prova())] == ["Film di ieri"]


def test_cache_del_browser():
    assert regole_cache("/static/app.js", versionato=True) == "public, max-age=31536000, immutable"
    assert regole_cache("/static/fonts/x.woff2", versionato=False) == "public, max-age=604800"
    assert regole_cache("/api/home", versionato=False) == "no-cache"
    v = impronta_statici(CARTELLA / "static")
    assert len(v) == 10 and v == impronta_statici(CARTELLA / "static")
    base = (CARTELLA / "templates" / "base.html").read_text()
    assert "style.css?v={{ v }}" in base and "app.js?v={{ v }}" in base
