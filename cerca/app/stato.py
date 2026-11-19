"""Spazio libero sul disco dei media e avviso quando sta per finire."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from .config import Config
from .db import Archivio

log = logging.getLogger("cerca.stato")

UN_GIORNO = 24 * 3600
CHIAVE_ULTIMO_AVVISO = "disco_ultimo_avviso"


def spazio(percorso: Path) -> dict:
    uso = shutil.disk_usage(percorso)
    libero_pct = round(100 * uso.free / uso.total, 1) if uso.total else 0.0
    return {"totale": uso.total, "libero": uso.free, "usato": uso.used, "libero_percento": libero_pct}


def messaggio_disco(info: dict) -> str:
    gb = info["libero"] / 1024**3
    return (
        f"Il disco dei film è quasi pieno: restano {gb:.0f} GB ({info['libero_percento']:.0f}%). "
        "Cancella qualche film già visto da Jellyfin, oppure sposta mediabox su un disco più grande "
        "con \"mediabox impostazioni\"."
    )


class ControlloDisco:
    def __init__(self, config: Config, archivio: Archivio, avvisami, pulizia=None) -> None:
        self.config = config
        self.archivio = archivio
        self.avvisami = avvisami  # per riusare l'invio delle notifiche ntfy
        self.pulizia = pulizia

    def stato(self) -> dict:
        try:
            info = spazio(self.config.cartella_download.parent)
        except OSError:
            return {"disponibile": False}
        info["disponibile"] = True
        info["quasi_pieno"] = info["libero_percento"] < self.config.soglia_disco
        info["soglia"] = self.config.soglia_disco
        if info["quasi_pieno"]:
            info["messaggio"] = messaggio_disco(info)
        return info

    async def controlla(self) -> None:
        info = self.stato()
        if not info.get("quasi_pieno"):
            return
        if self.pulizia is not None:
            # prima proviamo a liberare spazio da soli
            await self.pulizia.libera("disco quasi pieno")
            info = self.stato()
            if not info.get("quasi_pieno"):
                return
        ultimo = float(self.archivio.stato_leggi(CHIAVE_ULTIMO_AVVISO) or 0)
        if time.time() - ultimo < UN_GIORNO:
            return
        await self.avvisami.notifica("Disco quasi pieno", info["messaggio"], f"{self.config.indirizzo_pubblico}/cerca/")
        self.archivio.stato_scrivi(CHIAVE_ULTIMO_AVVISO, str(time.time()))

    async def ciclo(self) -> None:
        while True:
            try:
                await self.controlla()
            except Exception:  # noqa: BLE001 - il controllo non deve mai fermare Cerca
                log.exception("Errore nel controllo del disco")
            await asyncio.sleep(3600)
