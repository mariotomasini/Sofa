import asyncio
import json

from app.config import Config
from app.db import Archivio
from app.puntate_italiane import PuntateItaliane, ricorda_titoli, scegli, titoli_ricordati


def ris(id_, titolo, contenuto, seed=20, poche=False):
    return {"id": id_, "titolo": titolo, "contenuto": contenuto, "seed": seed, "protocollo": "torrent",
            "poche_fonti": poche}


def test_scegli_puntata_singola_solo_in_italiano_e_con_fonti():
    risultati = [
        ris("a", "The.Sea.Beyond.S05E01.1080p", "Stagione 5 · Episodio 1"),  # inglese
        ris("b", "Mare.Fuori.S05E01.iTALiAN.1080p", "Stagione 5 · Episodio 1", poche=True),  # senza fonti
        ris("c", "Mare.Fuori.S05E01.ITA.720p", "Stagione 5 · Episodio 1"),
        ris("d", "Mare.Fuori.S05E02.ITA.720p", "Stagione 5 · Episodio 2"),
    ]
    assert scegli(risultati, [(101, 5, 1)], "it", set()) == [(risultati[2], [101])]
    # un torrent già provato non si riprende
    assert scegli(risultati, [(101, 5, 1)], "it", {"c"}) == []


def test_scegli_stagione_intera_se_ne_mancano_piu_puntate():
    pacchetto = ris("s", "Mare.Fuori.S04.ITA.1080p", "Stagione 4")
    singola = ris("e", "Mare.Fuori.S04E03.ITA.1080p", "Stagione 4 · Episodio 3")
    scelti = scegli([singola, pacchetto], [(1, 4, 2), (2, 4, 3)], "it", set())
    assert scelti == [(pacchetto, [1, 2])]
    # con una sola puntata mancante, la puntata e non la stagione
    assert scegli([pacchetto, singola], [(2, 4, 3)], "it", set()) == [(singola, [2])]


def test_titoli_ricordati_italiano_prima(tmp_path):
    a = Archivio(tmp_path / "c.db")
    ricorda_titoli(a, 7, ["", "The Sea Beyond"])
    ricorda_titoli(a, 7, ["Mare fuori", "The Sea Beyond"])
    assert titoli_ricordati(a, 7) == ["Mare fuori", "The Sea Beyond"]
    assert titoli_ricordati(a, 8) == []


class FintaSerie:
    attivo = True

    async def _api(self, metodo, percorso, **kw):
        if percorso == "queue":
            return {"records": [{"episodeId": 3}], "totalRecords": 1}
        if percorso == "wanted/missing":
            serie = {"id": 7, "title": "The Sea Beyond", "alternateTitles": [],
                     "images": [{"coverType": "poster", "remoteUrl": "http://p/x.jpg"}]}
            return {"records": [
                {"id": 1, "seriesId": 7, "seasonNumber": 5, "episodeNumber": 1, "series": serie},
                {"id": 2, "seriesId": 7, "seasonNumber": 0, "episodeNumber": 1, "series": serie},  # speciale
                {"id": 3, "seriesId": 7, "seasonNumber": 5, "episodeNumber": 2, "series": serie},  # già in coda
            ], "totalRecords": 3}
        raise AssertionError(percorso)


class FintoProwlarr:
    def __init__(self):
        self.titoli = None

    async def cerca_serie(self, serie_id, titoli):
        self.titoli = titoli
        return {"risultati": [ris("x", "Mare.Fuori.S05E01.iTALiAN.1080p", "Stagione 5 · Episodio 1")]}

    def risultato_originale(self, serie_id, id_ris, tipo):
        return {"magnetUrl": "magnet:?xt=urn:btih:abc"}

    async def scarica_torrent(self, originale):
        return originale["magnetUrl"], None


class FintoDownload:
    def __init__(self):
        self.avviati = []

    async def da_torrent(self, **kw):
        self.avviati.append(kw)
        return 1


def test_giro_scarica_la_puntata_nuova_e_non_la_ripete(tmp_path, monkeypatch):
    monkeypatch.setenv("PROWLARR_API_KEY", "k")
    a = Archivio(tmp_path / "c.db")
    ricorda_titoli(a, 7, ["Mare fuori", "The Sea Beyond"])
    prowlarr, download = FintoProwlarr(), FintoDownload()
    p = PuntateItaliane(Config(), a, FintaSerie(), prowlarr, download)
    assert asyncio.run(p.giro()) == 1
    assert prowlarr.titoli[0] == "Mare fuori"
    kw = download.avviati[0]
    assert kw["serie"] == 7 and kw["magnet"].startswith("magnet:") and kw["poster"] == "http://p/x.jpg"
    assert kw["titolo"] == "Mare fuori · Stagione 5 · Episodio 1"
    assert "1" in json.loads(a.stato_leggi("puntate_italiane"))["avviate"]
    # al giro dopo la puntata è già avviata: niente doppioni
    assert asyncio.run(p.giro()) == 0 and len(download.avviati) == 1
