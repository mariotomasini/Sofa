"""Client HTTP condiviso, limiti di frequenza e errori leggibili."""

from __future__ import annotations

import asyncio
import base64
import re
import time
from urllib.parse import parse_qs, urljoin, urlparse

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
        self._lock: asyncio.Lock | None = None
        self._ciclo: asyncio.AbstractEventLoop | None = None

    async def attendi(self) -> None:
        # Un lock vale per un solo ciclo di eventi (nei test ne nasce uno per prova)
        ciclo = asyncio.get_running_loop()
        if self._lock is None or self._ciclo is not ciclo:
            self._lock, self._ciclo = asyncio.Lock(), ciclo
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


MAX_TORRENT = 10 * 1024 * 1024  # un file .torrent vero è di pochi KB, al massimo qualche MB
MAX_SALTI = 5


def hash_da_magnet(magnet: str) -> str:
    """L'hash (40 cifre esadecimali, minuscole) di un magnet "urn:btih:", anche in base32. "" se manca."""
    for xt in parse_qs(urlparse(magnet.strip()).query).get("xt", []):
        m = re.fullmatch(r"urn:btih:([0-9a-zA-Z]+)", xt.strip())
        if not m:
            continue
        valore = m.group(1)
        if re.fullmatch(r"[0-9a-fA-F]{40}", valore):
            return valore.lower()
        if len(valore) == 32:
            try:
                return base64.b32decode(valore.upper()).hex()
            except ValueError:
                return ""
    return ""


async def scarica_torrent(client: httpx.AsyncClient, url: str, *, timeout: float = 60) -> tuple[str | None, bytes]:
    """Il file .torrent a un indirizzo: (magnet, b"") se l'indirizzo porta a un magnet, altrimenti (None, file).

    - i rimandi si seguono a mano: molti indexer rimandano a un "magnet:", che httpx non sa aprire,
      e alcuni rimandano con un indirizzo relativo ("/download?id=…");
    - si legge al massimo MAX_TORRENT: un link sbagliato verso un video non riempie la memoria;
    - si controlla che sia davvero un .torrent (inizia con "d"), non una pagina di errore.
    Le eccezioni sono httpx.HTTPError (rete) o ValueError (non è un torrent): i chiamanti
    le traducono nel loro messaggio.
    """
    for _ in range(MAX_SALTI + 1):
        async with client.stream("GET", url, follow_redirects=False, timeout=timeout) as r:
            if r.is_redirect:
                destinazione = r.headers.get("location", "").strip()
                if destinazione.lower().startswith("magnet:"):
                    return destinazione, b""
                if not destinazione:
                    raise ValueError("rimando senza destinazione")
                url = urljoin(str(r.url), destinazione)
                continue
            r.raise_for_status()
            contenuto = bytearray()
            async for blocco in r.aiter_bytes():
                contenuto += blocco
                if len(contenuto) > MAX_TORRENT:
                    raise ValueError("file troppo grande per essere un .torrent")
        if not contenuto.startswith(b"d"):
            raise ValueError("non è un file .torrent")
        return None, bytes(contenuto)
    raise ValueError("troppi rimandi")
