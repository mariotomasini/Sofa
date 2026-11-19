"""Messaggi per l'utente, sempre in italiano semplice."""

from __future__ import annotations

import sys

_COLORE = sys.stdout.isatty()


def _c(codice: str, testo: str) -> str:
    return f"\033[{codice}m{testo}\033[0m" if _COLORE else testo


def titolo(testo: str) -> None:
    print("\n" + _c("1;36", f"== {testo} =="), flush=True)


def info(testo: str) -> None:
    print(f"  · {testo}", flush=True)


def ok(testo: str) -> None:
    print(_c("32", f"  ✔ {testo}"), flush=True)


def avviso(testo: str) -> None:
    print(_c("33", f"  ! {testo}"), flush=True)


def errore(testo: str) -> None:
    print(_c("31", f"  ✘ {testo}"), file=sys.stderr, flush=True)


class ErroreMediabox(Exception):
    """Errore con spiegazione e cosa fare, da mostrare all'utente."""

    def __init__(self, cosa: str, cosa_fare: str = "") -> None:
        super().__init__(cosa)
        self.cosa = cosa
        self.cosa_fare = cosa_fare

    def mostra(self) -> None:
        errore(self.cosa)
        if self.cosa_fare:
            print(f"    Cosa fare: {self.cosa_fare}", file=sys.stderr, flush=True)
