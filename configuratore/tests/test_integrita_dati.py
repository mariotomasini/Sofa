"""Integrità dei dati del configuratore: il file .env (password e chiavi) e il backup.

Un .env rovinato lascia mediabox senza password né chiavi; un ripristino sbagliato
cancella le configurazioni dei servizi. Questi test proteggono entrambe le cose.
"""

from __future__ import annotations

import io
import os
import stat
import tarfile
from pathlib import Path

import pytest

from configuratore import backup, piano_impostazioni
from configuratore.impostazioni import Impostazioni, leggi_env, scrivi_atomico
from configuratore.menu import aggiorna_env, percorso_valido
from configuratore.messaggi import ErroreMediabox
from configuratore.web.procedura import DatiNonValidi, valida_password

# --- il file .env ---------------------------------------------------------------------


def test_aggiorna_env_cambia_solo_le_righe_indicate(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# commento in cima\nADMIN_PASSWORD=vecchia\n\n# sezione\nWITH_SONARR=no\nWITH_SONARR=si\nALTRO=x\n")
    aggiorna_env(env, {"WITH_SONARR": "si", "NUOVA": "1"})
    righe = env.read_text().splitlines()
    assert righe == ["# commento in cima", "ADMIN_PASSWORD=vecchia", "", "# sezione", "WITH_SONARR=si", "ALTRO=x",
                     "NUOVA=1"]
    # rifare la stessa modifica non cambia niente (idempotente)
    prima = env.read_bytes()
    aggiorna_env(env, {"WITH_SONARR": "si", "NUOVA": "1"})
    assert env.read_bytes() == prima


def test_aggiorna_env_mantiene_i_permessi_privati(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ADMIN_PASSWORD=segreta\n")
    os.chmod(env, 0o600)
    aggiorna_env(env, {"WITH_LIDARR": "si"})
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert leggi_env(env) == {"ADMIN_PASSWORD": "segreta", "WITH_LIDARR": "si"}


@pytest.mark.parametrize("valore", ["abc\nWITH_IMMICH=si", "abc\rX=1", "a\x00b"])
def test_un_a_capo_in_un_valore_non_aggiunge_impostazioni(tmp_path, valore):
    env = tmp_path / ".env"
    env.write_text("ADMIN_PASSWORD=vecchia\n")
    with pytest.raises(ValueError):
        aggiorna_env(env, {"ADMIN_PASSWORD": valore})
    assert env.read_text() == "ADMIN_PASSWORD=vecchia\n"  # il file non è stato toccato


def test_una_chiave_non_valida_si_rifiuta(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\n")
    for chiave in ("", "minuscola", "CON SPAZIO", "X=Y"):
        with pytest.raises(ValueError):
            aggiorna_env(env, {chiave: "1"})
    assert env.read_text() == "A=1\n"


def test_la_scrittura_e_tutto_o_niente(tmp_path, monkeypatch):
    """Se la scrittura si interrompe (disco pieno, spegnimento) il .env di prima resta intero."""
    env = tmp_path / ".env"
    env.write_text("ADMIN_PASSWORD=segreta\nTMDB_API_KEY=abc\n")
    prima = env.read_bytes()

    def replace_rotto(*_a, **_kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("configuratore.impostazioni.os.replace", replace_rotto)
    with pytest.raises(OSError):
        aggiorna_env(env, {"WITH_SONARR": "si"})
    assert env.read_bytes() == prima
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]  # nessun file temporaneo rimasto


def test_scrivi_atomico_con_permessi_espliciti(tmp_path):
    file = tmp_path / "sotto" / ".env"
    scrivi_atomico(file, "A=1\n", modo=0o600)
    assert file.read_text() == "A=1\n" and stat.S_IMODE(file.stat().st_mode) == 0o600
    scrivi_atomico(file, "A=2\n")  # senza modo: tiene quelli di prima
    assert file.read_text() == "A=2\n" and stat.S_IMODE(file.stat().st_mode) == 0o600


def test_valori_con_caratteri_speciali_tornano_identici(tmp_path):
    env = tmp_path / ".env"
    env.write_text("")
    valori = {"ADMIN_PASSWORD": "p@ss$word=con#cancelletto", "MEDIA_DIR": "/mnt/disco esterno/città"}
    aggiorna_env(env, valori)
    assert leggi_env(env) == valori


def test_la_password_non_puo_contenere_a_capo():
    with pytest.raises(DatiNonValidi):
        valida_password("passwordlunga\nWITH_X=si", "passwordlunga\nWITH_X=si")
    with pytest.raises(DatiNonValidi):
        valida_password("con\ttabulazione", "con\ttabulazione")
    assert valida_password("unaPassword9", "unaPassword9") == "unaPassword9"


def test_il_percorso_su_windows_non_puo_contenere_a_capo():
    assert percorso_valido("D:\\Mediabox", windows=True) == "D:/Mediabox"
    assert percorso_valido("D:\\Media\nWITH_X=si", windows=True) is None
    assert percorso_valido("/mnt/x\nWITH_X=si", windows=False) is None


def test_il_piano_si_scrive_per_intero_e_si_somma(tmp_path):
    piano_impostazioni.scrivi(tmp_path, piano_impostazioni.Piano(prepara=False, configura=["radarr"]))
    somma = piano_impostazioni.scrivi(tmp_path, piano_impostazioni.Piano(prepara=True, configura=["sonarr"]))
    assert somma.prepara and somma.configura == ["radarr", "sonarr"]
    assert piano_impostazioni.leggi(tmp_path) == somma
    file = tmp_path / piano_impostazioni.FILE
    assert [x.name for x in file.parent.iterdir()] == [file.name]  # nessun file temporaneo rimasto


# --- backup e ripristino ----------------------------------------------------------------


def _archivio(percorso: Path, membri: dict[str, bytes]) -> Path:
    with tarfile.open(percorso, "w:gz") as tar:
        for nome, contenuto in membri.items():
            info = tarfile.TarInfo(nome)
            info.size = len(contenuto)
            tar.addfile(info, io.BytesIO(contenuto))
    return percorso


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    volumi = tmp_path / "volumi"
    (volumi / "radarr_config").mkdir(parents=True)
    (volumi / "radarr_config" / "config.xml").write_text("ATTUALE")
    (volumi / "cerca_dati").mkdir()
    (volumi / "cerca_dati" / "cerca.db").write_bytes(b"db attuale")
    mediabox = tmp_path / "mediabox"
    mediabox.mkdir()
    (mediabox / ".env").write_text("ADMIN_PASSWORD=attuale\n")
    fuori = tmp_path / "fuori.txt"
    fuori.write_text("NON TOCCARE")
    monkeypatch.setattr(backup, "RADICE_VOLUMI", volumi)
    return volumi, mediabox, fuori


def test_backup_e_ripristino_riportano_i_dati_identici(ambiente, tmp_path):
    volumi, mediabox, _ = ambiente
    imp = Impostazioni({"PUID": str(os.getuid()), "PGID": str(os.getgid())})
    file = backup.backup(imp, mediabox)
    assert stat.S_IMODE(file.stat().st_mode) == 0o600  # dentro c'è la password
    # dopo il backup cambia tutto
    (volumi / "radarr_config" / "config.xml").write_text("CAMBIATO")
    (volumi / "radarr_config" / "nuovo.txt").write_text("da togliere")
    (volumi / "cerca_dati" / "cerca.db").write_bytes(b"rovinato")
    (mediabox / ".env").write_text("ADMIN_PASSWORD=cambiata\n")
    backup.ripristina(imp, mediabox, str(file))
    assert (volumi / "radarr_config" / "config.xml").read_text() == "ATTUALE"
    assert not (volumi / "radarr_config" / "nuovo.txt").exists()
    assert (volumi / "cerca_dati" / "cerca.db").read_bytes() == b"db attuale"
    assert (mediabox / ".env").read_text() == "ADMIN_PASSWORD=attuale\n"


def test_un_backup_troncato_non_cancella_niente(ambiente, tmp_path):
    volumi, mediabox, _ = ambiente
    buono = _archivio(tmp_path / "buono.tar.gz", {".env": b"A=1\n", "volumi/radarr_config/config.xml": os.urandom(200_000)})
    troncato = tmp_path / "troncato.tar.gz"
    troncato.write_bytes(buono.read_bytes()[: buono.stat().st_size // 2])
    with pytest.raises(ErroreMediabox, match="rovinato"):
        backup.ripristina(Impostazioni(), mediabox, str(troncato))
    assert (volumi / "radarr_config" / "config.xml").read_text() == "ATTUALE"
    assert (mediabox / ".env").read_text() == "ADMIN_PASSWORD=attuale\n"


@pytest.mark.parametrize("nome", [
    "volumi/../fuori.txt",
    "volumi/radarr_config/../../fuori.txt",
    "/etc/passwd",
    "volumi//x",
    "volumi/./x",
])
def test_un_backup_con_percorsi_che_escono_si_rifiuta_senza_toccare_niente(ambiente, tmp_path, nome):
    volumi, mediabox, fuori = ambiente
    file = _archivio(tmp_path / "strano.tar.gz", {".env": b"A=1\n", nome: b"cattivo"})
    with pytest.raises(ErroreMediabox, match="percorsi non validi"):
        backup.ripristina(Impostazioni(), mediabox, str(file))
    assert fuori.read_text() == "NON TOCCARE"
    assert (volumi / "radarr_config" / "config.xml").read_text() == "ATTUALE"
    assert (volumi / "cerca_dati" / "cerca.db").read_bytes() == b"db attuale"


def test_un_file_che_non_e_un_backup_non_tocca_niente(ambiente, tmp_path):
    volumi, mediabox, _ = ambiente
    file = _archivio(tmp_path / "altro.tar.gz", {"volumi/radarr_config/config.xml": b"x"})
    with pytest.raises(ErroreMediabox, match="non sembra un backup"):
        backup.ripristina(Impostazioni(), mediabox, str(file))
    assert (volumi / "radarr_config" / "config.xml").read_text() == "ATTUALE"
    non_tar = tmp_path / "non-tar.tar.gz"
    non_tar.write_bytes(b"non sono un archivio")
    with pytest.raises(ErroreMediabox):
        backup.ripristina(Impostazioni(), mediabox, str(non_tar))


def test_i_volumi_non_attivi_si_saltano(ambiente, tmp_path):
    volumi, mediabox, _ = ambiente
    file = _archivio(tmp_path / "b.tar.gz", {".env": b"A=1\n", "volumi/lidarr_config/x": b"x",
                                              "volumi/radarr_config/config.xml": b"DAL BACKUP"})
    backup.ripristina(Impostazioni(), mediabox, str(file))
    assert not (volumi / "lidarr_config").exists()
    assert (volumi / "radarr_config" / "config.xml").read_text() == "DAL BACKUP"
    assert (volumi / "cerca_dati" / "cerca.db").read_bytes() == b"db attuale"  # non nel backup: resta


def test_nomi_pericolosi():
    assert backup.nomi_pericolosi([".env", "volumi/a/b.txt", "volumi/a", "volumi"]) == []
    assert backup.nomi_pericolosi(["volumi/../x", "/abs", "C:/win", "a/../../b", "volumi/a/..\\..\\b"]) == [
        "volumi/../x", "/abs", "C:/win", "a/../../b", "volumi/a/..\\..\\b"]
