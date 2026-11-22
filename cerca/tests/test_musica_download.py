import asyncio

from app.musica_download import ScaricaMusica, file_da_saltare, nome_cartella, scegli

MB = 1024**2


def r(titolo, seed=10, size=100 * MB, protocollo="torrent", guid=None):
    return {"title": titolo, "seeders": seed, "size": size, "protocol": protocollo, "guid": guid or titolo,
            "indexerId": 1, "magnetUrl": "magnet:?xt=urn:btih:" + "a" * 40}


def test_sceglie_il_giusto_non_la_discografia():
    risultati = [
        r("Marracash - Discografia Completa 2005-2024 MP3", seed=200, size=2000 * MB),
        r("Marracash - Status (2015) FLAC", seed=40),
        r("Marracash - Status (2015) MP3 320kbps", seed=35),
        r("Status Quo - Live 1977", seed=500),  # altro artista
        r("Marracash - Persona (2019) MP3", seed=90),  # altro album
        r("Marracash - Status (2015) MP3", seed=0),  # senza fonti
    ]
    assert scegli(risultati, "Marracash", ["Status"], 6 * 1024**3)["title"] == "Marracash - Status (2015) MP3 320kbps"


def test_niente_se_non_c_e():
    assert scegli([r("Altro - Altro")], "Mustafa Saboori", ["Aura Farming"], 10**10) is None


def test_solo_il_brano_dall_album():
    file = [{"index": 0, "name": "Status/01 - Intro.mp3"}, {"index": 1, "name": "Status/05 - Sabbie Mobili.mp3"},
            {"index": 2, "name": "Status/cover.jpg"}, {"index": 3, "name": "Status/06 - Nulla.mp3"}]
    assert file_da_saltare(file, "Sabbie mobili") == [0, 3]
    assert file_da_saltare(file, "Brano che non c'è") == []  # meglio tutto che niente


def test_nome_cartella_valido_su_windows():
    assert nome_cartella('AC/DC: "Live"?') == "AC DC Live"
    assert nome_cartella("") == "Sconosciuto"


def test_scarica_un_brano_nella_cartella_dell_artista():
    class DeezerFinto:
        async def _get(self, percorso, durata=0):
            return {"title": "Sabbie mobili", "artist": {"name": "Marracash"},
                    "album": {"title": "Status", "cover_medium": "http://c/1.jpg"}}

    class ProwlarrFinto:
        async def _cerca_testo(self, testo, categoria):
            assert categoria == 3000
            return [r("Marracash - Status (2015) MP3", seed=20)]

        async def scarica_torrent(self, risultato):
            return risultato["magnetUrl"], None

    class DownloadFinto:
        async def da_torrent(self, **kw):
            self.kw = kw
            return 7

    download = DownloadFinto()
    config = type("C", (), {"cartella_musica": "/data/media/musica", "min_fonti": 3})()
    esito = asyncio.run(ScaricaMusica(config, DeezerFinto(), ProwlarrFinto(), download).brano(1))
    assert "Sabbie mobili" in esito["messaggio"]
    assert download.kw["cartella"] == "/data/media/musica/Marracash"
    assert download.kw["categoria"] == "musica" and download.kw["solo_brano"] == "Sabbie mobili"
    assert download.kw["poster"] == "http://c/1.jpg"


def test_musica_con_abbastanza_fonti():
    risultati = [r("Marracash - Status (2015) MP3 320", seed=1), r("Marracash - Status (2015) FLAC", seed=25)]
    assert scegli(risultati, "Marracash", ["Status"], 10**10, min_fonti=3)["title"] == "Marracash - Status (2015) FLAC"
    # se nessuno ne ha abbastanza si prova comunque il migliore
    assert scegli(risultati[:1], "Marracash", ["Status"], 10**10, min_fonti=3) is not None
