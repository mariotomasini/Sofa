"""Prowlarr: ricerca negli indexer configurati dall'utente."""

from __future__ import annotations

import hashlib

import httpx

from .config import Config
from .db import Archivio
from .rete import ErroreServizio, non_raggiungibile

DIECI_MINUTI = 600


def id_risultato(r: dict) -> str:
    """Identificativo stabile di un risultato (serve per avviarne il download)."""
    base = f"{r.get('indexerId')}|{r.get('guid')}"
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def riassunto(r: dict) -> dict:
    return {
        "id": id_risultato(r),
        "titolo": r.get("title", ""),
        "dimensione": int(r.get("size") or 0),
        "seed": r.get("seeders"),
        "peer": r.get("leechers"),
        "indexer": r.get("indexer", ""),
        "protocollo": r.get("protocol", ""),
        "pagina": r.get("infoUrl") or "",
        "data": (r.get("publishDate") or "")[:10],
    }


def ordina(risultati: list[dict]) -> list[dict]:
    """Prima i torrent con più seed (scaricano prima e meglio)."""
    return sorted(risultati, key=lambda r: (r["protocollo"] == "torrent", r["seed"] or 0), reverse=True)


class Prowlarr:
    def __init__(self, config: Config, archivio: Archivio, client: httpx.AsyncClient) -> None:
        self.config = config
        self.archivio = archivio
        self.client = client

    def _intestazioni(self) -> dict:
        return {"X-Api-Key": self.config.prowlarr_chiave}

    async def _get(self, percorso: str, parametri: dict | None = None, timeout: float = 20):
        try:
            r = await self.client.get(
                f"{self.config.prowlarr_url}/api/v1/{percorso}",
                params=parametri,
                headers=self._intestazioni(),
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise non_raggiungibile("Prowlarr", "/prowlarr") from exc
        if r.status_code == 401:
            raise ErroreServizio(
                "Prowlarr rifiuta la chiave API.",
                "Esegui \"mediabox restart\" e poi \"mediabox configura\".",
            )
        if not r.is_success:
            raise ErroreServizio(f"Prowlarr ha risposto con un errore ({r.status_code}).", "Riprova tra poco.")
        return r.json()

    async def cerca(self, film: dict) -> dict:
        indexer = await self._get("indexer")
        attivi = [i for i in indexer if i.get("enable")]
        if not attivi:
            return {
                "risultati": [],
                "avviso": "Nessun indexer configurato in Prowlarr.",
                "cosa_fare": "Apri Prowlarr dalla pagina iniziale (tessera Prowlarr) e aggiungi i tuoi indexer: "
                "Indexer → Aggiungi indexer.",
            }
        titolo = film.get("titolo_originale") or film.get("titolo")
        testo = f"{titolo} {film.get('anno', '')}".strip()
        chiave_cache = f"prowlarr:{testo}"
        grezzi = self.archivio.cache_leggi(chiave_cache)
        if grezzi is None:
            grezzi = await self._get(
                "search",
                {"query": testo, "type": "search", "categories": [2000], "limit": 100},
                timeout=90,
            )
            self.archivio.cache_scrivi(chiave_cache, grezzi, DIECI_MINUTI)
        # Teniamo i risultati completi per poter avviare il download dopo
        self.archivio.cache_scrivi(f"prowlarr:film:{film['id']}", grezzi, 6 * 3600)
        risultati = ordina([riassunto(r) for r in grezzi if r.get("protocol") == "torrent"])
        return {"risultati": risultati[:60], "indexer": len(attivi)}

    def risultato_originale(self, tmdb_id: int, id_ris: str) -> dict:
        grezzi = self.archivio.cache_leggi(f"prowlarr:film:{tmdb_id}") or []
        for r in grezzi:
            if id_risultato(r) == id_ris:
                return r
        raise ErroreServizio(
            "Questo risultato non è più disponibile.",
            "Ricarica la pagina del film per rifare la ricerca e riprova.",
        )

    async def scarica_torrent(self, risultato: dict) -> tuple[str | None, bytes | None]:
        """Restituisce (magnet, None) oppure (None, contenuto del file .torrent)."""
        magnet = risultato.get("magnetUrl") or ""
        if magnet.startswith("magnet:"):
            return magnet, None
        url = risultato.get("downloadUrl") or magnet
        if not url:
            raise ErroreServizio("Questo risultato non ha un link per il download.", "Scegline un altro.")
        try:
            r = await self.client.get(url, follow_redirects=False, timeout=60)
            salti = 0
            while r.is_redirect and salti < 5:
                destinazione = r.headers.get("location", "")
                if destinazione.startswith("magnet:"):
                    return destinazione, None
                r = await self.client.get(destinazione, follow_redirects=False, timeout=60)
                salti += 1
        except httpx.HTTPError as exc:
            raise ErroreServizio(
                "Non riesco a scaricare il torrent da Prowlarr.",
                "L'indexer potrebbe non rispondere: riprova o scegli un altro risultato.",
            ) from exc
        if not r.is_success or not r.content:
            raise ErroreServizio(
                f"L'indexer non ha restituito il torrent (errore {r.status_code}).", "Scegli un altro risultato."
            )
        return None, r.content
