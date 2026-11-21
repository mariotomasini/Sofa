import asyncio

from app.automatici import Automatici, lingua_da, percentuale
from app.config import Config


def test_lingua_da_formati_e_da_radarr():
    assert lingua_da([{"name": "Lingua · MULTI"}, {"name": "Lingua · Spagnolo"}]) == "Spagnolo"
    assert lingua_da([], [{"name": "Italian"}]) == "Italiano"
    assert lingua_da(None, None) == ""
    assert lingua_da([{"name": "Lingua · Spagnolo"}, {"name": "Lingua · Italiano"}]) == "Italiano"
    assert lingua_da([{"name": "Lingua · Spagnolo"}, {"name": "Lingua · Italiano"}], ordine=("es", "it")) == "Spagnolo"
    assert percentuale(200, 50) == 75.0 and percentuale(0, 0) == 0.0


class FintoRadarr:
    async def _r(self, metodo, percorso, **kw):
        if percorso == "queue":
            return {"records": [
                {"movie": {"title": "Dune", "year": 2024}, "title": "Dune.2024.SPANISH.1080p", "size": 100,
                 "sizeleft": 40, "status": "downloading", "customFormats": [{"name": "Lingua · Spagnolo"}]},
                {"movie": {"title": "Alien", "year": 1979}, "title": "Alien.1979.iTALiAN.1080p", "size": 10,
                 "sizeleft": 0, "customFormats": [{"name": "Lingua · Italiano"}]},
            ]}
        if percorso == "wanted/cutoff":
            return {"records": [
                {"title": "Heat", "year": 1995,
                 "movieFile": {"relativePath": "Heat.mkv", "customFormats": [{"name": "Lingua · Inglese"}]}},
                {"title": "Up", "year": 2009, "movieFile": {"customFormats": [{"name": "Lingua · Italiano"}]}},
            ]}
        return None


class FintaSerie:
    attivo = False


def test_coda_e_attesa(monkeypatch):
    monkeypatch.setenv("LINGUE_AUDIO", "it,es,en")
    a = Automatici(Config(), FintoRadarr(), FintaSerie())
    coda = asyncio.run(a.coda())
    assert [(v["titolo"], v["lingua"], v["preferita"]) for v in coda] == [
        ("Dune (2024)", "Spagnolo", False), ("Alien (1979)", "Italiano", True)]
    assert coda[0]["percentuale"] == 60.0
    attesa = asyncio.run(a.in_attesa())
    assert [(v["titolo"], v["lingua"]) for v in attesa] == [("Heat (1995)", "Inglese")]


def test_lingua_preferita_cambia(monkeypatch):
    monkeypatch.setenv("LINGUE_AUDIO", "es,it")
    assert Automatici(Config(), FintoRadarr(), FintaSerie()).preferita == "Spagnolo"


def test_stato_in_parole_semplici():
    from app.automatici import stato_coda

    assert stato_coda({"status": "downloading"}) == ("download", "In download")
    assert stato_coda({"status": "queued"})[0] == "coda"
    assert stato_coda({"status": "delay"})[1] == "Trovato: parte tra poco"
    assert stato_coda({"status": "completed", "trackedDownloadState": "importPending"})[0] == "importazione"
    problema = stato_coda({"trackedDownloadStatus": "warning", "statusMessages": [{"messages": ["Nessun seed"]}]})
    assert problema == ("problema", "Problema: Nessun seed")


def test_velocita_tempo_e_fonte():
    from app.automatici import fonte, secondi

    assert secondi("01:02:03") == 3723 and secondi("1.00:00:10") == 86410 and secondi(None) == 0
    assert fonte({"downloadClient": "Telegram (tgarr)"}) == "Telegram"
    assert fonte({"protocol": "torrent", "downloadClient": "qBittorrent"}) == "Torrent"

    class Qbit:
        async def tutti(self):
            return [{"hash": "abc", "dlspeed": 2_000_000}]

    a = Automatici(Config(), FintoRadarr(), FintaSerie(), Qbit())
    asyncio.run(a._leggi_qbit())
    torrent = a._da_coda({"downloadId": "ABC", "status": "downloading", "size": 100, "sizeleft": 50}, "X", "film", [1])
    assert torrent["velocita"] == 2_000_000 and torrent["fonte"] == "Torrent"
    telegram = a._da_coda({"downloadId": "t1", "status": "downloading", "size": 1000, "sizeleft": 600,
                           "timeleft": "00:01:00", "downloadClient": "Telegram (tgarr)"}, "Y", "film", [2])
    assert telegram["velocita"] == 10 and telegram["rimanente"] == 60 and telegram["fonte"] == "Telegram"


def test_lingua_dalle_tracce_audio_vere():
    from app.automatici import tracce_audio
    assert tracce_audio({"audioLanguages": "eng/ita"}) == ["Inglese", "Italiano"]
    assert tracce_audio({"audioLanguages": ""}) == [] and tracce_audio(None) == []
    # un "MULTi" con dentro l'italiano è italiano
    assert lingua_da([{"name": "Lingua · MULTI (lingue non scritte)"}], media={"audioLanguages": "eng/ita"}) == "Italiano"
    assert lingua_da([{"name": "Lingua · MULTI (lingue non scritte)"}]) == ""


def test_controlla_tracce_ferma_solo_i_multi_con_la_prima_lingua():
    import asyncio
    from types import SimpleNamespace

    file_multi_ita = {"customFormats": [{"name": "Lingua · MULTI (lingue non scritte)"}],
                      "mediaInfo": {"audioLanguages": "eng/ita"}}
    file_multi_eng = {"customFormats": [{"name": "Lingua · MULTI (lingue non scritte)"}],
                      "mediaInfo": {"audioLanguages": "eng/fre"}}
    file_ita_720p = {"customFormats": [{"name": "Lingua · Italiano"}], "mediaInfo": {"audioLanguages": "ita"}}
    chiamate = []

    class Finto:
        attivo = True

        async def _r(self, metodo, percorso, **kw):
            chiamate.append((metodo, percorso, kw.get("json")))
            if percorso == "wanted/cutoff":
                return {"records": [{"id": 1, "movieFile": file_multi_ita}, {"id": 2, "movieFile": file_multi_eng},
                                    {"id": 3, "movieFile": file_ita_720p}]}

        async def _api(self, metodo, percorso, **kw):
            chiamate.append((metodo, percorso, kw.get("json")))
            if percorso == "wanted/cutoff":
                return {"records": [{"id": 10, "episodeFile": file_multi_ita}, {"id": 11, "episodeFile": {}}]}

    a = Automatici(SimpleNamespace(lingue_audio=("it", "es", "en")), Finto(), Finto())
    assert asyncio.run(a.controlla_tracce()) == 2
    assert ("PUT", "movie/editor", {"movieIds": [1], "monitored": False}) in chiamate
    assert ("PUT", "episode/monitor", {"episodeIds": [10], "monitored": False}) in chiamate
