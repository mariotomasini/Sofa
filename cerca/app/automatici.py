"""I download che fanno da soli Radarr e Sonarr, con la lingua in evidenza.

- coda(): cosa stanno scaricando adesso, con la lingua dell'audio;
- in_attesa(): film e puntate già scaricati ma non nella lingua preferita
  (verranno sostituiti appena esce la versione giusta);
- ricerca_notturna(): ogni notte fa ricercare a Radarr e Sonarr le versioni
  migliori e quello che non avevano trovato, per non affidarsi solo alle uscite nuove.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from .config import Config
from .radarr import Radarr
from .serie import Serie

log = logging.getLogger("cerca.automatici")

NOMI = {"it": "Italiano", "es": "Spagnolo", "fr": "Francese", "de": "Tedesco", "pt": "Portoghese", "en": "Inglese"}
DA_INGLESE = {"Italian": "Italiano", "Spanish": "Spagnolo", "French": "Francese", "German": "Tedesco",
              "Portuguese": "Portoghese", "English": "Inglese"}
PREFISSO = "Lingua · "
ORA_RICERCA = 4  # di notte, quando nessuno guarda
CACHE_ATTESA = 600


def lingua_da(formati: list[dict] | None, lingue: list[dict] | None = None,
              ordine: tuple[str, ...] = ("it", "es", "en")) -> str:
    """La lingua dell'audio: dai formati di mediabox ("Lingua · Spagnolo"), se no da quella letta da Radarr.
    Con più lingue (es. ITA.SPA) conta quella più in alto nelle preferenze."""
    trovate = [f.get("name", "").removeprefix(PREFISSO) for f in formati or []
               if f.get("name", "").startswith(PREFISSO) and f.get("name") != PREFISSO + "MULTI"]
    if not trovate:
        trovate = [DA_INGLESE[x["name"]] for x in lingue or [] if x.get("name") in DA_INGLESE]
    if not trovate:
        return ""
    posto = {NOMI[c]: i for i, c in enumerate(ordine) if c in NOMI}
    return min(trovate, key=lambda n: posto.get(n, len(posto)))


def percentuale(totale: float, mancante: float) -> float:
    return round(100 * (1 - mancante / totale), 1) if totale else 0.0


def episodio(e: dict | None) -> str:
    e = e or {}
    return f"St. {e.get('seasonNumber', 0)} · Ep. {e.get('episodeNumber', 0)}"


class Automatici:
    def __init__(self, config: Config, radarr: Radarr, serie: Serie) -> None:
        self.config = config
        self.radarr = radarr
        self.serie = serie
        self._attesa: tuple[float, list[dict]] | None = None

    @property
    def preferita(self) -> str:
        return NOMI.get(self.config.lingue_audio[0], "Italiano")

    def _lingua(self, dati: dict) -> str:
        return lingua_da(dati.get("customFormats"), dati.get("languages"), self.config.lingue_audio)

    def _voce(self, titolo: str, dettaglio: str, lingua: str, **altro) -> dict:
        return {
            "titolo": titolo, "dettaglio": dettaglio, "lingua": lingua or "lingua sconosciuta",
            "preferita": lingua == self.preferita, "lingua_preferita": self.preferita, **altro,
        }

    async def coda(self) -> list[dict]:
        voci: list[dict] = []
        try:
            dati = await self.radarr._r("GET", "queue", params={"pageSize": 50, "includeMovie": "true"})
            for r in (dati or {}).get("records", []):
                film = r.get("movie") or {}
                titolo = f"{film.get('title', r.get('title', ''))} ({film.get('year', '')})".replace(" ()", "")
                voci.append(self._voce(titolo, r.get("title", ""), self._lingua(r),
                                       tipo="film", percentuale=percentuale(r.get("size", 0), r.get("sizeleft", 0)),
                                       stato=r.get("trackedDownloadState") or r.get("status", "")))
        except Exception:  # noqa: BLE001 - se Radarr non risponde la pagina mostra il resto
            log.warning("Coda di Radarr non disponibile", exc_info=True)
        if self.serie.attivo:
            try:
                dati = await self.serie._api("GET", "queue", params={
                    "pageSize": 50, "includeSeries": "true", "includeEpisode": "true"})
                for r in (dati or {}).get("records", []):
                    titolo = f"{(r.get('series') or {}).get('title', '')} · {episodio(r.get('episode'))}"
                    voci.append(self._voce(titolo, r.get("title", ""),
                                           self._lingua(r),
                                           tipo="serie", percentuale=percentuale(r.get("size", 0), r.get("sizeleft", 0)),
                                           stato=r.get("trackedDownloadState") or r.get("status", "")))
            except Exception:  # noqa: BLE001
                log.warning("Coda di Sonarr non disponibile", exc_info=True)
        return voci

    async def in_attesa(self) -> list[dict]:
        """Già scaricati ma non nella lingua preferita (Radarr/Sonarr li sostituiscono da soli)."""
        if self._attesa is not None and time.monotonic() - self._attesa[0] < CACHE_ATTESA:
            return self._attesa[1]
        voci: list[dict] = []
        try:
            dati = await self.radarr._r("GET", "wanted/cutoff", params={"pageSize": 100})
            for m in (dati or {}).get("records", []):
                file = m.get("movieFile") or {}
                lingua = self._lingua(file)
                titolo = f"{m.get('title', '')} ({m.get('year', '')})".replace(" ()", "")
                voci.append(self._voce(titolo, file.get("relativePath", ""), lingua, tipo="film"))
        except Exception:  # noqa: BLE001
            log.warning("Elenco da migliorare di Radarr non disponibile", exc_info=True)
        if self.serie.attivo:
            try:
                dati = await self.serie._api("GET", "wanted/cutoff", params={
                    "pageSize": 100, "includeSeries": "true", "includeEpisodeFile": "true"})
                for e in (dati or {}).get("records", []):
                    file = e.get("episodeFile") or {}
                    titolo = f"{(e.get('series') or {}).get('title', '')} · {episodio(e)}"
                    voci.append(self._voce(titolo, file.get("relativePath", ""),
                                           self._lingua(file), tipo="serie"))
            except Exception:  # noqa: BLE001
                log.warning("Elenco da migliorare di Sonarr non disponibile", exc_info=True)
        voci = [v for v in voci if not v["preferita"]]
        self._attesa = (time.monotonic(), voci)
        return voci

    async def cerca_tutto(self) -> None:
        await self.radarr._r("POST", "command", json={"name": "CutoffUnmetMoviesSearch"})
        await self.radarr._r("POST", "command", json={"name": "MissingMoviesSearch"})
        if self.serie.attivo:
            await self.serie._api("POST", "command", json={"name": "CutoffUnmetEpisodeSearch"})
            await self.serie._api("POST", "command", json={"name": "MissingEpisodeSearch"})
        self._attesa = None

    async def ricerca_notturna(self) -> None:
        while True:
            adesso = dt.datetime.now()
            prossima = adesso.replace(hour=ORA_RICERCA, minute=0, second=0, microsecond=0)
            if prossima <= adesso:
                prossima += dt.timedelta(days=1)
            await asyncio.sleep((prossima - adesso).total_seconds())
            try:
                await self.cerca_tutto()
                log.info("Ricerca notturna avviata: versioni migliori e quelli mancanti")
            except Exception:  # noqa: BLE001 - riproviamo la notte dopo
                log.exception("Ricerca notturna non riuscita")
