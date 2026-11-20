"""Serie TV con Sonarr: "Segui la serie" da Cerca e i nuovi episodi delle serie seguite."""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx

from .config import Config
from .rete import ErroreServizio, non_raggiungibile

PROFILI_PREFERITI = ("HD - 720p/1080p", "HD-1080p", "HD-720p")


def scegli_profilo(profili: list[dict]) -> int:
    """La qualità: 720p/1080p se c'è (buona e leggera), altrimenti la prima."""
    for nome in PROFILI_PREFERITI:
        for p in profili:
            if p.get("name") == nome:
                return p["id"]
    if not profili:
        raise ErroreServizio("Sonarr non ha nessun profilo di qualità.", "Sul PC scrivi \"mediabox configura\".")
    return profili[0]["id"]


def scegli_cartella(cartelle: list[dict], anime: bool) -> str:
    percorsi = [c["path"] for c in cartelle if c.get("path")]
    if not percorsi:
        raise ErroreServizio("Sonarr non ha la cartella delle serie.", "Sul PC scrivi \"mediabox configura\".")
    if anime:
        for p in percorsi:
            if p.rstrip("/").endswith("/anime"):
                return p
    return next((p for p in percorsi if not p.rstrip("/").endswith("/anime")), percorsi[0])


def e_anime(serie: dict) -> bool:
    generi = {g.casefold() for g in serie.get("genres") or []}
    return "anime" in generi or (serie.get("originalLanguage") or {}).get("name") == "Japanese" and "animation" in generi


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

    def _intestazioni(self) -> dict:
        return {"X-Api-Key": self.config.sonarr_chiave}

    async def _api(self, metodo: str, percorso: str, **kw):
        try:
            r = await self.client.request(metodo, f"{self.config.sonarr_url}/api/v3/{percorso}",
                                          headers=self._intestazioni(), timeout=60, **kw)
        except httpx.HTTPError as exc:
            raise non_raggiungibile("Sonarr", "/sonarr") from exc
        if r.status_code == 401:
            raise ErroreServizio("Sonarr rifiuta la chiave di mediabox.", "Sul PC scrivi \"mediabox configura\".")
        if r.status_code >= 500:
            raise ErroreServizio("Sonarr non riesce a cercare la serie adesso.",
                                 "Di solito dipende dal suo servizio delle schede: riprova tra qualche minuto.")
        if not r.is_success:
            dettaglio = ""
            try:
                dettaglio = "; ".join(e.get("errorMessage", "") for e in r.json() if isinstance(e, dict))
            except ValueError:
                pass
            raise ErroreServizio(f"Sonarr ha risposto con un errore ({r.status_code}). {dettaglio}".strip(),
                                 "Riprova, oppure aggiungila da Sonarr.")
        return r.json() if r.content else None

    async def trova(self, *, tvdb: int | None = None, tmdb: int | None = None, imdb: str = "",
                    titolo: str = "") -> dict | None:
        """La serie in Sonarr: prima con i codici (precisi), poi con il titolo."""
        termini = [t for t in (f"tvdb:{tvdb}" if tvdb else "", f"tmdb:{tmdb}" if tmdb else "",
                               f"imdb:{imdb}" if imdb else "", titolo.strip()) if t]
        for termine in termini:
            risultati = await self._api("GET", "series/lookup", params={"term": termine})
            if risultati:
                return risultati[0]
        return None

    async def segui(self, *, tvdb: int | None = None, tmdb: int | None = None, imdb: str = "",
                    titolo: str = "") -> dict:
        """Aggiunge la serie a Sonarr e fa cercare tutte le puntate: si scaricano da sole."""
        if not self.attivo:
            raise ErroreServizio("Le serie TV non sono attive.",
                                 "Accendile nelle impostazioni di mediabox (/impostazioni), voce \"Serie TV\".")
        serie = await self.trova(tvdb=tvdb, tmdb=tmdb, imdb=imdb, titolo=titolo)
        if serie is None:
            raise ErroreServizio(f"Sonarr non trova \"{titolo}\".", "Prova a cercarla con il titolo originale.")
        nome = serie.get("title") or titolo
        if serie.get("id"):  # è già tra le serie seguite
            return {"messaggio": f"Segui già \"{nome}\": le puntate nuove arrivano da sole in Jellyfin.", "nuova": False}
        profili, cartelle = await asyncio.gather(self._api("GET", "qualityprofile"), self._api("GET", "rootfolder"))
        anime = e_anime(serie)
        serie.update(
            qualityProfileId=scegli_profilo(profili),
            rootFolderPath=scegli_cartella(cartelle, anime),
            seriesType="anime" if anime else "standard",
            monitored=True,
            seasonFolder=True,
            addOptions={"monitor": "all", "searchForMissingEpisodes": True, "searchForCutoffUnmetEpisodes": False},
        )
        await self._api("POST", "series", json=serie)
        return {"messaggio": f"Fatto! Sonarr cerca le puntate di \"{nome}\": quando sono scaricate le trovi in Jellyfin.",
                "nuova": True}

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
