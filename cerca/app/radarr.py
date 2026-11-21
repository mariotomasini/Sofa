"""Radarr: aggiunta del film e importazione dei file scaricati.

Due strade:
- "Scarica" (un clic): Radarr cerca da solo la release migliore con il profilo
  di mediabox (audio italiano prima di tutto), la scarica e la importa;
- scelta manuale di una fonte: Cerca scarica il file e lo passa a Radarr con
  un'importazione manuale, così Radarr lo rinomina e Jellyfin lo trova.
"""

from __future__ import annotations

import asyncio
import os

import httpx

from .config import Config
from .rete import ErroreServizio, non_raggiungibile

PROFILO_PREFERITO = "Migliore disponibile"
ESTENSIONI_VIDEO = (".mkv", ".mp4", ".m4v", ".avi", ".mpg", ".mpeg", ".mov", ".ogv", ".webm", ".wmv", ".ts", ".m2ts")


class Radarr:
    def __init__(self, config: Config, client: httpx.AsyncClient) -> None:
        self.config = config
        self.client = client
        self.api = f"{config.radarr_url}/api/v3"

    async def _r(self, metodo: str, percorso: str, **kw):
        try:
            r = await self.client.request(
                metodo, f"{self.api}/{percorso}", headers={"X-Api-Key": self.config.radarr_chiave}, timeout=60, **kw
            )
        except httpx.HTTPError as exc:
            raise non_raggiungibile("Radarr", "/radarr") from exc
        if r.status_code == 401:
            raise ErroreServizio(
                "Radarr rifiuta la chiave API.", "Esegui \"mediabox restart\" e poi \"mediabox configura\"."
            )
        if not r.is_success:
            try:
                dettaglio = r.json().get("message", "") if isinstance(r.json(), dict) else ""
            except ValueError:
                dettaglio = r.text[:200]
            raise ErroreServizio(
                f"Radarr ha risposto con un errore ({r.status_code}): {dettaglio}",
                "Apri Radarr dalla pagina iniziale per vedere i dettagli, poi riprova.",
            )
        return r.json() if r.content else None

    async def film(self, tmdb_id: int) -> dict | None:
        elenco = await self._r("GET", "movie", params={"tmdbId": tmdb_id})
        return elenco[0] if elenco else None

    async def aggiungi_film(self, tmdb_id: int) -> dict:
        esistente = await self.film(tmdb_id)
        if esistente:
            return esistente
        dati = await self._r("GET", "movie/lookup/tmdb", params={"tmdbId": tmdb_id})
        profili = await self._r("GET", "qualityprofile")
        profilo = next((p for p in profili if p["name"] == PROFILO_PREFERITO), profili[0])
        dati.update(
            qualityProfileId=profilo["id"],
            rootFolderPath=self.config.cartella_film_radarr,
            monitored=True,
            minimumAvailability="released",
            addOptions={"searchForMovie": False},
        )
        return await self._r("POST", "movie", json=dati)

    async def scarica_da_solo(self, tmdb_id: int) -> dict:
        """Un clic e basta: Radarr segue il film, sceglie la release migliore (ITA prima)
        con il profilo di mediabox, la scarica con qBittorrent e la mette in Jellyfin.
        Se poi esce una versione italiana migliore, la sostituisce da solo."""
        film = await self.aggiungi_film(tmdb_id)
        if not film.get("monitored"):
            await self._r("PUT", "movie/editor", json={"movieIds": [film["id"]], "monitored": True})
        await self._r("POST", "command", json={"name": "MoviesSearch", "movieIds": [film["id"]]})
        return film

    async def annulla(self, id_film: int) -> str:
        """Ferma tutto: download in corso e ricerca. Se il file non c'era ancora, il film esce da Radarr."""
        film = await self._r("GET", f"movie/{id_film}")
        coda = await self._r("GET", "queue/details", params={"movieId": id_film}) or []
        for voce in coda:
            if voce.get("id"):
                await self._r("DELETE", f"queue/{voce['id']}", params={"removeFromClient": "true", "blocklist": "false"})
        if not film.get("hasFile"):
            await self._r("DELETE", f"movie/{id_film}", params={"deleteFiles": "true", "addImportExclusion": "false"})
            return f"Annullato: \"{film.get('title', '')}\" non viene più cercato."
        await self._r("PUT", "movie/editor", json={"movieIds": [id_film], "monitored": False})
        return f"Annullato: tengo la versione che hai già di \"{film.get('title', '')}\" e non cerco più di meglio."

    async def cerca_di_nuovo(self, id_film: int) -> None:
        await self._r("PUT", "movie/editor", json={"movieIds": [id_film], "monitored": True})
        await self._r("POST", "command", json={"name": "MoviesSearch", "movieIds": [id_film]})

    async def importa(self, percorso: str, id_film: int) -> str:
        """Importa (spostando) il video principale trovato in `percorso`."""
        cartella = percorso if os.path.isdir(percorso) else os.path.dirname(percorso)
        candidati = await self._r(
            "GET",
            "manualimport",
            params={"folder": cartella, "filterExistingFiles": "false"},
        )
        if os.path.isfile(percorso):
            candidati = [c for c in candidati if c.get("path") == percorso]
        else:
            candidati = [c for c in candidati if c.get("path", "").startswith(percorso.rstrip("/") + "/")]
        candidati = [c for c in candidati if c.get("path", "").lower().endswith(ESTENSIONI_VIDEO)]
        if not candidati:
            raise ErroreServizio(
                "Radarr non trova un file video nel download.",
                "Il torrent potrebbe contenere un archivio (.rar/.zip) o un formato strano: controllalo da qBittorrent.",
            )
        principale = max(candidati, key=lambda c: c.get("size", 0))
        file = {
            "path": principale["path"],
            "movieId": id_film,
            "quality": principale.get("quality"),
            "languages": principale.get("languages") or [],
            "releaseGroup": principale.get("releaseGroup") or "",
            "indexerFlags": principale.get("indexerFlags") or 0,
            "downloadId": principale.get("downloadId") or "",
        }
        comando = await self._r(
            "POST", "command", json={"name": "ManualImport", "importMode": "move", "files": [file]}
        )
        for _ in range(180):
            stato = await self._r("GET", f"command/{comando['id']}")
            if stato.get("status") in ("completed", "failed", "aborted", "cancelled"):
                break
            await asyncio.sleep(2)
        film = await self._r("GET", f"movie/{id_film}")
        if not film.get("hasFile"):
            raise ErroreServizio(
                "Radarr non è riuscito a importare il film.",
                "Apri Radarr → Attività → Coda o Sistema → Eventi per vedere il motivo.",
            )
        return film.get("path", "")

    async def analizza_titolo(self, titolo: str) -> dict:
        """Chiede a Radarr di capire titolo e anno dal nome di un file."""
        dati = await self._r("GET", "parse", params={"title": titolo})
        info = (dati or {}).get("parsedMovieInfo") or {}
        titoli = info.get("movieTitles") or ([info["movieTitle"]] if info.get("movieTitle") else [])
        return {"titolo": titoli[0] if titoli else "", "anno": str(info.get("year") or "")}
