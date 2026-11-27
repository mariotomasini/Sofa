"""Avvisi quando Radarr e Sonarr trovano da soli la versione in italiano."""

import asyncio

from app.avvisi_italiano import AvvisiItaliano, puntate_testo
from app.config import Config

ITA = [{"name": "Lingua · Italiano"}]
ENG = [{"name": "Lingua · Inglese"}]


class Archivio:
    def __init__(self, hash_di_cerca=()):
        self.stato = {}
        self.hash_di_cerca = set(hash_di_cerca)

    def stato_leggi(self, chiave):
        return self.stato.get(chiave)

    def stato_scrivi(self, chiave, valore):
        self.stato[chiave] = valore

    def download_per_hash(self, h):
        return {"id": 1} if h in self.hash_di_cerca else None


class Finto:
    """Radarr o Sonarr: risponde alla cronologia con i record dati (dal più nuovo)."""

    def __init__(self, record=(), attivo=True):
        self.record = list(record)
        self.attivo = attivo

    async def _r(self, metodo, percorso, params=None, json=None):
        assert percorso == "history"
        dal_piu_nuovo = sorted(self.record, key=lambda r: -r["id"])
        inizio = (params["page"] - 1) * params["pageSize"]
        return {"records": dal_piu_nuovo[inizio:inizio + params["pageSize"]], "totalRecords": len(self.record)}

    _api = _r


def puntata(id_, evento, episodio, formati, serie=7, **altro):
    return {"id": id_, "eventType": evento, "seriesId": serie, "episodeId": 100 + episodio, "customFormats": formati,
            "series": {"title": "Yaga"}, "episode": {"seasonNumber": 1, "episodeNumber": episodio}, **altro}


def prepara(radarr=(), sonarr=(), hash_di_cerca=()):
    inviate = []

    async def notifica(titolo, messaggio, link=""):
        inviate.append((titolo, messaggio))
        return True

    archivio = Archivio(hash_di_cerca)
    r, s = Finto(), Finto()
    a = AvvisiItaliano(Config(), archivio, r, s, notifica)
    asyncio.run(a.giro())  # primo giro: segna dove si è, senza avvisare
    r.record, s.record = list(radarr), list(sonarr)
    return a, inviate


def test_puntate_testo():
    assert puntate_testo([(1, 3), (1, 4), (1, 5), (1, 7), (2, 1)]) == "St. 1 Ep. 3-5, 7 · St. 2 Ep. 1"
    assert puntate_testo([(1, 7)]) == "St. 1 Ep. 7"


def test_primo_giro_non_avvisa_del_passato():
    inviate = []

    async def notifica(titolo, messaggio, link=""):
        inviate.append(titolo)

    archivio = Archivio()
    a = AvvisiItaliano(Config(), archivio, Finto([{"id": 5, "eventType": "grabbed", "movieId": 1, "customFormats": ITA}]),
                       Finto(), notifica)
    assert asyncio.run(a.giro()) == 0 and not inviate
    assert archivio.stato["avvisi_italiano:ultimo:radarr"] == "5"


def test_puntate_trovate_raggruppate_per_serie():
    a, inviate = prepara(sonarr=[puntata(1, "grabbed", e, ITA) for e in (3, 4, 5)]
                         + [puntata(4, "grabbed", 6, ENG)])
    asyncio.run(a.giro())
    assert inviate == [("Trovata in italiano", "Yaga St. 1 Ep. 3-5: in download.")]
    assert asyncio.run(a.giro()) == 0  # una volta sola


def test_pronta_e_sostituita():
    film = {"title": "Dune", "year": 2021}
    radarr = [
        {"id": 1, "eventType": "grabbed", "movieId": 9, "movie": film, "customFormats": ITA},
        {"id": 2, "eventType": "movieFileDeleted", "movieId": 9, "movie": film, "customFormats": ENG,
         "data": {"reason": "Upgrade"}},
        {"id": 3, "eventType": "downloadFolderImported", "movieId": 9, "movie": film, "customFormats": ITA},
    ]
    sonarr = [puntata(1, "downloadFolderImported", 2, ITA)]
    a, inviate = prepara(radarr, sonarr)
    asyncio.run(a.giro())
    # il film: niente "trovata" (è già importato nello stesso giro), solo "ora in italiano"
    assert inviate == [("Ora in italiano", "Dune (2021): ho sostituito la versione in inglese."),
                       ("Pronta in italiano", "Yaga St. 1 Ep. 2: in Jellyfin.")]
    assert [x["titolo"] for x in a.recenti()] == ["Pronta in italiano", "Ora in italiano"]
    a.segna_visti()
    assert a.recenti() == []


def test_salta_qualita_migliore_download_di_cerca_e_altre_lingue():
    sonarr = [
        # da italiano a italiano (solo qualità migliore): niente di nuovo
        puntata(1, "episodeFileDeleted", 1, ITA, data={"reason": "Upgrade"}),
        puntata(2, "downloadFolderImported", 1, ITA),
        # avviato da Cerca: ha già il suo avviso
        puntata(3, "downloadFolderImported", 2, ITA, downloadId="ABC"),
        # in inglese
        puntata(4, "grabbed", 3, ENG),
    ]
    a, inviate = prepara(sonarr=sonarr, hash_di_cerca={"abc"})
    asyncio.run(a.giro())
    assert inviate == []


def test_multi_confermati():
    a, inviate = prepara()
    asyncio.run(a.multi_confermati(
        [{"title": "Dune", "year": 2021}],
        [{"seriesId": 7, "series": {"title": "Yaga"}, "seasonNumber": 1, "episodeNumber": 7}]))
    assert inviate == [("Pronta in italiano", "Dune (2021): era un MULTI, l'audio in italiano c'è."),
                       ("Pronta in italiano", "Yaga St. 1 Ep. 7: era un MULTI, l'audio in italiano c'è.")]


def test_senza_ntfy_resta_nella_pagina():
    archivio = Archivio()
    a = AvvisiItaliano(Config(), archivio, Finto(), Finto(attivo=False), None)
    asyncio.run(a.multi_confermati([{"title": "Dune", "year": 2021}], []))
    assert len(a.recenti()) == 1
