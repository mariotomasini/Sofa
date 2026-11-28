"""Download di ogni tipo che vanno storti: linea che cade, server strani, siti di riserva,
ffmpeg bloccato, torrent doppi, link degli indexer con rimandi."""

from __future__ import annotations

import asyncio
import base64
import functools
import http.server
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import httpx
import pytest

from app import download as modulo_download
from app.config import Config
from app.db import Archivio
from app.download import GestoreDownload, fonti_ruotate, ids_da_etichette, range_del_server
from app.qbittorrent import QBittorrent
from app.rete import ErroreServizio, hash_da_magnet, scarica_torrent

CONTENUTO = bytes(range(256)) * 8192  # 2 MB riconoscibili
CONFIG = Config(radarr_url="http://radarr", radarr_chiave="x", tmdb_chiave="", qbit_url="http://qbit")


@pytest.fixture(autouse=True)
def senza_attese(monkeypatch):
    """Le pause tra un tentativo e l'altro (10, 20, 30 s…) nei test non servono."""
    vera = asyncio.sleep

    async def breve(secondi, *a, **kw):
        await vera(0)

    monkeypatch.setattr(modulo_download.asyncio, "sleep", breve)


def _gestore(tmp_path: Path, server, qbit=None) -> tuple[GestoreDownload, Archivio]:
    a = Archivio(tmp_path / "cerca.db")
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    g = GestoreDownload(CONFIG, a, client, qbit, None, None, None)

    async def niente(*_a, **_kw):
        return None

    g._passa_a_radarr = niente
    return g, a


def _voce(a: Archivio, tmp_path: Path, **kw) -> tuple[int, Path]:
    finale = tmp_path / "film.mp4"
    campi = {"titolo": "Film", "fonte": "archive", "tipo": "http", "url": "http://sito/film.mp4",
             "percorso": str(finale), **kw}
    return a.nuovo_download(**campi), finale


def _inizio_range(r: httpx.Request) -> int:
    intervallo = r.headers.get("range", "")
    return int(intervallo.removeprefix("bytes=").rstrip("-")) if intervallo else 0


# --- funzioni pure --------------------------------------------------------------------


def test_range_del_server():
    assert range_del_server("bytes 100-199/1000") == (100, 1000)
    assert range_del_server("bytes */1000") == (None, 1000)
    assert range_del_server("bytes 0-9/*") == (0, None)
    assert range_del_server("") == (None, None) and range_del_server("rotto") == (None, None)


def test_fonti_ruotate():
    url = "diretto:" + json.dumps([["animeunity", 1], ["animeworld", "x"]])
    assert fonti_ruotate(url, 0) == url
    assert json.loads(fonti_ruotate(url, 1)[8:]) == [["animeworld", "x"], ["animeunity", 1]]
    assert fonti_ruotate(url, 2) == "diretto:" + json.dumps([["animeunity", 1], ["animeworld", "x"]])
    assert fonti_ruotate("http://x", 3) == "http://x"
    assert fonti_ruotate("diretto:" + json.dumps([["animeunity", 1]]), 1).endswith('[["animeunity", 1]]')
    assert fonti_ruotate("diretto:{rotto", 1) == "diretto:{rotto"


def test_ids_da_etichette():
    assert ids_da_etichette("mediabox-3, altro, mediabox-9") == [3, 9]
    assert ids_da_etichette("") == [] and ids_da_etichette("mediabox-x") == []


def test_hash_da_magnet():
    esadecimale = "c12fe1c06bba254a9dc9f519b335aa7c1367a88a"
    assert hash_da_magnet(f"magnet:?xt=urn:btih:{esadecimale.upper()}&dn=x") == esadecimale
    base32 = base64.b32encode(bytes.fromhex(esadecimale)).decode()
    assert hash_da_magnet(f"magnet:?dn=x&xt=urn:btih:{base32}") == esadecimale
    assert hash_da_magnet("magnet:?dn=senza-hash") == "" and hash_da_magnet("magnet:?xt=urn:btih:corto") == ""


# --- download HTTP ------------------------------------------------------------------


def test_una_linea_che_cade_spesso_arriva_comunque_alla_fine(tmp_path):
    """Il server chiude dopo 300 KB ogni volta: 7 cadute (più dei 5 tentativi), ma ogni volta
    il file cresce, quindi si continua fino alla fine e il file è identico."""
    richieste = []

    async def a_pezzi(dati: bytes):
        yield dati[:300_000]
        if len(dati) > 300_000:
            raise httpx.ReadError("la linea è caduta")

    def server(r: httpx.Request) -> httpx.Response:
        inizio = _inizio_range(r)
        richieste.append(inizio)
        resto = CONTENUTO[inizio:]
        stato = 206 if inizio else 200
        return httpx.Response(stato, headers={"content-length": str(len(resto))}, content=a_pezzi(resto))

    g, a = _gestore(tmp_path, server)
    id_d, finale = _voce(a, tmp_path)
    asyncio.run(g._scarica_http_ora(id_d, a.download(id_d)))
    assert len(richieste) == 7 and richieste == sorted(set(richieste))  # ogni volta da dove era
    assert finale.read_bytes() == CONTENUTO
    assert a.download(id_d)["stato"] == "completato"


def test_un_server_che_non_da_mai_niente_si_ferma_con_un_errore(tmp_path):
    chiamate = []

    def server(r):
        chiamate.append(1)
        return httpx.Response(503)

    g, a = _gestore(tmp_path, server)
    id_d, finale = _voce(a, tmp_path)
    asyncio.run(g._scarica_http_ora(id_d, a.download(id_d)))
    assert len(chiamate) == 5 and not finale.exists()
    assert a.download(id_d)["stato"] == "errore"


def test_una_risposta_troncata_non_diventa_un_file_finito(tmp_path):
    """Il server dichiara 2 MB ma ne manda metà e chiude "bene": si riprende dal punto giusto."""
    def server(r):
        inizio = _inizio_range(r)
        if not inizio:
            return httpx.Response(200, headers={"content-length": str(len(CONTENUTO))},
                                  content=CONTENUTO[: len(CONTENUTO) // 2])
        return httpx.Response(206, headers={"content-range": f"bytes {inizio}-{len(CONTENUTO) - 1}/{len(CONTENUTO)}"},
                              content=CONTENUTO[inizio:])

    g, a = _gestore(tmp_path, server)
    id_d, finale = _voce(a, tmp_path)
    asyncio.run(g._scarica_http_ora(id_d, a.download(id_d)))
    assert finale.read_bytes() == CONTENUTO


def test_un_pezzo_da_un_punto_sbagliato_non_si_attacca(tmp_path):
    """Chiedo da 500000, il server risponde 206 ma da 0: attaccarlo rovinerebbe il film."""
    def server(r):
        if _inizio_range(r):
            return httpx.Response(206, headers={"content-range": f"bytes 0-99/{len(CONTENUTO)}"}, content=CONTENUTO[:100])
        return httpx.Response(200, content=CONTENUTO)

    g, a = _gestore(tmp_path, server)
    id_d, finale = _voce(a, tmp_path)
    Path(str(finale) + ".part").write_bytes(CONTENUTO[:500_000])
    asyncio.run(g._scarica_http_ora(id_d, a.download(id_d)))
    assert finale.read_bytes() == CONTENUTO


def test_416_e_completo_solo_se_le_dimensioni_tornano(tmp_path):
    # il pezzo c'è tutto: 416 con la dimensione giusta → finito senza riscaricare
    g, a = _gestore(tmp_path, lambda r: httpx.Response(416, headers={"content-range": f"bytes */{len(CONTENUTO)}"}))
    id_d, finale = _voce(a, tmp_path)
    Path(str(finale) + ".part").write_bytes(CONTENUTO)
    asyncio.run(g._scarica_http_ora(id_d, a.download(id_d)))
    assert finale.read_bytes() == CONTENUTO

    # il pezzo è più grande del file sul sito (cambiato): non è "finito", si rifà da capo
    piccolo = CONTENUTO[:1000]

    def server(r):
        if _inizio_range(r):
            return httpx.Response(416, headers={"content-range": f"bytes */{len(piccolo)}"})
        return httpx.Response(200, content=piccolo)

    altra = tmp_path / "b"
    altra.mkdir()
    g, a = _gestore(altra, server)
    id_d, finale = _voce(a, altra)
    Path(str(finale) + ".part").write_bytes(CONTENUTO[:5000])
    asyncio.run(g._scarica_http_ora(id_d, a.download(id_d)))
    assert finale.read_bytes() == piccolo


def test_se_un_sito_non_scarica_si_passa_a_quello_di_riserva(tmp_path):
    """AnimeUnity dà il link ma il file non arriva: dopo 2 tentativi si prova AnimeWorld, da capo."""
    serviti = []

    async def a_pezzi():
        yield b"PEZZO-DI-ANIMEUNITY" * 100
        raise httpx.ReadError("cade sempre")

    def server(r):
        serviti.append(r.url.host)
        if r.url.host == "cdn-au":
            return httpx.Response(200, content=a_pezzi())
        return httpx.Response(200, content=CONTENUTO)

    g, a = _gestore(tmp_path, server)
    usati = []

    async def risolutore(voce):
        fonti = json.loads(voce["url"][len("diretto:"):])
        usati.append(fonti[0][0])
        return ("http://cdn-au/ep.mp4" if fonti[0][0] == "animeunity" else "http://cdn-aw/ep.mp4"), {}

    g.risolutori["animeunity"] = risolutore
    fonti = [["animeunity", 11], ["animeworld", "ab"]]
    id_d, finale = _voce(a, tmp_path, fonte="animeunity", url="diretto:" + json.dumps(fonti), serie=1,
                         puntate="[5]")
    asyncio.run(g._scarica_http_ora(id_d, a.download(id_d)))
    assert finale.read_bytes() == CONTENUTO  # niente pezzi di AnimeUnity attaccati davanti
    assert "animeworld" in usati and serviti[-1] == "cdn-aw"


def test_riprova_rifa_il_download_se_il_file_finito_non_c_e(tmp_path):
    """Lo streaming stima i byte: un HLS fallito poteva avere "scaricati == totale" e "Riprova"
    lo mandava a Radarr senza file. Ora conta se il file c'è davvero."""
    g, a = _gestore(tmp_path, lambda r: httpx.Response(200, content=CONTENUTO))
    id_d, finale = _voce(a, tmp_path, stato="errore", scaricati=100, totale=100)
    passati = []

    async def a_radarr(i):
        passati.append(i)

    async def prova():
        g._passa_a_radarr = a_radarr
        await g.riprova(id_d)
        await asyncio.gather(*g._compiti.values())

    asyncio.run(prova())
    assert finale.read_bytes() == CONTENUTO and passati == [id_d]

    # file finito presente (l'importazione era fallita): si riprova solo l'importazione
    altra = tmp_path / "x"
    altra.mkdir()
    g2, a2 = _gestore(altra, lambda r: pytest.fail("non deve riscaricare"))
    id2, finale2 = _voce(a2, altra, stato="errore")
    finale2.write_bytes(b"film")
    importati = []

    async def a_radarr2(i):
        importati.append(i)

    async def prova2():
        g2._passa_a_radarr = a_radarr2
        await g2.riprova(id2)
        await asyncio.sleep(0)
        await asyncio.gather(*g2._in_sottofondo)

    asyncio.run(prova2())
    assert importati == [id2] and a2.download(id2)["stato"] == "completato"


def test_lo_stesso_download_non_parte_due_volte(tmp_path):
    g, a = _gestore(tmp_path, lambda r: httpx.Response(200, content=CONTENUTO))
    id_d, finale = _voce(a, tmp_path)

    async def prova():
        g._avvia_http(id_d)
        primo = g._compiti[id_d]
        g._avvia_http(id_d)  # doppio clic, o ripresa all'avvio mentre parte
        assert g._compiti[id_d] is primo
        await primo

    asyncio.run(prova())
    assert finale.read_bytes() == CONTENUTO


# --- HLS con ffmpeg ---------------------------------------------------------------------


def _ffmpeg_finto(tmp_path: Path, corpo: str) -> str:
    script = tmp_path / "ffmpeg"
    script.write_text(f"#!{sys.executable}\nimport sys, time\n{corpo}\n")
    script.chmod(0o755)
    return str(script)


def _voce_hls(a: Archivio, tmp_path: Path) -> tuple[int, Path]:
    finale = tmp_path / "film.mkv"
    return a.nuovo_download(titolo="Film", fonte="streamingcommunity", tipo="http", percorso=str(finale)), finale


def test_ffmpeg_che_scrive_tanti_errori_non_si_blocca(tmp_path, monkeypatch):
    """Più di 64 KB su stderr: prima nessuno li leggeva e ffmpeg restava fermo per sempre."""
    finto = _ffmpeg_finto(tmp_path, (
        "sys.stderr.write('x' * 300_000); sys.stderr.flush()\n"
        "print('out_time_us=1000000', flush=True)\n"
        "open(sys.argv[-1], 'wb').write(b'MKV' * 1000)\n"))
    monkeypatch.setattr(modulo_download.shutil, "which", lambda nome: finto)
    g, a = _gestore(tmp_path, lambda r: httpx.Response(500))
    id_d, finale = _voce_hls(a, tmp_path)

    async def prova():
        await asyncio.wait_for(g._scarica_hls(id_d, Path(str(finale) + ".part"), {"video": "http://x/v.m3u8"}, {}), 20)

    asyncio.run(prova())
    assert Path(str(finale) + ".part").read_bytes() == b"MKV" * 1000


def test_ffmpeg_fermo_si_ferma_e_non_resta_acceso(tmp_path, monkeypatch):
    finto = _ffmpeg_finto(tmp_path, "open(sys.argv[-1] + '.pid', 'w').write(str(__import__('os').getpid()))\n"
                                    "time.sleep(600)")
    monkeypatch.setattr(modulo_download.shutil, "which", lambda nome: finto)
    monkeypatch.setattr(modulo_download, "FFMPEG_FERMO", 0.5)
    g, a = _gestore(tmp_path, lambda r: httpx.Response(500))
    id_d, finale = _voce_hls(a, tmp_path)
    parziale = Path(str(finale) + ".part")

    async def prova():
        with pytest.raises(ErroreServizio, match="fermato"):
            await g._scarica_hls(id_d, parziale, {"video": "http://x/v.m3u8"}, {})

    asyncio.run(prova())
    pid = int(Path(str(parziale) + ".pid").read_text())
    stato = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    assert stato == ""  # il processo non c'è più (neanche come zombie)


def test_ffmpeg_che_fallisce_non_lascia_file(tmp_path, monkeypatch):
    finto = _ffmpeg_finto(tmp_path, "open(sys.argv[-1], 'wb').write(b'mezzo'); sys.stderr.write('403'); sys.exit(1)")
    monkeypatch.setattr(modulo_download.shutil, "which", lambda nome: finto)
    g, a = _gestore(tmp_path, lambda r: httpx.Response(500))
    id_d, finale = _voce_hls(a, tmp_path)
    parziale = Path(str(finale) + ".part")
    with pytest.raises(ErroreServizio, match="HLS"):
        asyncio.run(g._scarica_hls(id_d, parziale, {"video": "http://x/v.m3u8"}, {}))
    assert not parziale.exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg non installato")
def test_hls_vero_con_ffmpeg(tmp_path):
    """Un vero flusso HLS servito in locale: le opzioni passate a ffmpeg (rw_timeout, intestazioni) vanno bene."""
    sorgente = tmp_path / "hls"
    sorgente.mkdir()
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=duration=3:size=160x120:rate=10", "-f", "lavfi", "-i", "sine=duration=3",
                    "-c:v", "libx264", "-c:a", "aac", "-hls_time", "1", "-hls_list_size", "0", "-f", "hls",
                    str(sorgente / "index.m3u8")], check=True)
    gestore_http = functools.partial(_Silenzioso, directory=str(sorgente))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), gestore_http)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        g, a = _gestore(tmp_path, lambda r: httpx.Response(500))
        id_d, finale = _voce_hls(a, tmp_path)
        parziale = Path(str(finale) + ".part")
        asyncio.run(g._scarica_hls(id_d, parziale, {"video": f"http://127.0.0.1:{server.server_port}/index.m3u8",
                                                     "durata": 3}, {"Referer": "http://sito/"}))
        assert parziale.stat().st_size > 1000 and parziale.read_bytes()[:4] == b"\x1a\x45\xdf\xa3"  # Matroska
    finally:
        server.shutdown()


class _Silenzioso(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_a):
        pass


# --- torrent ----------------------------------------------------------------------------


class QbitFinto:
    def __init__(self, torrent: list[dict]):
        self.torrent = torrent
        self.richieste = 0
        self.etichette: list[tuple[str, str]] = []

    async def tutti(self):
        self.richieste += 1
        return self.torrent

    async def aggiungi_etichetta(self, hash_torrent, etichetta):
        self.etichette.append((hash_torrent, etichetta))

    async def per_etichetta(self, etichetta):  # non deve più servire nel giro
        raise AssertionError("una richiesta per ogni download")


def test_il_giro_dei_torrent_fa_una_richiesta_sola(tmp_path):
    a = Archivio(tmp_path / "cerca.db")
    ids = [a.nuovo_download(titolo="Torrent", fonte="manuale", tipo="torrent") for _ in range(4)]
    torrent = [
        {"hash": "a" * 40, "name": "Film.Uno.2020", "tags": f"mediabox-{ids[0]}", "progress": 0.5, "state": "downloading",
         "size": 100, "completed": 50, "dlspeed": 10},
        {"hash": "b" * 40, "name": "b" * 40, "tags": f"mediabox-{ids[1]}", "progress": 0, "state": "metaDL"},
        {"hash": "c" * 40, "name": "Film.Tre", "tags": f"mediabox-{ids[2]}", "progress": 1, "state": "moving",
         "content_path": "/vecchio"},
        {"hash": "d" * 40, "name": "Film.Quattro", "tags": f"altro, mediabox-{ids[3]}", "progress": 1,
         "state": "stalledUP", "content_path": "/data/downloads/cerca/Film.Quattro"},
        {"hash": "e" * 40, "name": "Esterno", "tags": "", "category": "manuale", "progress": 0.1, "state": "downloading"},
    ]
    qbit = QbitFinto(torrent)
    g = GestoreDownload(CONFIG, a, None, qbit, None, None, None)
    importati = []

    async def a_radarr(i):
        importati.append(i)

    g._passa_a_radarr = a_radarr

    async def prova():
        elenco = await qbit.tutti()
        await g._adotta_torrent_esterni(elenco)
        await g._controlla_torrent(elenco)
        await asyncio.sleep(0)

    asyncio.run(prova())
    assert qbit.richieste == 1
    assert a.download(ids[0])["titolo"] == "Film.Uno.2020" and a.download(ids[0])["scaricati"] == 50
    assert a.download(ids[1])["titolo"] == "Torrent"  # il nome era l'hash: non è un titolo
    assert a.download(ids[2])["stato"] == "in_corso"  # sta spostando i file: si aspetta
    assert a.download(ids[3])["stato"] == "completato" and importati == [ids[3]]
    adottato = a.download_per_hash("e" * 40)
    assert adottato and adottato["fonte"] == "manuale" and qbit.etichette == [("e" * 40, f"mediabox-{adottato['id']}")]


def test_un_torrent_che_qbittorrent_ha_gia_si_segue_invece_di_dare_errore():
    esadecimale = "c12fe1c06bba254a9dc9f519b335aa7c1367a88a"
    chiamate = []

    def server(r: httpx.Request) -> httpx.Response:
        chiamate.append((r.url.path, dict(r.url.params), r.content.decode(errors="ignore")))
        if r.url.path.endswith("/torrents/add"):
            return httpx.Response(200, text="Fails.")
        if r.url.path.endswith("/torrents/info"):
            return httpx.Response(200, json=[{"hash": esadecimale}] if r.url.params.get("hashes") == esadecimale else [])
        return httpx.Response(200, text="Ok.")

    qbit = QBittorrent(CONFIG, httpx.AsyncClient(transport=httpx.MockTransport(server)))
    asyncio.run(qbit.aggiungi(categoria="cerca", etichetta="mediabox-7", magnet_o_url=f"magnet:?xt=urn:btih:{esadecimale}"))
    assert any(p.endswith("/torrents/addTags") and "mediabox-7" in corpo for p, _, corpo in chiamate)

    # un torrent davvero rotto resta un errore
    with pytest.raises(ErroreServizio, match="non riesce"):
        asyncio.run(qbit.aggiungi(categoria="cerca", etichetta="mediabox-8", magnet_o_url="magnet:?xt=urn:btih:" + "f" * 40))


# --- file .torrent dagli indexer ---------------------------------------------------------


def _scarica(server, url="http://indexer/dl/1"):
    async def prova():
        async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
            return await scarica_torrent(client, url)

    return asyncio.run(prova())


def test_rimando_relativo_e_rimando_a_un_magnet():
    def relativo(r):
        if r.url.path == "/dl/1":
            return httpx.Response(302, headers={"location": "/file/1.torrent"})
        return httpx.Response(200, content=b"d8:announce0:e")

    assert _scarica(relativo) == (None, b"d8:announce0:e")
    magnet = "magnet:?xt=urn:btih:" + "a" * 40
    assert _scarica(lambda r: httpx.Response(301, headers={"location": magnet})) == (magnet, b"")


def test_quello_che_non_e_un_torrent_si_rifiuta():
    with pytest.raises(ValueError, match="non è un file"):
        _scarica(lambda r: httpx.Response(200, text="<html>accesso negato</html>"))
    with pytest.raises(ValueError, match="troppo grande"):
        _scarica(lambda r: httpx.Response(200, content=b"d" + b"x" * (11 * 1024 * 1024)))
    with pytest.raises(ValueError, match="troppi"):
        _scarica(lambda r: httpx.Response(302, headers={"location": "/ancora"}))
    with pytest.raises(httpx.HTTPStatusError):
        _scarica(lambda r: httpx.Response(404))
