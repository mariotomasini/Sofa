"""Test dei casi limite corretti: compiti in sottofondo, errori inattesi, valori scritti male."""

import asyncio
import gc
import warnings

from app.avvisami import Avvisami
from app.config import Config
from app.db import Archivio
from app.download import GestoreDownload
from app.main import dopo
from app.rete import ErroreServizio
from app.stato import ControlloDisco


def _gestore(tmp_path) -> GestoreDownload:
    config = Config(cartella_dati=tmp_path, cartella_download=tmp_path / "dl")
    return GestoreDownload(config, Archivio(tmp_path / "cerca.db"), None, None, None, None, None)


def test_i_compiti_in_sottofondo_non_spariscono(tmp_path):
    """asyncio tiene i compiti solo con un riferimento debole: il gestore deve tenerli in vita."""
    g = _gestore(tmp_path)
    finiti = []

    async def lavoro():
        await asyncio.sleep(0.01)
        finiti.append(1)

    async def prova():
        g._sottofondo(lavoro())
        gc.collect()
        assert len(g._in_sottofondo) == 1
        await asyncio.sleep(0.05)

    asyncio.run(prova())
    assert finiti == [1]
    assert not g._in_sottofondo  # finito: non resta in memoria


def test_un_errore_in_sottofondo_viene_registrato(tmp_path, caplog):
    g = _gestore(tmp_path)

    async def rotto():
        raise RuntimeError("boom")

    async def prova():
        g._sottofondo(rotto())
        await asyncio.sleep(0.01)

    asyncio.run(prova())
    assert "Errore in un compito dei download" in caplog.text


def test_un_errore_inatteso_non_lascia_il_download_in_corso(tmp_path):
    g = _gestore(tmp_path)
    id_d = g.archivio.nuovo_download(titolo="Film", fonte="archive", tipo="http", url="http://x",
                                     percorso=str(tmp_path / "dl" / "f.mp4"))

    async def scarica_rotto(_id):
        raise ValueError("risposta strana")

    g._scarica_http = scarica_rotto

    async def prova():
        g._avvia_http(id_d)
        await asyncio.sleep(0.01)

    asyncio.run(prova())
    voce = g.archivio.download(id_d)
    assert voce["stato"] == "errore" and "Riprova" in voce["messaggio"]
    assert id_d not in g._compiti


def test_dopo_si_spegne_senza_avvisi(recwarn):
    """Se Cerca si spegne prima del ritardo, il lavoro non partito non lascia "coroutine never awaited"."""
    async def lavoro():
        return None

    async def prova():
        compito = asyncio.create_task(dopo(60, lavoro()))
        await asyncio.sleep(0)
        compito.cancel()
        try:
            await compito
        except asyncio.CancelledError:
            pass

    with warnings.catch_warnings():
        warnings.simplefilter("always")
        asyncio.run(prova())
        gc.collect()
    assert not [w for w in recwarn if "never awaited" in str(w.message)]


def test_valori_scritti_male_nel_env(monkeypatch):
    monkeypatch.setenv("PULIZIA_GIORNI", "trenta")
    monkeypatch.setenv("SOGLIA_DISCO_LIBERO", "dieci")
    c = Config()
    assert c.pulizia_giorni == 30 and c.soglia_disco == 10.0
    monkeypatch.setenv("SOGLIA_DISCO_LIBERO", "7,5")
    assert Config().soglia_disco == 7.5


class _PuliziaRotta:
    async def libera(self, motivo):
        raise ErroreServizio("qBittorrent non risponde: per sicurezza non pulisco i download.")


class _Notifiche:
    def __init__(self):
        self.inviate = []

    async def notifica(self, titolo, messaggio, link=""):
        self.inviate.append(titolo)
        return True


def test_disco_pieno_avvisa_anche_se_la_pulizia_fallisce(tmp_path):
    notifiche = _Notifiche()
    config = Config(cartella_dati=tmp_path, cartella_download=tmp_path / "dl", soglia_disco=100.0)
    (tmp_path / "dl").mkdir()
    disco = ControlloDisco(config, Archivio(tmp_path / "cerca.db"), notifiche, _PuliziaRotta())
    asyncio.run(disco.controlla())
    assert notifiche.inviate == ["Disco quasi pieno"]


class _TmdbGratis:
    async def provider(self, tmdb_id, durata_cache=0):
        return {"gratis": [{"nome": "Pluto TV"}]}


def test_la_notifica_non_partita_si_riprova(tmp_path):
    a = Archivio(tmp_path / "cerca.db")
    a.avvisami_aggiungi(1, "Nosferatu", "1922", "")
    a.avvisami_imposta(1, 123.0, [{"nome": "Pluto TV"}], False)  # gratis, ma la notifica non era partita
    config = Config(tmdb_chiave="chiave", ntfy_topic="prova")
    av = Avvisami(config, a, _TmdbGratis(), None)
    inviate = []

    async def notifica(titolo, messaggio, link=""):
        inviate.append(messaggio)
        return True

    av.notifica = notifica
    asyncio.run(av.controlla())
    assert len(inviate) == 1
    assert a.avvisami_elenco()[0]["notificato"] == 1
    asyncio.run(av.controlla())
    assert len(inviate) == 1  # già avvisato: non si ripete
