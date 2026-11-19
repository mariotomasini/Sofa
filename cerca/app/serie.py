"""Nuovi episodi delle serie seguite in Sonarr (per il mondo Serie TV)."""

from __future__ import annotations

import datetime as dt

import httpx

from .config import Config


def interpreta_calendario(episodi: list[dict], quanti: int = 18) -> list[dict]:
    """Dal calendario di Sonarr: gli episodi usciti, il più recente per ogni serie."""
    visti: set[int] = set()
    risultato = []
    for e in sorted(episodi, key=lambda x: x.get("airDateUtc") or "", reverse=True):
        serie = e.get("series") or {}
        id_serie = e.get("seriesId") or serie.get("id")
        if id_serie in visti:
            continue
        visti.add(id_serie)
        poster = next((i.get("remoteUrl") for i in serie.get("images", []) if i.get("coverType") == "poster"), "")
        risultato.append({
            "titolo": serie.get("title") or e.get("title") or "",
            "sottotitolo": f"St. {e.get('seasonNumber', 0)} · Ep. {e.get('episodeNumber', 0)}",
            "poster": poster or "",
            "pronto": bool(e.get("hasFile")),
            "anno": str(serie.get("year") or ""),
        })
    return risultato[:quanti]


class Serie:
    def __init__(self, config: Config, client: httpx.AsyncClient) -> None:
        self.config = config
        self.client = client

    @property
    def attivo(self) -> bool:
        return bool(self.config.sonarr_url)

    async def nuovi_episodi(self) -> list[dict]:
        if not self.attivo:
            return []
        oggi = dt.datetime.now(dt.UTC)
        try:
            r = await self.client.get(
                f"{self.config.sonarr_url}/api/v3/calendar",
                params={
                    "start": (oggi - dt.timedelta(days=14)).isoformat(),
                    "end": (oggi + dt.timedelta(days=1)).isoformat(),
                    "includeSeries": "true",
                },
                headers={"X-Api-Key": self.config.sonarr_chiave},
                timeout=30,
            )
            r.raise_for_status()
        except httpx.HTTPError:
            return []
        return interpreta_calendario(r.json())
