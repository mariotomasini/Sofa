"""Piccoli aiuti per le chiamate HTTP verso i servizi."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from .messaggi import ErroreMediabox, info


def minuti(secondi: float) -> str:
    secondi = int(secondi)
    return f"{secondi // 60}:{secondi % 60:02d}"


class AvvisoAttesa:
    """Mentre si aspetta un servizio, ogni 20 secondi dice da quanto e fino a quando.

    Così chi guarda (nel terminale o nella pagina di avanzamento) vede che non è bloccato."""

    OGNI = 20

    def __init__(self, nome: str, timeout: float) -> None:
        self.nome = nome
        self.timeout = timeout
        self.inizio = time.monotonic()
        self.ultimo: float | None = None

    def ogni_tanto(self) -> None:
        adesso = time.monotonic()
        if self.ultimo is None:
            info(f"Attendo che {self.nome} sia pronto… (la prima accensione può richiedere qualche minuto)")
            self.ultimo = adesso
        elif adesso - self.ultimo >= self.OGNI:
            info(f"Attendo che {self.nome} sia pronto… {minuti(adesso - self.inizio)} (al massimo {minuti(self.timeout)})")
            self.ultimo = adesso


def attendi(
    nome: str,
    url: str,
    *,
    timeout: float = 600,  # sui PC lenti la prima accensione di Radarr & co. supera i 5 minuti
    accetta: Callable[[httpx.Response], bool] | None = None,
    pausa: float = 3,
) -> httpx.Response:
    """Attende che un servizio risponda. Restituisce l'ultima risposta valida."""
    accetta = accetta or (lambda r: r.status_code < 500)
    scadenza = time.monotonic() + timeout
    ultimo_errore = ""
    attesa = AvvisoAttesa(nome, timeout)
    while time.monotonic() < scadenza:
        try:
            risposta = httpx.get(url, timeout=10, follow_redirects=True)
            if accetta(risposta):
                return risposta
            ultimo_errore = f"risposta {risposta.status_code}"
        except httpx.HTTPError as exc:
            ultimo_errore = type(exc).__name__
        attesa.ogni_tanto()
        time.sleep(pausa)
    raise ErroreMediabox(
        f"{nome} non si è avviato in tempo ({ultimo_errore}).",
        f"Guarda i messaggi con \"mediabox logs {nome.lower()}\", poi riprova con \"mediabox configura\".",
    )


def controlla(risposta: httpx.Response, cosa: str) -> httpx.Response:
    """Solleva un errore leggibile se la risposta non è andata a buon fine."""
    if risposta.is_success:
        return risposta
    dettaglio = risposta.text[:300].strip()
    raise ErroreMediabox(
        f"Non sono riuscito a {cosa} (codice {risposta.status_code}). {dettaglio}",
        "Riprova con \"mediabox configura\". Se l'errore resta, apri una segnalazione allegando questo messaggio.",
    )
