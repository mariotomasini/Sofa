"""L'ordine della coda di qBittorrent: le puntate dalla prima all'ultima.

Sonarr manda i torrent a qBittorrent nell'ordine in cui li trova, che non è quello
delle puntate. qBittorrent ne scarica pochi alla volta (la coda la imposta il
configuratore), quindi l'ordine conta: qui le puntate di ogni serie vanno in fila
per stagione e numero, nei posti della coda che avevano già (i film restano dove sono).

"Scarica prima questa" mette un torrent in cima a tutto: quelli scelti così passano
davanti a tutti, nell'ordine in cui sono stati scelti (l'ultimo scelto per primo).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

log = logging.getLogger(__name__)

CHIAVE_PRIMI = "download_primi"
MAX_PRIMI = 30
OGNI = 60  # secondi tra un controllo e l'altro

RE_PUNTATA = re.compile(r"\bS(\d{1,2})[ ._-]?E(\d{1,3})\b|\b(\d{1,2})x(\d{2,3})\b", re.I)
RE_STAGIONE = re.compile(r"\b(?:S|Season[ ._-]?|Stagione[ ._-]?)(\d{1,2})\b", re.I)
RE_ANNO = re.compile(r"\b(19|20)\d{2}\b")


def _nome(testo: str) -> str:
    testo = RE_ANNO.sub(" ", re.sub(r"[\W_]+", " ", testo))
    return " ".join(testo.lower().split())


def numero(nome: str) -> tuple[str, int, int] | None:
    """(serie, stagione, puntata) dal nome del torrent; puntata 0 per una stagione intera. None se è un film."""
    if m := RE_PUNTATA.search(nome):
        stagione, puntata = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        return _nome(nome[:m.start()]), int(stagione), int(puntata)
    if m := RE_STAGIONE.search(nome):
        return _nome(nome[:m.start()]), int(m.group(1)), 0
    return None


def in_coda(torrent: list[dict]) -> list[dict]:
    """I torrent ancora da finire, nell'ordine attuale della coda (priority 1, 2, …)."""
    return sorted((t for t in torrent if (t.get("priority") or 0) > 0), key=lambda t: t["priority"])


def ordine_voluto(torrent: list[dict], primi: list[str]) -> list[str]:
    """Gli hash nell'ordine voluto: prima quelli scelti, poi la coda con le puntate in fila."""
    coda = in_coda(torrent)
    numeri = {t["hash"]: numero(t.get("name") or "") for t in coda}
    puntate = [t for t in coda if numeri[t["hash"]]]
    # le serie restano nell'ordine in cui sono arrivate; dentro ogni serie, stagione e puntata
    arrivo: dict[str, float] = {}
    for t in puntate:
        serie = numeri[t["hash"]][0]
        arrivo[serie] = min(arrivo.get(serie, float("inf")), t.get("added_on") or 0)
    in_fila = iter(sorted(puntate, key=lambda t: (arrivo[numeri[t["hash"]][0]], *numeri[t["hash"]])))
    ordine = [next(in_fila)["hash"] if numeri[t["hash"]] else t["hash"] for t in coda]
    scelti = [h for h in primi if h in numeri]
    return scelti + [h for h in ordine if h not in scelti]


def posizioni(torrent: list[dict]) -> dict[str, int]:
    """hash → posto nella coda (1 = il prossimo a partire)."""
    return {t["hash"]: i + 1 for i, t in enumerate(in_coda(torrent))}


class OrdineDownload:
    def __init__(self, archivio, qbit) -> None:
        self.archivio = archivio
        self.qbit = qbit

    def primi(self) -> list[str]:
        try:
            return json.loads(self.archivio.stato_leggi(CHIAVE_PRIMI) or "[]")
        except ValueError:
            return []

    def _salva_primi(self, primi: list[str]) -> None:
        self.archivio.stato_scrivi(CHIAVE_PRIMI, json.dumps(primi[:MAX_PRIMI]))

    async def ordina(self) -> bool:
        """Rimette in ordine la coda se serve. True se ha cambiato qualcosa."""
        torrent = await self.qbit.tutti()
        primi = await asyncio.to_thread(self.primi)
        presenti = {t["hash"] for t in in_coda(torrent)}
        if (ancora := [h for h in primi if h in presenti]) != primi:
            await asyncio.to_thread(self._salva_primi, ancora)  # quelli finiti non servono più
        voluto = ordine_voluto(torrent, ancora)
        if voluto == [t["hash"] for t in in_coda(torrent)]:
            return False
        for h in voluto:  # in fondo uno alla volta: alla fine la coda è nell'ordine voluto
            await self.qbit.in_fondo(h)
        return True

    async def scarica_prima(self, hash_torrent: str) -> None:
        hash_torrent = hash_torrent.lower()
        primi = [h for h in await asyncio.to_thread(self.primi) if h != hash_torrent]
        await asyncio.to_thread(self._salva_primi, [hash_torrent, *primi])
        await self.ordina()

    async def ciclo(self) -> None:
        while True:
            try:
                await self.ordina()
            except Exception:  # noqa: BLE001 - qBittorrent spento o coda disattivata: si riprova
                log.debug("Coda non riordinata", exc_info=True)
            await asyncio.sleep(OGNI)
