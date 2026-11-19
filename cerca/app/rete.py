"""Client HTTP condiviso, limiti di frequenza e errori leggibili."""

from __future__ import annotations

import asyncio
import time

import httpx

# MusicBrainz e Internet Archive chiedono un User-Agent con un indirizzo di contatto
AGENTE = "mediabox-cerca/1.2 ( https://github.com/mariotomasini/Sofa )"


class ErroreServizio(Exception):
    """Errore da mostrare all'utente: cosa è successo e cosa fare."""

    def __init__(self, cosa: str, cosa_fare: str = "") -> None:
        super().__init__(cosa)
        self.cosa = cosa
        self.cosa_fare = cosa_fare

    def come_dizionario(self) -> dict:
        return {"errore": self.cosa, "cosa_fare": self.cosa_fare}


class Limitatore:
    """Non più di `per_secondo` richieste al secondo verso un servizio."""

    def __init__(self, per_secondo: float) -> None:
        self.intervallo = 1.0 / per_secondo
        self._prossima = 0.0
        self._lock = asyncio.Lock()

    async def attendi(self) -> None:
        async with self._lock:
            adesso = time.monotonic()
            if self._prossima > adesso:
                await asyncio.sleep(self._prossima - adesso)
                adesso = time.monotonic()
            self._prossima = adesso + self.intervallo


def nuovo_client(timeout: float = 20) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=10),
        headers={"User-Agent": AGENTE},
        follow_redirects=True,
    )


def non_raggiungibile(nome: str, percorso: str) -> ErroreServizio:
    return ErroreServizio(
        f"{nome} non risponde.",
        f"Controlla che sia acceso: sul PC di mediabox scrivi \"mediabox status\". "
        f"Puoi aprirlo da {percorso}.",
    )
