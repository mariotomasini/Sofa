"""Le foto e i video di Immich non si cancellano mai da mediabox.

Stanno in MEDIA_DIR/foto (in Cerca /data/foto), fuori dalle cartelle che la pulizia e la
pagina Spazio conoscono (downloads/ e media/). Questo controllo è una cintura di
sicurezza in più: anche per sbaglio (un percorso strano, un collegamento, una
configurazione cambiata a mano) niente dentro la cartella delle foto viene toccato,
né le foto né i video importati dall'iPhone, né quelle riprese dal cloud.
"""

from __future__ import annotations

import os
from pathlib import Path

CARTELLE_FOTO = (Path("/data/foto"), Path("/data/foto-ripristinate"))


class FotoProtette(PermissionError):
    """Qualcuno ha provato a cancellare dentro la cartella delle foto (si salta, come un
    file che non si può cancellare)."""


def _vero(percorso: Path) -> Path:
    # segue i collegamenti e toglie i ".." : conta dove finisce davvero il percorso
    return Path(os.path.realpath(percorso))


def tocca_le_foto(percorso: Path | str, cartelle: tuple[Path, ...] | None = None) -> bool:
    """Vero se il percorso è una cartella delle foto, sta dentro, o la contiene."""
    cartelle = CARTELLE_FOTO if cartelle is None else cartelle
    candidati = {Path(os.path.normpath(percorso)), _vero(Path(percorso))}
    for c in cartelle:
        for radice in {c, _vero(c)}:
            for p in candidati:
                if p == radice or radice in p.parents or p in radice.parents:
                    return True
    return False


def controlla(percorso: Path | str) -> None:
    if tocca_le_foto(percorso):
        raise FotoProtette(f"Le foto non si cancellano mai: {percorso}")
