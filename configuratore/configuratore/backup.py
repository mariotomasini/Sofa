"""Backup e ripristino di mediabox.

Il backup contiene il file .env, le configurazioni dei servizi e il database
di Cerca. NON contiene film, serie e registrazioni (sono troppo grandi: per
quelli usa un disco esterno).

I servizi vanno fermati prima (lo fa il comando "mediabox backup").
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import tarfile
from pathlib import Path

from .impostazioni import Impostazioni
from .messaggi import ErroreMediabox, info, ok

RADICE_VOLUMI = Path("/volumi")
# La cache di Jellyfin (immagini ridimensionate, transcodifiche) non serve.
ESCLUSI = ("jellyfin_config/cache", "jellyfin_config/log", "jellyfin_config/transcodes")


def _filtro(info_tar: tarfile.TarInfo) -> tarfile.TarInfo | None:
    nome = info_tar.name.removeprefix("./")
    if any(nome.startswith(f"volumi/{e}") for e in ESCLUSI):
        return None
    if nome.endswith((".pid", "-shm", "-wal")) and "cerca" not in nome:
        return None
    return info_tar


def nomi_pericolosi(nomi: list[str]) -> list[str]:
    """I nomi che uscirebbero dalla cartella di destinazione: assoluti, con "..", o un volume
    vuoto. Con "volumi/../x" il ripristino svuotava la cartella sopra /volumi."""
    strani = []
    for nome in nomi:
        parti = nome.replace("\\", "/").split("/")
        if nome.startswith(("/", "\\")) or ".." in parti or (len(nome) > 1 and nome[1] == ":"):
            strani.append(nome)
        elif parti[0] == "volumi" and len(parti) > 1 and parti[1] in ("", "."):
            strani.append(nome)
    return strani


def backup(imp: Impostazioni, cartella_mediabox: Path) -> Path:
    cartella = cartella_mediabox / "backup"
    cartella.mkdir(exist_ok=True)
    nome = f"mediabox-backup-{dt.datetime.now():%Y-%m-%d_%H%M}.tar.gz"
    destinazione = cartella / nome
    info("Creo il backup (può richiedere qualche minuto)…")
    with tarfile.open(destinazione, "w:gz") as tar:
        tar.add(cartella_mediabox / ".env", arcname=".env")
        for volume in sorted(RADICE_VOLUMI.iterdir()):
            if volume.name == "caddy_data":
                continue
            tar.add(volume, arcname=f"volumi/{volume.name}", filter=_filtro)
    # Il backup contiene la password: lo legge solo il proprietario
    os.chmod(destinazione, 0o600)
    try:
        os.chown(destinazione, int(imp.get("PUID", "1000")), int(imp.get("PGID", "1000")))
    except OSError:
        pass
    dimensione = destinazione.stat().st_size / 1024 / 1024
    ok(f"Backup creato: backup/{nome} ({dimensione:.0f} MB)")
    info("Copialo su una chiavetta o su un altro computer per tenerlo al sicuro.")
    return destinazione


def ripristina(imp: Impostazioni, cartella_mediabox: Path, file: str) -> None:
    percorso = Path(file)
    if not percorso.is_absolute():
        # il comando mediabox passa il percorso relativo alla cartella di mediabox
        percorso = cartella_mediabox / file
    if not percorso.exists():
        raise ErroreMediabox(
            f"Non trovo il file di backup {file}.",
            "Copia il file nella cartella \"backup\" di mediabox e indica il suo nome, "
            "es. mediabox restore backup/mediabox-backup-2025-01-31_2100.tar.gz",
        )
    try:
        tar = tarfile.open(percorso, "r:gz")
        # getnames() legge tutto l'archivio: un file troncato o rovinato si scopre qui,
        # prima di aver cancellato qualcosa
        nomi = tar.getnames()
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ErroreMediabox(
            "Il file di backup è rovinato o incompleto: non ho toccato niente.",
            "Ricopialo dalla chiavetta o usa un altro backup.",
        ) from exc
    with tar:
        if ".env" not in nomi:
            raise ErroreMediabox(
                "Questo file non sembra un backup di mediabox.",
                "Usa un file creato con \"mediabox backup\".",
            )
        if strani := nomi_pericolosi(nomi):
            raise ErroreMediabox(
                f"Il backup contiene percorsi non validi ({strani[0]}): non ho toccato niente.",
                "Usa un file creato con \"mediabox backup\".",
            )
        volumi = {n.split("/")[1] for n in nomi if n.startswith("volumi/") and n.count("/") >= 1}
        volumi.discard("")
        for volume in volumi:
            destinazione = RADICE_VOLUMI / volume
            if not destinazione.exists():
                info(f"Salto {volume}: il servizio non è attivo in questa installazione")
                continue
            for elemento in destinazione.iterdir():
                if elemento.is_dir() and not elemento.is_symlink():
                    shutil.rmtree(elemento)
                else:
                    elemento.unlink()
        membri = []
        for m in tar.getmembers():
            if m.name == ".env":
                membri.append(m)
            elif m.name.startswith("volumi/") and (RADICE_VOLUMI / m.name.split("/")[1]).exists():
                membri.append(m)
        # .env torna nella cartella di mediabox, i volumi in /volumi
        for m in membri:
            if m.name == ".env":
                tar.extract(m, cartella_mediabox, filter="data")
            else:
                m.name = m.name.removeprefix("volumi/")
                tar.extract(m, RADICE_VOLUMI, filter="fully_trusted")
    ok("Ripristino completato")
