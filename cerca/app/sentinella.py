"""Sentinella: se Cerca si blocca, dice dove e poi si riavvia da solo.

Un'operazione lenta fatta nel posto sbagliato può fermare tutto Cerca: le pagine
restano in caricamento e nei messaggi non compare più niente. Un thread a parte
controlla che il ciclo principale dia segni di vita ogni pochi secondi:
- dopo 20 secondi di silenzio scrive nei messaggi ("mediabox logs cerca")
  in che punto del codice è fermo, e lo salva in un file che la pagina
  "Controllo" mostra anche dopo il riavvio (utile se sei fuori casa);
- dopo 90 secondi esce, e Docker lo riavvia (restart: unless-stopped).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import sys
import threading
import time
import traceback
from pathlib import Path

log = logging.getLogger("cerca.sentinella")

BATTITO = 5
AVVISO = 20
RIAVVIO = 90
FILE_BLOCCO = "ultimo-blocco.json"


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
    def __init__(self, avviso: float = AVVISO, riavvio: float = RIAVVIO, esci=os._exit,
                 cartella: Path | None = None) -> None:
        self.cartella = cartella
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
            self._salva(fermo_da, riavviato=True)
            sys.stderr.flush()
            self.esci(1)
            return "riavvio"
        if fermo_da >= self.avviso and not self._avvisato:
            self._avvisato = True
            self._dove = dove_si_trova()
            log.error("Cerca è bloccato da %d secondi. Ecco dove si trova:\n%s", fermo_da, self._dove)
            self._salva(fermo_da, riavviato=False)
            return "avviso"
        if fermo_da < self.avviso:
            self._avvisato = False
        return ""

    _avvisato = False
    _dove = ""

    def _salva(self, fermo_da: float, riavviato: bool) -> None:
        if self.cartella is None:
            return
        dati = {"quando": dt.datetime.now().isoformat(timespec="seconds"), "secondi": int(fermo_da),
                "riavviato": riavviato, "dove": self._dove or dove_si_trova()}
        try:
            (self.cartella / FILE_BLOCCO).write_text(json.dumps(dati, ensure_ascii=False), encoding="utf-8")
        except OSError:
            log.warning("Non riesco a salvare dove si è bloccato Cerca")


    def _guarda(self) -> None:
        while not self._fermo.wait(BATTITO):
            self.controlla()

    def avvia(self) -> None:
        threading.Thread(target=self._guarda, name="sentinella", daemon=True).start()

    def ferma(self) -> None:
        self._fermo.set()


def ultimo_blocco(cartella: Path) -> dict | None:
    """Per la pagina Controllo: l'ultimo blocco, con le righe del codice di Cerca dove era fermo."""
    try:
        dati = json.loads((cartella / FILE_BLOCCO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    righe = [r.strip() for r in dati.get("dove", "").splitlines() if "/app/app/" in r or r.strip().startswith("---")]
    dati["punti"] = [r for r in righe if not r.startswith("---")][-8:]
    return dati
