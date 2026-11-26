"""Test delle funzioni di supporto ai download e del database."""

from app.avvisami import messaggio_notifica
from app.db import Archivio
from app.download import e_magnet, etichetta, id_da_etichette, nome_cartella, nome_da_magnet, titoli_simili
from app.prowlarr import id_risultato, ordina, riassunto
from app.radarr import Radarr  # noqa: F401  (import di controllo)


def test_nome_cartella_senza_caratteri_vietati():
    assert nome_cartella("Mission: Impossible", "1996") == "Mission Impossible (1996)"
    assert nome_cartella('Che cos\'è? "Boh"/Mah', "") == "Che cos'è Boh Mah"
    assert nome_cartella("", "2000") == "Film (2000)"


def test_magnet():
    m = "magnet:?xt=urn:btih:abc&dn=Night.of.the.Living.Dead.1968.1080p&tr=udp%3A%2F%2Ftracker"
    assert e_magnet(m)
    assert not e_magnet("https://example.org/file.torrent")
    assert nome_da_magnet(m) == "Night.of.the.Living.Dead.1968.1080p"


def test_etichette():
    assert etichetta(12) == "mediabox-12"
    assert id_da_etichette("altro, mediabox-12") == 12
    assert id_da_etichette("mediabox-x") is None
    assert id_da_etichette("") is None


def test_titoli_simili():
    assert titoli_simili("Night of the Living Dead", "night of the living dead")
    assert titoli_simili("Amélie", "Amelie")
    assert not titoli_simili("Dawn of the Dead", "Night of the Living Dead")


def test_prowlarr_ordina_per_seed_e_id_stabile():
    grezzi = [
        {"guid": "a", "indexerId": 1, "title": "A", "seeders": 3, "protocol": "torrent"},
        {"guid": "b", "indexerId": 1, "title": "B", "seeders": 50, "protocol": "torrent"},
        {"guid": "c", "indexerId": 2, "title": "C", "seeders": None, "protocol": "torrent"},
    ]
    ordinati = ordina([riassunto(r) for r in grezzi])
    assert [r["titolo"] for r in ordinati] == ["B", "A", "C"]
    assert id_risultato(grezzi[0]) == id_risultato(dict(grezzi[0]))
    assert id_risultato(grezzi[0]) != id_risultato(grezzi[1])


def test_messaggio_notifica():
    msg = messaggio_notifica("Nosferatu", "1922", [{"nome": "Pluto TV"}, {"nome": "Rakuten TV"}])
    assert msg == "Nosferatu (1922) ora si può vedere gratis su Pluto TV, Rakuten TV."


def test_archivio(tmp_path):
    a = Archivio(tmp_path / "prova.db")
    a.aggiungi_ricerca("Nosferatu")
    a.aggiungi_ricerca("nosferatu")
    assert [r["testo"] for r in a.ultime_ricerche()] == ["nosferatu"]

    id_d = a.nuovo_download(titolo="Film", fonte="manuale", tipo="torrent", hash="abc")
    a.aggiorna_download(id_d, stato="completato", percorso="/data/x")
    assert a.download(id_d)["stato"] == "completato"
    assert a.download_per_hash("ABC")["id"] == id_d
    assert [d["id"] for d in a.download_per_stato("completato")] == [id_d]

    a.avvisami_aggiungi(5, "Film", "2000", "")
    assert a.avvisami_presente(5)
    a.avvisami_imposta(5, 123.0, [{"nome": "Pluto TV"}], True)
    assert a.avvisami_elenco()[0]["provider"] == [{"nome": "Pluto TV"}]

    a.cache_scrivi("k", {"x": 1}, 60)
    assert a.cache_leggi("k") == {"x": 1}
    a.cache_scrivi("scaduta", 1, -1)
    assert a.cache_leggi("scaduta") is None


def test_percorso_per_jellyfin():
    from app.jellyfin import percorso_per_jellyfin

    assert percorso_per_jellyfin("/data/media/film/X (2000)") == "/data/film/X (2000)"
    assert percorso_per_jellyfin("/altro/x") == "/altro/x"


def test_torrent_coerenti_con_il_film():
    from app.prowlarr import corrisponde

    titoli = ["The Matrix", "Matrix"]
    assert corrisponde("Matrix.1999.iTA.ENG.1080p.BluRay", titoli, "1999")
    assert corrisponde("The Matrix (1999) [720p]", titoli, "1999")
    assert corrisponde("Matrix [DVDRip] ITA", titoli, "1999")  # senza anno va bene
    assert not corrisponde("Matrix Reloaded 2003 1080p", titoli, "1999")  # altro anno
    assert not corrisponde("Il Padrino 1972 ITA", titoli, "1999")  # altro film
    assert not corrisponde("Animatrix 2003", titoli, "1999")  # parola dentro un'altra
    assert corrisponde("Blade Runner 2049 (2017) ITA", ["Blade Runner 2049"], "2017")
    assert corrisponde("Il.Gladiatore.2000.iTA", ["Gladiator", "Il gladiatore"], "2000")


def test_torrent_in_italiano_prima():
    from app.prowlarr import e_italiano

    assert e_italiano("Matrix.1999.iTA.ENG.1080p")
    assert e_italiano("Matrix (1999) [ITA-ENG] BDRip")
    assert e_italiano("Matrix 1999 iTALiAN MD")
    assert not e_italiano("The Matrix 1999 1080p BluRay x264")
    assert not e_italiano("Matrix 1999 Italia Tour")  # "Italia" non è la lingua
    tutti = [riassunto({"title": "Matrix 1999 1080p", "seeders": 900, "protocol": "torrent"}),
             riassunto({"title": "Matrix 1999 iTA", "seeders": 5, "protocol": "torrent"})]
    assert [r["titolo"] for r in ordina(tutti)] == ["Matrix 1999 iTA", "Matrix 1999 1080p"]


def test_ricerca_in_sottofondo_poi_torrent_sulla_stessa_voce(tmp_path):
    """La musica: la voce compare subito ("ricerca"), poi diventa il torrent o un errore con il perché."""
    import asyncio

    from app.download import GestoreDownload as Download
    from app.rete import ErroreServizio

    class QbitFinto:
        def __init__(self):
            self.aggiunti = []

        async def aggiungi(self, **kw):
            self.aggiunti.append(kw)

    a = Archivio(tmp_path / "prova.db")
    qbit = QbitFinto()
    d = Download(None, a, None, qbit, None, None, None)

    async def trovato(id_download):
        await d.da_torrent(fonte="prowlarr", categoria="musica", film=None, titolo="A · B",
                           magnet="magnet:?xt=urn:btih:" + "a" * 40, id_download=id_download)

    async def non_trovato():
        raise ErroreServizio("Non trovo B di A sui tuoi indexer.", "Riprova tra qualche giorno.")

    async def prova():
        uno = a.nuovo_download(titolo="A · B", fonte="prowlarr", tipo="torrent", categoria="musica", stato="ricerca")
        due = a.nuovo_download(titolo="A · C", fonte="prowlarr", tipo="torrent", categoria="musica", stato="ricerca")
        d.cerca_in_sottofondo(uno, trovato(uno))
        d.cerca_in_sottofondo(due, non_trovato())
        await asyncio.gather(*d._compiti.values())
        return uno, due

    uno, due = asyncio.run(prova())
    assert a.download(uno)["stato"] == "in_corso" and a.download(uno)["url"].startswith("magnet:")
    assert qbit.aggiunti[0]["etichetta"] == etichetta(uno)
    assert a.download(due)["stato"] == "errore" and "Non trovo" in a.download(due)["messaggio"]
    assert len(a.elenco_download()) == 2 and not d._compiti
