"""Integrità dei dati di Cerca: l'archivio SQLite, i file scaricati, il segreto dei cookie.

Ogni test descrive una garanzia: se fallisce, qualcosa può perdere, duplicare o
rovinare dati dell'utente (download, cronologia, avvisi, file sul disco).
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import stat
import threading
import time
from pathlib import Path

import httpx
import pytest

from app.accesso import FILE_SEGRETO, Accesso
from app.config import Config
from app.db import COLONNE_DOWNLOAD, Archivio
from app.download import GestoreDownload
from app.foto_protette import FotoProtette
from app.pulizia import _cancella, protetto
from app.rete import ErroreServizio

# --- archivio: schema e aggiornamenti ------------------------------------------------


def _indici(percorso: Path) -> set[str]:
    with sqlite3.connect(percorso) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}


def test_un_archivio_vecchio_si_aggiorna_senza_perdere_righe(tmp_path):
    """Archivio creato prima delle serie: mancano le colonne serie/puntate. All'avvio si
    aggiungono, le righe restano tutte e gli indici si creano."""
    percorso = tmp_path / "cerca.db"
    with sqlite3.connect(percorso) as conn:
        conn.executescript("""
            CREATE TABLE download (
                id INTEGER PRIMARY KEY AUTOINCREMENT, tmdb_id INTEGER, titolo TEXT NOT NULL, anno TEXT,
                poster TEXT, fonte TEXT NOT NULL, tipo TEXT NOT NULL, url TEXT, nome_file TEXT, percorso TEXT,
                categoria TEXT, hash TEXT, stato TEXT NOT NULL, scaricati INTEGER DEFAULT 0,
                totale INTEGER DEFAULT 0, velocita INTEGER DEFAULT 0, messaggio TEXT,
                creato REAL NOT NULL, aggiornato REAL NOT NULL);
            INSERT INTO download (titolo, fonte, tipo, stato, hash, creato, aggiornato)
                VALUES ('Vecchio', 'manuale', 'torrent', 'importato', 'abc', 1, 1);
        """)
    a = Archivio(percorso)
    vecchio = a.elenco_download()[0]
    assert vecchio["titolo"] == "Vecchio" and vecchio["serie"] is None and vecchio["puntate"] is None
    assert {"download_stato", "download_hash", "download_serie", "download_tmdb", "download_creato"} <= _indici(percorso)
    # una seconda apertura non rifà niente e non tocca i dati
    b = Archivio(percorso)
    assert [d["titolo"] for d in b.elenco_download()] == ["Vecchio"]
    assert b.integrita() == "ok"


def test_i_dati_restano_dopo_la_chiusura(tmp_path):
    a = Archivio(tmp_path / "cerca.db")
    id_d = a.nuovo_download(titolo="Nosferatu", fonte="archive", tipo="http", percorso="/data/x.mp4")
    a.stato_scrivi("chiave", "valore")
    a.avvisami_aggiungi(7, "Film", "1922", "")
    a.aggiungi_ricerca("Metropolis")
    del a
    b = Archivio(tmp_path / "cerca.db")  # come dopo un riavvio
    assert b.download(id_d)["titolo"] == "Nosferatu"
    assert b.stato_leggi("chiave") == "valore"
    assert b.avvisami_presente(7)
    assert b.ultime_ricerche()[0]["testo"] == "Metropolis"


def test_testo_con_accenti_ed_emoji_torna_identico(tmp_path):
    a = Archivio(tmp_path / "cerca.db")
    testo = "La città più bella 🎬 — «Amélie» 東京"
    a.cache_scrivi("c", {"t": testo, "n": [1, 2.5, None, True]}, 60)
    a.stato_scrivi("s", testo)
    id_d = a.nuovo_download(titolo=testo, fonte="manuale", tipo="torrent")
    assert a.cache_leggi("c") == {"t": testo, "n": [1, 2.5, None, True]}
    assert a.stato_leggi("s") == testo
    assert a.download(id_d)["titolo"] == testo


# --- download: colonne, hash, stati -------------------------------------------------


def test_le_colonne_dei_download_sono_solo_quelle_previste(tmp_path):
    """I nomi delle colonne finiscono nel testo SQL: un nome sconosciuto si rifiuta,
    senza scrivere niente (niente SQL iniettato da un nome di campo)."""
    a = Archivio(tmp_path / "cerca.db")
    with pytest.raises(ValueError):
        a.nuovo_download(titolo="X", fonte="manuale", tipo="torrent", **{"stato) VALUES (1); DROP TABLE download; --": 1})
    assert a.elenco_download() == []
    id_d = a.nuovo_download(titolo="X", fonte="manuale", tipo="torrent")
    with pytest.raises(ValueError):
        a.aggiorna_download(id_d, **{"stato = 'importato', titolo": "Y"})
    with pytest.raises(ValueError):
        a.aggiorna_download(id_d, inesistente=1)
    voce = a.download(id_d)
    assert voce["stato"] == "in_corso" and voce["titolo"] == "X"
    # tutte le colonne vere della tabella (tranne id) sono accettate
    with sqlite3.connect(tmp_path / "cerca.db") as conn:
        vere = {r[1] for r in conn.execute("PRAGMA table_info(download)")} - {"id"}
    assert vere == COLONNE_DOWNLOAD


def test_l_hash_si_salva_in_minuscolo_e_si_ritrova(tmp_path):
    a = Archivio(tmp_path / "cerca.db")
    id_d = a.nuovo_download(titolo="X", fonte="manuale", tipo="torrent", hash="ABCDEF")
    assert a.download(id_d)["hash"] == "abcdef"
    assert a.download_per_hash("AbCdEf")["id"] == id_d
    a.aggiorna_download(id_d, hash="FFF000")
    assert a.download_per_hash("fff000")["id"] == id_d and a.download_per_hash("abcdef") is None


def test_l_aggiornamento_tocca_solo_la_voce_indicata(tmp_path):
    a = Archivio(tmp_path / "cerca.db")
    uno = a.nuovo_download(titolo="Uno", fonte="manuale", tipo="torrent")
    due = a.nuovo_download(titolo="Due", fonte="manuale", tipo="torrent")
    prima = a.download(due)
    time.sleep(0.01)
    a.aggiorna_download(uno, stato="errore", messaggio="no")
    assert a.download(due) == prima
    assert a.download(uno)["aggiornato"] > a.download(uno)["creato"]


def test_puntate_rovinate_non_fermano_l_elenco(tmp_path):
    a = Archivio(tmp_path / "cerca.db")
    buona = a.nuovo_download(titolo="Ep 1", fonte="animeunity", tipo="http", serie=9, puntate=json.dumps([101, 102]))
    a.nuovo_download(titolo="Ep 2", fonte="animeunity", tipo="http", serie=9, puntate="{rovinato")
    a.nuovo_download(titolo="Ep 3", fonte="animeunity", tipo="http", serie=9, puntate="[103]", stato="importato")
    puntate = a.download_puntate(9)
    assert set(puntate) == {101, 102} and puntate[101]["id"] == buona  # le importate non contano
    assert a.puntate_in_download(9) == {101, 102}


def test_il_conteggio_degli_attivi_coincide_con_l_elenco(tmp_path):
    a = Archivio(tmp_path / "cerca.db")
    stati = ["ricerca", "in_corso", "completato", "importazione", "importato", "errore", "da_associare"]
    for i in range(80):
        a.nuovo_download(titolo=f"D{i}", fonte="manuale", tipo="torrent", stato=stati[i % len(stati)])
    attivi = ("ricerca", "in_corso", "completato", "importazione")
    atteso = len([d for d in a.elenco_download(60) if d["stato"] in attivi])
    assert a.conta_download(*attivi, tra_gli_ultimi=60) == atteso
    assert a.conta_download("importato", tra_gli_ultimi=1000) == len(a.download_per_stato("importato"))


def test_tanti_thread_che_scrivono_non_perdono_niente(tmp_path):
    """I lavori in sottofondo scrivono da thread diversi (un collegamento per thread)."""
    a = Archivio(tmp_path / "cerca.db")
    ids: list[int] = []
    lucchetto = threading.Lock()
    errori: list[BaseException] = []

    def lavoro(n: int) -> None:
        try:
            for i in range(25):
                id_d = a.nuovo_download(titolo=f"{n}-{i}", fonte="manuale", tipo="torrent")
                a.aggiorna_download(id_d, scaricati=i, totale=25)
                with lucchetto:
                    ids.append(id_d)
        except BaseException as exc:  # noqa: BLE001
            errori.append(exc)

    thread = [threading.Thread(target=lavoro, args=(n,)) for n in range(8)]
    for t in thread:
        t.start()
    for t in thread:
        t.join()
    assert not errori
    assert len(ids) == len(set(ids)) == 200
    assert len(a.elenco_download(1000)) == 200
    assert all(a.download(i)["totale"] == 25 for i in ids)


# --- cronologia, avvisi, stato -------------------------------------------------------


def test_la_cronologia_non_ha_doppioni_neanche_in_parallelo(tmp_path):
    a = Archivio(tmp_path / "cerca.db")
    errori: list[BaseException] = []

    def cerca(testo: str) -> None:
        try:
            for _ in range(10):
                a.aggiungi_ricerca(testo)
        except BaseException as exc:  # noqa: BLE001
            errori.append(exc)

    thread = [threading.Thread(target=cerca, args=(t,)) for t in ["Dune", "dune", "DUNE", "Alien"] * 3]
    for t in thread:
        t.start()
    for t in thread:
        t.join()
    assert not errori
    testi = [r["testo"].lower() for r in a.ultime_ricerche(100)]
    assert sorted(testi) == ["alien", "dune"]


def test_la_cronologia_tiene_al_massimo_30_ricerche(tmp_path):
    a = Archivio(tmp_path / "cerca.db")
    for i in range(45):
        a.aggiungi_ricerca(f"film {i}")
    a.aggiungi_ricerca("   ")  # vuota: ignorata
    ultime = a.ultime_ricerche(100)
    assert len(ultime) == 30 and ultime[0]["testo"] == "film 44" and ultime[-1]["testo"] == "film 15"


def test_avvisami_non_si_duplica_e_tiene_la_data_originale(tmp_path):
    a = Archivio(tmp_path / "cerca.db")
    a.avvisami_aggiungi(1, "Film", "2000", "")
    prima = a.avvisami_elenco()[0]["aggiunto"]
    time.sleep(0.01)
    a.avvisami_aggiungi(1, "Film (doppione)", "2000", "")
    elenco = a.avvisami_elenco()
    assert len(elenco) == 1 and elenco[0]["aggiunto"] == prima and elenco[0]["titolo"] == "Film"


def test_un_avviso_rovinato_non_fa_sparire_gli_altri(tmp_path):
    a = Archivio(tmp_path / "cerca.db")
    a.avvisami_aggiungi(1, "Buono", "2000", "")
    a.avvisami_aggiungi(2, "Rovinato", "2001", "")
    a.avvisami_imposta(1, 10.0, [{"nome": "Pluto TV"}], False)
    a._esegui("UPDATE avvisami SET provider = '{non json' WHERE tmdb_id = 2")
    elenco = {v["tmdb_id"]: v for v in a.avvisami_elenco()}
    assert elenco[1]["provider"] == [{"nome": "Pluto TV"}] and elenco[2]["provider"] == []


def test_i_prefissi_sono_esatti(tmp_path):
    """Con LIKE "_" faceva da jolly e le maiuscole non contavano: si toccavano chiavi altrui."""
    a = Archivio(tmp_path / "cerca.db")
    for chiave in ("importa_musica:1", "importa_musica:2", "importaXmusica:3", "IMPORTA_MUSICA:4", "importa_musica"):
        a.stato_scrivi(chiave, "x")
    assert set(a.stato_con_prefisso("importa_musica:")) == {"importa_musica:1", "importa_musica:2"}
    assert set(a.stato_con_prefisso("50%")) == set()

    for chiave in ("libero:a", "libero2:b", "LIBERO:c", "liberoXa"):
        a.cache_scrivi(chiave, 1, 60)
    a.cache_togli("libero:")
    assert a.cache_leggi("libero:a") is None
    assert all(a.cache_leggi(k) == 1 for k in ("libero2:b", "LIBERO:c", "liberoXa"))
    a.cache_togli("")  # prefisso vuoto: tutto
    assert a.cache_dimensione() == 0


def test_una_risposta_in_cache_rovinata_vale_come_mancante(tmp_path):
    a = Archivio(tmp_path / "cerca.db")
    a.cache_scrivi("buona", {"ok": True}, 60)
    a._esegui("INSERT INTO cache (chiave, valore, scade) VALUES ('rotta', '{\"tronca', ?)", (time.time() + 60,))
    assert a.cache_leggi("rotta") is None
    assert a.cache_leggi("rotta", scadute=True) is None
    assert a._righe("SELECT chiave FROM cache") == [{"chiave": "buona"}]  # tolta, la prossima volta si riscarica


def test_le_transazioni_sono_tutto_o_niente(tmp_path):
    a = Archivio(tmp_path / "cerca.db")
    a.stato_scrivi("prima", "1")
    with pytest.raises(RuntimeError), a._transazione() as conn:
        conn.execute("INSERT INTO stato (chiave, valore) VALUES ('meta', '2')")
        raise RuntimeError("a metà")
    assert a.stato_leggi("meta") is None and a.stato_leggi("prima") == "1"
    # dopo il ROLLBACK il collegamento è di nuovo utilizzabile
    a.stato_scrivi("dopo", "3")
    assert a.stato_leggi("dopo") == "3"


def test_il_limite_della_cache_non_tocca_le_altre_tabelle(tmp_path):
    a = Archivio(tmp_path / "cerca.db")
    id_d = a.nuovo_download(titolo="X", fonte="manuale", tipo="torrent")
    a.stato_scrivi("s", "v")
    for i in range(15):
        a.cache_scrivi(f"g{i}", "x" * 100_000, 100 + i)
    assert a.cache_limita(mb=0.5) > 0
    assert a.download(id_d) is not None and a.stato_leggi("s") == "v"
    assert a.integrita() == "ok"


# --- download HTTP: i file sul disco -------------------------------------------------


CONTENUTO = bytes(range(256)) * 4096  # 1 MB riconoscibile


def _gestore(tmp_path: Path, gestore_http) -> tuple[GestoreDownload, Archivio]:
    a = Archivio(tmp_path / "cerca.db")
    config = Config(radarr_url="http://radarr", radarr_chiave="x", tmdb_chiave="")
    client = httpx.AsyncClient(transport=httpx.MockTransport(gestore_http))
    g = GestoreDownload(config, a, client, None, None, None, None)
    return g, a


def test_un_download_ripreso_da_meta_e_identico_all_originale(tmp_path):
    richieste: list[str | None] = []

    def server(r: httpx.Request) -> httpx.Response:
        intervallo = r.headers.get("range")
        richieste.append(intervallo)
        if intervallo:
            inizio = int(intervallo.removeprefix("bytes=").rstrip("-"))
            return httpx.Response(206, content=CONTENUTO[inizio:])
        return httpx.Response(200, content=CONTENUTO)

    g, a = _gestore(tmp_path, server)
    finale = tmp_path / "dl" / "film.mp4"
    finale.parent.mkdir()
    (finale.parent / "film.mp4.part").write_bytes(CONTENUTO[:300_000])  # interrotto a metà
    id_d = a.nuovo_download(titolo="Film", fonte="archive", tipo="http", url="http://archive/film.mp4",
                            percorso=str(finale), totale=len(CONTENUTO))

    async def prova():
        g._passa_a_radarr = _niente  # l'importazione non c'entra qui
        await g._scarica_http_ora(id_d, a.download(id_d))

    asyncio.run(prova())
    assert richieste == ["bytes=300000-"]
    assert finale.read_bytes() == CONTENUTO
    assert not (finale.parent / "film.mp4.part").exists()
    voce = a.download(id_d)
    assert voce["stato"] == "completato" and voce["scaricati"] == voce["totale"] == len(CONTENUTO)


def test_se_il_server_non_riprende_si_ricomincia_senza_mescolare(tmp_path):
    """Il server ignora Range e manda tutto (200): il pezzo vecchio non va tenuto davanti."""
    g, a = _gestore(tmp_path, lambda r: httpx.Response(200, content=CONTENUTO))
    finale = tmp_path / "film.mp4"
    (tmp_path / "film.mp4.part").write_bytes(b"VECCHIO" * 1000)
    id_d = a.nuovo_download(titolo="Film", fonte="archive", tipo="http", url="http://x/film.mp4", percorso=str(finale))

    async def prova():
        g._passa_a_radarr = _niente
        await g._scarica_http_ora(id_d, a.download(id_d))

    asyncio.run(prova())
    assert finale.read_bytes() == CONTENUTO


def test_un_download_che_fallisce_non_diventa_un_file_finito(tmp_path, monkeypatch):
    g, a = _gestore(tmp_path, lambda r: httpx.Response(500))
    finale = tmp_path / "film.mp4"
    id_d = a.nuovo_download(titolo="Film", fonte="archive", tipo="http", url="http://x/film.mp4", percorso=str(finale))

    async def subito(_):
        return None

    monkeypatch.setattr("app.download.asyncio.sleep", subito)
    asyncio.run(g._scarica_http_ora(id_d, a.download(id_d)))
    assert not finale.exists()
    voce = a.download(id_d)
    assert voce["stato"] == "errore" and "Riprova" in voce["messaggio"]


def test_annulla_ferma_il_download_e_toglie_i_file(tmp_path):
    async def prova():
        iniziato = asyncio.Event()  # creato dentro il ciclo di asyncio

        async def flusso():
            yield CONTENUTO[:1000]
            iniziato.set()
            await asyncio.sleep(3600)  # resta "in corso"
            yield b""

        def server(r):
            return httpx.Response(200, content=flusso())

        g, a = _gestore(tmp_path, server)
        finale = tmp_path / "film.mp4"
        id_d = a.nuovo_download(titolo="Film", fonte="archive", tipo="http", url="http://x/f", percorso=str(finale))
        g._avvia_http(id_d)
        await asyncio.wait_for(iniziato.wait(), 5)
        await g.annulla(id_d)
        return a, id_d, finale

    a, id_d, finale = asyncio.run(prova())
    assert a.download(id_d) is None
    assert not finale.exists() and not Path(str(finale) + ".part").exists()


def test_un_torrent_rifiutato_da_qbittorrent_non_lascia_voci(tmp_path):
    class QbitGiu:
        async def aggiungi(self, **kw):
            raise ErroreServizio("qBittorrent non risponde.")

    a = Archivio(tmp_path / "cerca.db")
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"d4:infoe")))
    g = GestoreDownload(Config(radarr_url="http://r", radarr_chiave="x", tmdb_chiave=""), a, client, QbitGiu(),
                        None, None, None)
    film = {"id": 1, "titolo": "Film", "anno": "1922"}

    async def prova():
        with pytest.raises(ErroreServizio):
            await g.da_archive_torrent(film, {"url_torrent": "http://archive/x.torrent", "nome": "x.mp4"})
        with pytest.raises(ErroreServizio):
            await g.da_torrent(fonte="prowlarr", categoria="cerca", film=film, titolo="", magnet="magnet:?xt=1")

    asyncio.run(prova())
    assert a.elenco_download() == []


async def _niente(*_args, **_kw):
    return None


# --- pulizia: cosa si cancella --------------------------------------------------------


def test_la_pulizia_toglie_il_collegamento_non_la_cartella_a_cui_punta(tmp_path):
    vera = tmp_path / "vera"
    vera.mkdir()
    (vera / "film.mkv").write_bytes(b"x")
    collegamento = tmp_path / "downloads" / "collegamento"
    collegamento.parent.mkdir()
    collegamento.symlink_to(vera, target_is_directory=True)
    _cancella(collegamento)
    assert not collegamento.is_symlink() and not collegamento.exists()
    assert (vera / "film.mkv").read_bytes() == b"x"


def test_la_pulizia_non_tocca_mai_le_foto(monkeypatch, tmp_path):
    foto = tmp_path / "foto"
    (foto / "2026").mkdir(parents=True)
    (foto / "2026" / "IMG_0001.HEIC").write_bytes(b"ricordo")
    monkeypatch.setattr("app.foto_protette.CARTELLE_FOTO", (foto,))
    collegamento = tmp_path / "downloads" / "finto"
    collegamento.parent.mkdir()
    collegamento.symlink_to(foto, target_is_directory=True)
    for percorso in (foto, foto / "2026", foto / "2026" / "IMG_0001.HEIC", collegamento, tmp_path):
        with pytest.raises(FotoProtette):
            _cancella(percorso)
    assert (foto / "2026" / "IMG_0001.HEIC").read_bytes() == b"ricordo"


def test_un_download_in_uso_e_protetto():
    in_uso = {"/data/downloads/cerca/Film (2000)", "/data/downloads/cerca/Film (2000)/f.mkv.part"}
    assert protetto(Path("/data/downloads/cerca/Film (2000)"), in_uso)
    assert protetto(Path("/data/downloads/cerca"), in_uso)  # la contiene
    assert not protetto(Path("/data/downloads/cerca/Film (2000) bis"), in_uso)  # nome simile, non è lei


# --- il segreto dei cookie ------------------------------------------------------------


def test_il_segreto_e_privato_stabile_e_scritto_per_intero(tmp_path):
    a = Accesso(tmp_path)
    segreto = a.segreto
    file = tmp_path / FILE_SEGRETO
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    assert len(file.read_text().strip()) == 64
    assert Accesso(tmp_path).segreto == segreto  # dopo un riavvio: stessi cookie validi
    assert sorted(p.name for p in tmp_path.iterdir()) == [FILE_SEGRETO]  # niente file temporanei
    cookie = a.cookie({"id": "u1", "nome": "Anna", "admin": False})
    assert Accesso(tmp_path).leggi(cookie) == {"id": "u1", "nome": "Anna", "admin": False}


def test_un_segreto_troncato_si_rifa(tmp_path):
    (tmp_path / FILE_SEGRETO).write_text("corto")
    os.chmod(tmp_path / FILE_SEGRETO, 0o644)
    a = Accesso(tmp_path)
    assert len(a.segreto) == 64
    assert stat.S_IMODE((tmp_path / FILE_SEGRETO).stat().st_mode) == 0o600


def test_un_cookie_manomesso_non_vale(tmp_path):
    a = Accesso(tmp_path)
    cookie = a.cookie({"id": "u1", "nome": "Anna", "admin": False})
    dati, firma = cookie.rsplit(".", 1)
    falso = a.cookie({"id": "u1", "nome": "Anna", "admin": True}).rsplit(".", 1)[0]
    assert a.leggi(f"{falso}.{firma}") is None
    assert a.leggi(f"{dati}.{'0' * len(firma)}") is None
    assert a.leggi("niente-punto") is None and a.leggi("") is None and a.leggi("à.è") is None
