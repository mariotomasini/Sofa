"""Sentinella: se Cerca si blocca, dice dove e poi si riavvia da solo.

Un'operazione lenta fatta nel posto sbagliato può fermare tutto Cerca: le pagine
restano in caricamento e nei messaggi non compare più niente. Un thread a parte
controlla che il ciclo principale dia segni di vita ogni pochi secondi:
- dopo 30 secondi di silenzio scrive nei messaggi ("mediabox logs cerca")
  in che punto del codice è fermo;
- dopo 3 minuti esce, e Docker lo riavvia (restart: unless-stopped).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import traceback

log = logging.getLogger("cerca.sentinella")

BATTITO = 5
AVVISO = 30
RIAVVIO = 180


def dove_si_trova() -> str:
    """Lo stack di ogni thread tranne la sentinella: dice quale operazione tiene fermo Cerca."""
    righe = []
    for ident, frame in sys._current_frames().items():
        if ident == threading.get_ident():
            continue
        nome = next((t.name for t in threading.enumerate() if t.ident == ident), str(ident))
        righe.append(f"--- thread {nome} ---\n" + "".join(traceback.format_stack(frame)))
    return "\n".join(righe)


class Sentinella:
    def __init__(self, avviso: float = AVVISO, riavvio: float = RIAVVIO, esci=os._exit) -> None:
        self.avviso = avviso
        self.riavvio = riavvio
        self.esci = esci
        self.ultimo = time.monotonic()
        self._fermo = threading.Event()

    async def battito(self) -> None:
        """Gira nel ciclo principale: finché aggiorna l'orario, Cerca non è bloccato."""
        while True:
            self.ultimo = time.monotonic()
            await asyncio.sleep(BATTITO)

    def controlla(self) -> str:
        """Una passata del controllo. Restituisce cosa ha fatto (per i test)."""
        fermo_da = time.monotonic() - self.ultimo
        if fermo_da >= self.riavvio:
            log.error("Cerca è bloccato da %d secondi: mi riavvio.", fermo_da)
            sys.stderr.flush()
            self.esci(1)
            return "riavvio"
        if fermo_da >= self.avviso and not self._avvisato:
            self._avvisato = True
            log.error("Cerca è bloccato da %d secondi. Ecco dove si trova:\n%s", fermo_da, dove_si_trova())
            return "avviso"
        if fermo_da < self.avviso:
            self._avvisato = False
        return ""

    _avvisato = False

    def _guarda(self) -> None:
        while not self._fermo.wait(BATTITO):
            self.controlla()

    def avvia(self) -> None:
        threading.Thread(target=self._guarda, name="sentinella", daemon=True).start()

    def ferma(self) -> None:
        self._fermo.set()
