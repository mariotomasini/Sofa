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


def test_le_pagine_non_aspettano_i_lavori_in_sottofondo(tmp_path):
    """Un thread che scrive a lungo nell'archivio non deve fermare le letture del ciclo principale."""
    import threading

    a = Archivio(tmp_path / "cerca.db")
    a.cache_scrivi("pagina", {"ok": True}, 3600)
    iniziato, finito = threading.Event(), threading.Event()

    def scrittura_lunga():
        conn = a._conn
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR REPLACE INTO cache (chiave, valore, scade) VALUES ('grande', '1', 0)")
        iniziato.set()
        time.sleep(1.5)
        conn.execute("COMMIT")
        finito.set()

    t = threading.Thread(target=scrittura_lunga)
    t.start()
    iniziato.wait()
    inizio = time.monotonic()
    assert a.cache_leggi("pagina") == {"ok": True}
    assert a.elenco_download(10) == []
    assert time.monotonic() - inizio < 0.3 and not finito.is_set()  # letto mentre l'altro scriveva
    t.join()
    a.cache_scrivi("dopo", 1, 60)  # e le scritture ripartono appena l'altro ha finito
    assert a.cache_leggi("dopo") == 1


def test_la_sentinella_salva_dove_si_era_bloccato(tmp_path):
    from app.sentinella import Sentinella, ultimo_blocco

    uscite = []
    s = Sentinella(avviso=0.01, riavvio=0.05, esci=uscite.append, cartella=tmp_path)
    s.ultimo = time.monotonic() - 0.02
    assert s.controlla() == "avviso"
    dati = ultimo_blocco(tmp_path)
    assert dati["secondi"] == 0 and not dati["riavviato"] and "quando" in dati
    s.ultimo = time.monotonic() - 1
    assert s.controlla() == "riavvio" and uscite == [1]
    assert ultimo_blocco(tmp_path)["riavviato"] is True
    assert ultimo_blocco(tmp_path / "niente") is None


def test_il_controllo_giornaliero_non_gira_a_vuoto(tmp_path, monkeypatch):
    """Senza chiave TMDB il controllo di "avvisami" finisce subito: il ciclo deve comunque fermarsi."""
    from app import avvisami as modulo
    from app.avvisami import Avvisami

    vero_sleep = asyncio.sleep
    attese = []

    async def sleep_finto(secondi):
        attese.append(secondi)
        await vero_sleep(0)

    monkeypatch.setattr(modulo.asyncio, "sleep", sleep_finto)
    a = Archivio(tmp_path / "cerca.db")
    config = Config(radarr_url="http://radarr", radarr_chiave="x", tmdb_chiave="")
    controlli = []
    av = Avvisami(config, a, None, None)
    vero_controlla = av.controlla

    async def controlla_contato():
        controlli.append(1)
        if len(controlli) > 5:  # il ciclo gira a vuoto: lo interrompiamo invece di bloccare il test
            raise RuntimeError("ciclo a vuoto")
        return await vero_controlla()

    av.controlla = controlla_contato

    async def prova():
        compito = asyncio.create_task(av.ciclo())
        battiti = 0
        for _ in range(200):  # il resto di Cerca deve poter lavorare
            await vero_sleep(0)
            battiti += 1
        compito.cancel()
        return battiti

    assert asyncio.run(prova()) == 200
    assert len(controlli) == 1  # un controllo, poi l'attesa fino al giorno dopo
    assert any(s >= 60 for s in attese[1:])
