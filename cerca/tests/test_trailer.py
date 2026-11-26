"""Trailer per l'anteprima al passaggio: quale video sceglie e da dove lo prende."""

import asyncio
from types import SimpleNamespace

from app.rete import ErroreServizio
from app.trailer import migliore, trova


def test_migliore_preferisce_il_trailer_ufficiale():
    video = [
        {"site": "YouTube", "key": "teaser", "type": "Teaser", "official": True},
        {"site": "YouTube", "key": "fan", "type": "Trailer", "official": False},
        {"site": "Vimeo", "key": "vimeo", "type": "Trailer", "official": True},
        {"site": "YouTube", "key": "ufficiale", "type": "Trailer", "official": True, "size": 1080},
    ]
    assert migliore(video) == "ufficiale"


def test_migliore_senza_video_youtube():
    assert migliore([]) == ""
    assert migliore([{"site": "Vimeo", "key": "x", "type": "Trailer"}]) == ""
    assert migliore(None) == ""


class Archivio:
    def __init__(self):
        self.dati = {}

    def cache_leggi(self, chiave):
        return self.dati.get(chiave)

    def cache_scrivi(self, chiave, valore, durata):
        self.dati[chiave] = valore


def _servizi(con_tmdb, videos=None, radarr=""):
    chiamate = []

    async def _get(percorso, parametri, durata):
        chiamate.append((percorso, parametri["language"]))
        return {"results": (videos or {}).get(parametri["language"], [])}

    async def trailer_radarr(tmdb_id):
        chiamate.append(("radarr", tmdb_id))
        return radarr

    config = SimpleNamespace(con_tmdb=con_tmdb, lingua_tmdb="it-IT", radarr_chiave="chiave")
    tmdb = SimpleNamespace(_get=_get) if con_tmdb else SimpleNamespace()
    return SimpleNamespace(config=config, tmdb=tmdb, radarr=SimpleNamespace(trailer=trailer_radarr),
                           archivio=Archivio()), chiamate


def test_con_tmdb_prima_italiano_poi_inglese():
    s, chiamate = _servizi(True, {"en-US": [{"site": "YouTube", "key": "abc", "type": "Trailer"}]})
    assert asyncio.run(trova(s, "tv", 42)) == "abc"
    assert chiamate == [("/tv/42/videos", "it-IT"), ("/tv/42/videos", "en-US")]
    # la seconda volta arriva dalla memoria, senza chiedere di nuovo
    assert asyncio.run(trova(s, "tv", 42)) == "abc"
    assert len(chiamate) == 2


def test_senza_tmdb_i_film_li_trova_radarr():
    s, chiamate = _servizi(False, radarr="xyz")
    assert asyncio.run(trova(s, "movie", 550)) == "xyz"
    assert chiamate == [("radarr", 550)]


def test_senza_tmdb_le_serie_non_hanno_trailer():
    s, chiamate = _servizi(False, radarr="xyz")
    assert asyncio.run(trova(s, "tv", 1)) == ""
    assert chiamate == []


def test_tipo_sconosciuto_o_id_non_valido():
    s, chiamate = _servizi(True)
    assert asyncio.run(trova(s, "musica", 1)) == ""
    assert asyncio.run(trova(s, "movie", 0)) == ""
    assert chiamate == []


def test_servizio_spento_non_e_un_errore():
    s, _ = _servizi(False)

    async def spento(tmdb_id):
        raise ErroreServizio("Radarr non risponde.")

    s.radarr.trailer = spento
    assert asyncio.run(trova(s, "movie", 7)) == ""
