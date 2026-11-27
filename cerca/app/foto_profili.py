"""Una foto per ogni utente di casa, al posto della sola iniziale: in "Chi guarda?" e in Jellyfin.

A chi non ha ancora un'immagine in Jellyfin, Cerca ne dà una scelta a caso da una raccolta di
foto belle (natura, animali, luci calde), sempre diversa da quelle già date agli altri. La foto
va a Jellyfin come immagine dell'utente: così si vede uguale sulla TV, nell'app e qui.
Chi ha già un'immagine (messa a mano in Jellyfin) la tiene: Cerca non la cambia mai.

Le foto sono di Unsplash (licenza libera), servite da picsum.photos. Senza Internet si resta
con le iniziali e si riprova la volta dopo.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random

import httpx

from .rete import ErroreServizio

log = logging.getLogger("cerca.foto_profili")

# scelte a mano tra le foto di picsum.photos: soggetto chiaro anche piccolo, colori caldi o vivi
FOTO = (28, 35, 56, 75, 82, 102, 106, 110, 152, 159, 167, 174, 184, 219, 237, 239, 248, 287, 306,
        309, 323, 360, 400, 402, 433, 476, 503, 509, 517, 525, 537, 553, 564, 582, 593, 631, 639)
INDIRIZZO = "https://picsum.photos/id/{}/512/512"
CHIAVE_USATE = "foto_profili:usate"


def scegli_foto(quante: int, usate: list[int], caso: random.Random | None = None) -> list[int]:
    """Foto diverse tra loro e, finché ce ne sono, diverse da quelle già date."""
    caso = caso or random.Random()
    libere = [f for f in FOTO if f not in usate]
    caso.shuffle(libere)
    if quante > len(libere):  # più utenti che foto: si ricomincia dalla raccolta intera
        altre = [f for f in FOTO if f not in libere]
        caso.shuffle(altre)
        libere += altre
    return libere[:quante]


class FotoProfili:
    def __init__(self, archivio, client: httpx.AsyncClient, jellyfin) -> None:
        self.archivio = archivio
        self.client = client
        self.jellyfin = jellyfin
        self._blocco = asyncio.Lock()
        self._compito: asyncio.Task | None = None

    def _usate(self) -> list[int]:
        try:
            return [int(x) for x in json.loads(self.archivio.stato_leggi(CHIAVE_USATE) or "[]")]
        except (TypeError, ValueError):
            return []

    async def assegna(self) -> int:
        """Dà una foto a chi non ce l'ha. Quante ne ha date."""
        if self._blocco.locked():
            return 0
        async with self._blocco:
            try:
                senza = [p for p in await self.jellyfin.profili() if not p.get("immagine")]
            except ErroreServizio:
                return 0
            if not senza:
                return 0
            usate = self._usate()
            date = 0
            for profilo, foto in zip(senza, scegli_foto(len(senza), usate), strict=False):
                try:
                    r = await self.client.get(INDIRIZZO.format(foto), follow_redirects=True, timeout=30)
                    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
                        continue
                    await self.jellyfin.imposta_immagine(profilo["id"], r.content,
                                                         r.headers["content-type"].split(";")[0])
                except (httpx.HTTPError, ErroreServizio) as exc:
                    log.info("Foto per %s non assegnata: %s", profilo["nome"], exc)
                    continue
                usate.append(foto)
                date += 1
            self.archivio.stato_scrivi(CHIAVE_USATE, json.dumps(usate[-len(FOTO):]))
            if date:
                log.info("Foto profilo assegnate: %s", date)
            return date

    def in_sottofondo(self) -> None:
        """Dalla pagina "Chi guarda?": non la fa aspettare, le foto si vedono alla prossima apertura."""
        if self._compito is None or self._compito.done():
            self._compito = asyncio.create_task(self.assegna())
