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
