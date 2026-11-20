import asyncio

from app.automatici import Automatici, lingua_da, percentuale
from app.config import Config


def test_lingua_da_formati_e_da_radarr():
    assert lingua_da([{"name": "Lingua · MULTI"}, {"name": "Lingua · Spagnolo"}]) == "Spagnolo"
    assert lingua_da([], [{"name": "Italian"}]) == "Italiano"
    assert lingua_da(None, None) == ""
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
