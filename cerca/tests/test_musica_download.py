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


class ArchivioFinto:
    def __init__(self):
        self.voci = {}

    def nuovo_download(self, **campi):
        self.voci[7] = {"id": 7, **campi}
        return 7

    def aggiorna_download(self, id_download, **campi):
        self.voci[id_download].update(campi)


class DownloadFinto:
    """Come Download: la voce va subito in elenco, la ricerca gira dopo (qui la si aspetta)."""

    def __init__(self):
        self.archivio = ArchivioFinto()
        self.kw = None
        self.lavori = []

    async def da_torrent(self, **kw):
        self.kw = kw
        return kw["id_download"]

    def cerca_in_sottofondo(self, id_download, coro):
        self.lavori.append((id_download, coro))


class DeezerFinto:
    async def _get(self, percorso, durata=0):
        return {"title": "Sabbie mobili", "artist": {"name": "Marracash"},
                "album": {"title": "Status", "cover_medium": "http://c/1.jpg"}}


def prowlarr_con(*risultati):
    class ProwlarrFinto:
        async def _cerca_testo(self, testo, categoria):
            assert categoria == 3000
            return list(risultati)

        async def scarica_torrent(self, risultato):
            return risultato["magnetUrl"], None

    return ProwlarrFinto()


CONFIG = type("C", (), {"cartella_musica": "/data/media/musica", "min_fonti": 3})()


def test_scarica_un_brano_nella_cartella_dell_artista():
    download = DownloadFinto()

    async def prova():
        esito = await ScaricaMusica(CONFIG, DeezerFinto(), prowlarr_con(r("Marracash - Status (2015) MP3", seed=20)),
                                    download).brano(1)
        # la voce è già nella pagina Download, prima che la ricerca finisca
        assert download.archivio.voci[7]["stato"] == "ricerca"
        assert download.archivio.voci[7]["categoria"] == "musica"
        assert download.kw is None
        for _, lavoro in download.lavori:
            await lavoro
        return esito

    esito = asyncio.run(prova())
    assert "Sabbie mobili" in esito["messaggio"] and esito["id"] == 7
    assert download.kw["cartella"] == "/data/media/musica/Marracash"
    assert download.kw["categoria"] == "musica" and download.kw["solo_brano"] == "Sabbie mobili"
    assert download.kw["poster"] == "http://c/1.jpg" and download.kw["id_download"] == 7


def test_brano_non_trovato_resta_in_elenco_con_il_perche():
    download = DownloadFinto()

    async def prova():
        await ScaricaMusica(CONFIG, DeezerFinto(), prowlarr_con(r("Altro - Altro")), download).brano(1)
        (_, lavoro), = download.lavori
        try:
            await lavoro
        except Exception as exc:  # noqa: BLE001 - in Download lo trasforma in "errore" con il messaggio
            return exc
        return None

    errore = asyncio.run(prova())
    assert errore is not None and "Non trovo" in errore.cosa
    assert download.kw is None


def test_musica_con_abbastanza_fonti():
    risultati = [r("Marracash - Status (2015) MP3 320", seed=1), r("Marracash - Status (2015) FLAC", seed=25)]
    assert scegli(risultati, "Marracash", ["Status"], 10**10, min_fonti=3)["title"] == "Marracash - Status (2015) FLAC"
    # se nessuno ne ha abbastanza si prova comunque il migliore
    assert scegli(risultati[:1], "Marracash", ["Status"], 10**10, min_fonti=3) is not None
