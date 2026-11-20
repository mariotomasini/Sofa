"""Serie TV con Sonarr: "Segui la serie" da Cerca e i nuovi episodi delle serie seguite."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os

import httpx

from .config import Config
from .radarr import ESTENSIONI_VIDEO
from .rete import ErroreServizio, non_raggiungibile

log = logging.getLogger("cerca.serie")

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
            return {"messaggio": f"Segui già \"{nome}\": le puntate nuove arrivano da sole in Jellyfin.",
                    "nuova": False, "serie_id": serie["id"]}
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
        aggiunta = await self._api("POST", "series", json=serie)
        return {"messaggio": f"Fatto! Sonarr cerca le puntate di \"{nome}\": quando sono scaricate le trovi in Jellyfin.",
                "nuova": True, "serie_id": (aggiunta or {}).get("id")}

    async def titoli(self, serie_id: int) -> list[str]:
        """Il titolo della serie in Sonarr e quelli alternativi (per cercare i torrent)."""
        serie = await self._api("GET", f"series/{serie_id}")
        altri = [a.get("title", "") for a in serie.get("alternateTitles") or []]
        return [serie.get("title", ""), *altri]

    async def importa(self, percorso: str, serie_id: int) -> tuple[int, str]:
        """Importa in Sonarr le puntate di un download, dicendo noi di che serie sono.

        Sonarr da solo riconosce le serie dal titolo inglese: un torrent "Mare Fuori S04E03"
        non lo collegherebbe a "The Sea Beyond". Gli facciamo leggere dal nome del file solo
        stagione ed episodio (quello lo sa fare sempre) e li colleghiamo noi alla serie giusta.
        Restituisce quante puntate ha importato e la cartella della serie.
        """
        # Sonarr crea la cartella della serie solo al primo file: se manca, l'importazione
        # può fallire ("Could not find a part of the path"). Cerca vede /data come Sonarr.
        dati_serie = await self._api("GET", f"series/{serie_id}")
        if dati_serie and dati_serie.get("path"):
            try:
                await asyncio.to_thread(os.makedirs, dati_serie["path"], exist_ok=True)
            except OSError:
                log.warning("Non riesco a creare la cartella %s", dati_serie["path"])
        cartella = percorso if os.path.isdir(percorso) else os.path.dirname(percorso)
        candidati = await self._api("GET", "manualimport", params={"folder": cartella, "filterExistingFiles": "false"})
        dentro = [c for c in candidati or [] if (c.get("path") == percorso
                  or c.get("path", "").startswith(percorso.rstrip("/") + "/"))
                  and c.get("path", "").lower().endswith(ESTENSIONI_VIDEO)
                  and not any("sample" in (r.get("reason") or "").lower() for r in c.get("rejections") or [])]
        puntate = {(e["seasonNumber"], e["episodeNumber"]): e["id"]
                   for e in await self._api("GET", "episode", params={"seriesId": serie_id}) or []}
        file = []
        for c in dentro:
            if (c.get("series") or {}).get("id") == serie_id and c.get("episodes"):
                ids = [e["id"] for e in c["episodes"]]
            else:
                nome = os.path.splitext(os.path.basename(c["path"]))[0]
                info = (await self._api("GET", "parse", params={"title": nome}) or {}).get("parsedEpisodeInfo") or {}
                ids = [puntate[(info.get("seasonNumber"), n)] for n in info.get("episodeNumbers") or []
                       if (info.get("seasonNumber"), n) in puntate]
            if ids:
                file.append({
                    "path": c["path"], "seriesId": serie_id, "episodeIds": ids,
                    "quality": c.get("quality"), "languages": c.get("languages") or [],
                    "releaseGroup": c.get("releaseGroup") or "", "downloadId": c.get("downloadId") or "",
                    "indexerFlags": c.get("indexerFlags") or 0,
                })
        if not file:
            raise ErroreServizio("Sonarr non riconosce nessuna puntata nel download.",
                                 "Apri Sonarr → Attività → Coda per importarla a mano, oppure scegli un altro torrent.")
        comando = await self._api("POST", "command", json={"name": "ManualImport", "importMode": "move", "files": file})
        stato = {}
        for _ in range(300):
            stato = await self._api("GET", f"command/{comando['id']}")
            if stato.get("status") in ("completed", "failed", "aborted", "cancelled"):
                break
            await asyncio.sleep(2)
        if stato.get("status") != "completed":
            raise ErroreServizio("Sonarr non è riuscito a importare le puntate.",
                                 "Apri Sonarr → Sistema → Eventi per vedere il motivo.")
        return len(file), (dati_serie or {}).get("path", "")

    async def ultima_importazione(self) -> str:
        """La data dell'ultima puntata importata da Sonarr ("" se nessuna)."""
        dati = await self._api("GET", "history", params={
            "page": 1, "pageSize": 1, "sortKey": "date", "sortDirection": "descending", "eventType": 3})
        record = (dati or {}).get("records") or []
        return record[0].get("date", "") if record else ""

    async def sorveglia_importazioni(self, jellyfin, ogni: float = 120) -> None:
        """Le puntate che Sonarr scarica da solo: quando ne arriva una, Jellyfin aggiorna la libreria.

        Sonarr avvisa Jellyfin, ma per una serie nuova (cartella che prima non c'era) Jellyfin
        ignora l'avviso e la puntata comparirebbe solo alla scansione di ogni 12 ore.
        Una chiamata leggera ogni 2 minuti; la scansione solo quando c'è davvero qualcosa di nuovo.
        """
        visto = None
        while True:
            if self.attivo:
                try:
                    ultima = await self.ultima_importazione()
                    if visto is not None and ultima and ultima != visto:
                        log.info("Sonarr ha importato nuove puntate: aggiorno Jellyfin")
                        await jellyfin.aggiorna_libreria()
                    visto = ultima
                except ErroreServizio:
                    pass
                except Exception:  # noqa: BLE001 - la sorveglianza non deve mai fermare Cerca
                    log.exception("Errore nel controllo delle puntate importate")
            await asyncio.sleep(ogni)

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
