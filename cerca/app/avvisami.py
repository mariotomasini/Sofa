"""Lista "avvisami": controllo giornaliero dei film che diventano gratis.

Un film è "gratis" quando in Italia compare tra le offerte gratuite con
pubblicità (o gratuite e basta) secondo i dati JustWatch di TMDB.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

import httpx

from .config import Config
from .db import Archivio
from .rete import ErroreServizio
from .tmdb import TMDB

log = logging.getLogger("cerca.avvisami")

OGNI_QUANTO = 24 * 3600
CHIAVE_ULTIMO_CONTROLLO = "avvisami_ultimo_controllo"


def messaggio_notifica(titolo: str, anno: str, provider: list[dict]) -> str:
    nomi = ", ".join(p["nome"] for p in provider) or "un servizio gratuito"
    film = f"{titolo} ({anno})" if anno else titolo
    return f"{film} ora si può vedere gratis su {nomi}."


class Avvisami:
    def __init__(self, config: Config, archivio: Archivio, tmdb: TMDB, client: httpx.AsyncClient) -> None:
        self.config = config
        self.archivio = archivio
        self.tmdb = tmdb
        self.client = client

    async def notifica(self, titolo: str, messaggio: str, link: str = "") -> bool:
        if not self.config.ntfy_topic:
            return False
        corpo = {"topic": self.config.ntfy_topic, "title": titolo, "message": messaggio, "tags": ["tv"]}
        if link:
            corpo["click"] = link
        try:
            r = await self.client.post(self.config.ntfy_server, json=corpo, timeout=20)
            return r.is_success
        except httpx.HTTPError:
            log.warning("Notifica ntfy non inviata")
            return False

    async def controlla(self) -> int:
        """Controlla tutti i film della lista. Restituisce quanti sono diventati gratis."""
        nuovi = 0
        if not self.config.con_tmdb:
            self.archivio.stato_scrivi(CHIAVE_ULTIMO_CONTROLLO, str(time.time()))
            return 0  # senza chiave TMDB non sappiamo dove si guarda un film
        for voce in self.archivio.avvisami_elenco():
            try:
                provider = await self.tmdb.provider(voce["tmdb_id"], durata_cache=3600)
            except ErroreServizio as exc:
                log.info("Controllo di %s saltato: %s", voce["titolo"], exc.cosa)
                continue
            gratis = provider["gratis"]
            link = f"{self.config.indirizzo_pubblico}{self.config.root_path}/film/{voce['tmdb_id']}"
            if gratis and not voce["gratis_dal"]:
                nuovi += 1
                inviata = await self.notifica(
                    "Film gratis!", messaggio_notifica(voce["titolo"], voce["anno"], gratis), link
                )
                self.archivio.avvisami_imposta(voce["tmdb_id"], time.time(), gratis, inviata)
            elif gratis:
                inviata = bool(voce["notificato"])
                if not inviata and self.config.ntfy_topic:
                    # la volta scorsa la notifica non è partita (es. rete giù): si riprova
                    inviata = await self.notifica(
                        "Film gratis!", messaggio_notifica(voce["titolo"], voce["anno"], gratis), link
                    )
                self.archivio.avvisami_imposta(voce["tmdb_id"], voce["gratis_dal"], gratis, inviata)
            elif voce["gratis_dal"]:
                # non è più gratis: torna in attesa
                self.archivio.avvisami_imposta(voce["tmdb_id"], None, [], False)
            await asyncio.sleep(0.5)
        self.archivio.stato_scrivi(CHIAVE_ULTIMO_CONTROLLO, str(time.time()))
        return nuovi

    async def ciclo(self) -> None:
        await asyncio.sleep(30)  # lascia partire con calma gli altri servizi
        while True:
            ultimo = float(self.archivio.stato_leggi(CHIAVE_ULTIMO_CONTROLLO) or 0)
            attesa = ultimo + OGNI_QUANTO - time.time()
            if attesa > 0:
                await asyncio.sleep(min(attesa, 3600))
                continue
            try:
                await self.controlla()
            except Exception:  # noqa: BLE001 - il controllo non deve mai fermare Cerca
                log.exception("Errore nel controllo giornaliero")
            # Sempre: segniamo il controllo come fatto e lasciamo respirare il resto di Cerca.
            # Senza chiave TMDB "controlla" finisce subito: senza questa riga il ciclo girava
            # all'infinito senza mai fermarsi, e Cerca restava bloccato al 100% del processore.
            with contextlib.suppress(Exception):
                self.archivio.stato_scrivi(CHIAVE_ULTIMO_CONTROLLO, str(time.time()))
            await asyncio.sleep(60)
