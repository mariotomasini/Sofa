"""Piccoli aiuti per le chiamate HTTP verso i servizi."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from .messaggi import ErroreMediabox, info


def attendi(
    nome: str,
    url: str,
    *,
    timeout: float = 300,
    accetta: Callable[[httpx.Response], bool] | None = None,
    pausa: float = 3,
) -> httpx.Response:
    """Attende che un servizio risponda. Restituisce l'ultima risposta valida."""
    accetta = accetta or (lambda r: r.status_code < 500)
    scadenza = time.monotonic() + timeout
    ultimo_errore = ""
    annunciato = False
    while time.monotonic() < scadenza:
        try:
            risposta = httpx.get(url, timeout=10, follow_redirects=True)
            if accetta(risposta):
                return risposta
            ultimo_errore = f"risposta {risposta.status_code}"
        except httpx.HTTPError as exc:
            ultimo_errore = type(exc).__name__
        if not annunciato:
            info(f"Attendo che {nome} sia pronto…")
            annunciato = True
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
