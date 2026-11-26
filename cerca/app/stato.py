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
TEMPO_DISCO = 10  # secondi massimi per misurare lo spazio
OGNI_QUANTO = 600  # la misura si rinnova ogni 10 minuti


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
        self._ultimo: dict = {"disponibile": False}
        self._misura: asyncio.Future | None = None

    def stato(self) -> dict:
        """L'ultima misura: non tocca mai il disco, così le pagine non aspettano.

        Su Windows la cartella dei film arriva nel contenitore attraverso Docker
        Desktop, e misurarne lo spazio a volte ci mette moltissimo: se lo facessimo
        a ogni pagina, Cerca resterebbe fermo per tutti.
        """
        return dict(self._ultimo)

    async def aggiorna(self) -> dict:
        """Rinnova la misura in un thread a parte, aspettando al massimo TEMPO_DISCO secondi."""
        if self._misura is None or self._misura.done():
            self._misura = asyncio.ensure_future(asyncio.to_thread(self._calcola))
        try:
            self._ultimo = await asyncio.wait_for(asyncio.shield(self._misura), TEMPO_DISCO)
        except TimeoutError:
            log.warning("Il disco dei film è lento a rispondere: uso l'ultima misura dello spazio")
        return self.stato()

    def _calcola(self) -> dict:
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
        info = await self.aggiorna()
        if not info.get("quasi_pieno"):
            return
        if self.pulizia is not None:
            # prima proviamo a liberare spazio da soli; se non si può (es. qBittorrent spento)
            # l'avviso sul telefono deve partire lo stesso
            try:
                await self.pulizia.libera("disco quasi pieno")
            except Exception:  # noqa: BLE001
                log.warning("Pulizia automatica non riuscita", exc_info=True)
            info = await self.aggiorna()
            if not info.get("quasi_pieno"):
                return
        ultimo = float(self.archivio.stato_leggi(CHIAVE_ULTIMO_AVVISO) or 0)
        if time.time() - ultimo < UN_GIORNO:
            return
        link = f"{self.config.indirizzo_pubblico}{self.config.root_path}/spazio"
        await self.avvisami.notifica("Disco quasi pieno", info["messaggio"], link)
        self.archivio.stato_scrivi(CHIAVE_ULTIMO_AVVISO, str(time.time()))

    async def ciclo(self) -> None:
        giri = 0
        while True:
            try:
                # la misura ogni 10 minuti, il controllo (pulizia e avviso) ogni ora
                await (self.controlla() if giri % 6 == 0 else self.aggiorna())
            except Exception:  # noqa: BLE001 - il controllo non deve mai fermare Cerca
                log.exception("Errore nel controllo del disco")
            giri += 1
            await asyncio.sleep(OGNI_QUANTO)
