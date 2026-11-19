"""Jellyfin: avviso di un film nuovo, così compare subito nella libreria.

Radarr vede i film in /data/media/film, Jellyfin in /data/film: il percorso
va tradotto prima di avvisare Jellyfin.
"""

from __future__ import annotations

import logging
import time

import httpx

from .config import Config
from .rete import non_raggiungibile

log = logging.getLogger("cerca.jellyfin")

INTESTAZIONE = 'MediaBrowser Client="mediabox-cerca", Device="cerca", DeviceId="mediabox-cerca", Version="1.0"'


def percorso_per_jellyfin(percorso_radarr: str) -> str:
    if percorso_radarr.startswith("/data/media/"):
        return "/data/" + percorso_radarr[len("/data/media/"):]
    return percorso_radarr


class Jellyfin:
    def __init__(self, config: Config, client: httpx.AsyncClient) -> None:
        self.config = config
        self.client = client
        self._token = ""
        self._utente = ""
        self._in_libreria: dict[str, tuple[float, set[int]]] = {}

    async def _accedi(self) -> None:
        r = await self.client.post(
            f"{self.config.jellyfin_url}/Users/AuthenticateByName",
            json={"Username": self.config.utente, "Pw": self.config.password},
            headers={"Authorization": INTESTAZIONE},
            timeout=20,
        )
        r.raise_for_status()
        dati = r.json()
        self._token = dati["AccessToken"]
        self._utente = dati["User"]["Id"]

    async def avvisa_nuovo(self, percorso_radarr: str) -> None:
        """Non solleva mai errori: nel peggiore dei casi Jellyfin lo troverà
        alla prossima scansione automatica."""
        try:
            if not self._token:
                await self._accedi()
            corpo = {"Updates": [{"Path": percorso_per_jellyfin(percorso_radarr), "UpdateType": "Created"}]}
            for _ in range(2):
                r = await self.client.post(
                    f"{self.config.jellyfin_url}/Library/Media/Updated",
                    json=corpo,
                    headers={"Authorization": f'{INTESTAZIONE}, Token="{self._token}"'},
                    timeout=20,
                )
                if r.status_code != 401:
                    break
                await self._accedi()
            # Jellyfin ignora gli avvisi per le cartelle che all'avvio erano vuote:
            # una scansione completa è sicura e, in una libreria di casa, veloce.
            await self.client.post(
                f"{self.config.jellyfin_url}/Library/Refresh",
                headers={"Authorization": f'{INTESTAZIONE}, Token="{self._token}"'},
                timeout=20,
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("Non sono riuscito ad avvisare Jellyfin: %s", exc)

    # --- musica --------------------------------------------------------------------
    async def _get(self, percorso: str, parametri: dict) -> dict:
        if not self._token:
            await self._accedi()
        for _ in range(2):
            r = await self.client.get(
                f"{self.config.jellyfin_url}{percorso}",
                params=parametri,
                headers={"Authorization": f'{INTESTAZIONE}, Token="{self._token}"'},
                timeout=30,
            )
            if r.status_code != 401:
                break
            await self._accedi()
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _artista(voce: dict) -> dict:
        return {"nome": voce.get("Name", ""), "mbid": (voce.get("ProviderIds") or {}).get("MusicBrainzArtist", "")}

    async def artisti(self, limite: int = 2000) -> list[dict]:
        """Tutti gli artisti presenti nella libreria Musica."""
        try:
            if not self._utente:
                await self._accedi()
            dati = await self._get("/Artists", {"UserId": self._utente, "Limit": limite, "Fields": "ProviderIds"})
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise non_raggiungibile("Jellyfin", "/jellyfin") from exc
        return [self._artista(v) for v in dati.get("Items", []) if v.get("Name")]

    async def artisti_piu_ascoltati(self, limite: int = 8) -> list[dict]:
        """Gli artisti che ascolti di più; se non hai ancora ascoltato nulla, alcuni della libreria."""
        try:
            if not self._utente:
                await self._accedi()
            brani = await self._get(
                f"/Users/{self._utente}/Items",
                {"IncludeItemTypes": "Audio", "Recursive": "true", "Filters": "IsPlayed",
                 "SortBy": "PlayCount", "SortOrder": "Descending", "Limit": 300, "Fields": "ProviderIds"},
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise non_raggiungibile("Jellyfin", "/jellyfin") from exc
        conteggi: dict[str, int] = {}
        for brano in brani.get("Items", []):
            for artista in brano.get("ArtistItems") or []:
                nome = artista.get("Name")
                if nome:
                    conteggi[nome] = conteggi.get(nome, 0) + int((brano.get("UserData") or {}).get("PlayCount") or 1)
        tutti = {a["nome"]: a for a in await self.artisti()}
        if conteggi:
            preferiti = sorted(conteggi, key=conteggi.get, reverse=True)[:limite]
            return [tutti.get(n, {"nome": n, "mbid": ""}) for n in preferiti]
        return list(tutti.values())[:limite]

    # --- home in stile Netflix ----------------------------------------------------------
    async def _id_utente(self) -> str:
        if not self._utente:
            await self._accedi()
        return self._utente

    async def generi_preferiti(self, tipo: str) -> list[str]:
        """I generi di quello che hai visto o messo tra i preferiti, dal più frequente.

        tipo: "movie" o "tv". Le serie contano per gli episodi visti, così una serie
        seguita a lungo pesa più di un film visto una volta.
        """
        utente = await self._id_utente()
        conteggi: dict[str, int] = {}
        if tipo == "tv":
            episodi = await self._get(
                f"/Users/{utente}/Items",
                {"IncludeItemTypes": "Episode", "Recursive": "true", "Filters": "IsPlayed", "Limit": 500,
                 "SortBy": "DatePlayed", "SortOrder": "Descending"},
            )
            per_serie: dict[str, int] = {}
            for e in episodi.get("Items", []):
                if e.get("SeriesId"):
                    per_serie[e["SeriesId"]] = per_serie.get(e["SeriesId"], 0) + 1
            preferite = await self._get(
                f"/Users/{utente}/Items",
                {"IncludeItemTypes": "Series", "Recursive": "true", "Filters": "IsFavorite", "Limit": 100},
            )
            for v in preferite.get("Items", []):
                per_serie[v["Id"]] = per_serie.get(v["Id"], 0) + 5
            if per_serie:
                serie = await self._get(
                    f"/Users/{utente}/Items",
                    {"Ids": ",".join(list(per_serie)[:200]), "Fields": "Genres"},
                )
                for v in serie.get("Items", []):
                    for g in v.get("Genres") or []:
                        conteggi[g] = conteggi.get(g, 0) + min(per_serie.get(v["Id"], 1), 10)
        else:
            for filtro, peso in (("IsPlayed", 1), ("IsFavorite", 3)):
                film = await self._get(
                    f"/Users/{utente}/Items",
                    {"IncludeItemTypes": "Movie", "Recursive": "true", "Filters": filtro, "Fields": "Genres",
                     "Limit": 300},
                )
                for v in film.get("Items", []):
                    for g in v.get("Genres") or []:
                        conteggi[g] = conteggi.get(g, 0) + peso
        return sorted(conteggi, key=lambda g: (-conteggi[g], g))

    async def tmdb_in_libreria(self, tipo: str) -> set[int]:
        """Gli id TMDB dei film (o delle serie) che hai già: non ha senso consigliarteli."""
        adesso = time.monotonic()
        if tipo in self._in_libreria and adesso - self._in_libreria[tipo][0] < 600:
            return self._in_libreria[tipo][1]
        utente = await self._id_utente()
        dati = await self._get(
            f"/Users/{utente}/Items",
            {"IncludeItemTypes": "Series" if tipo == "tv" else "Movie", "Recursive": "true",
             "Fields": "ProviderIds", "Limit": 20000},
        )
        ids = set()
        for v in dati.get("Items", []):
            valore = str((v.get("ProviderIds") or {}).get("Tmdb") or "")
            if valore.isdigit():
                ids.add(int(valore))
        self._in_libreria[tipo] = (adesso, ids)
        return ids

    @staticmethod
    def _da_continuare(v: dict) -> dict:
        episodio = v.get("Type") == "Episode"
        immagine_id = v.get("SeriesId") if episodio and v.get("SeriesId") else v.get("Id")
        dati = v.get("UserData") or {}
        sottotitolo = ""
        if episodio:
            numero = ""
            if v.get("ParentIndexNumber") is not None and v.get("IndexNumber") is not None:
                numero = f"S{v['ParentIndexNumber']} E{v['IndexNumber']} · "
            sottotitolo = numero + (v.get("Name") or "")
        return {
            "id": v.get("Id", ""),
            "titolo": (v.get("SeriesName") if episodio else v.get("Name")) or "",
            "sottotitolo": sottotitolo,
            "anno": str(v.get("ProductionYear") or ""),
            "poster": f"/jellyfin/Items/{immagine_id}/Images/Primary?fillHeight=450&quality=85",
            "percentuale": round(dati.get("PlayedPercentage") or 0),
            "link": f"/jellyfin/web/#/details?id={v.get('Id', '')}",
            "jellyfin": True,
        }

    async def continua(self, tipo: str, limite: int = 12) -> list[dict]:
        """Quello che hai lasciato a metà; per le serie anche il prossimo episodio da vedere."""
        utente = await self._id_utente()
        dati = await self._get(
            f"/Users/{utente}/Items/Resume",
            {"IncludeItemTypes": "Episode" if tipo == "tv" else "Movie", "Limit": limite, "MediaTypes": "Video"},
        )
        elementi = [self._da_continuare(v) for v in dati.get("Items", [])]
        if tipo == "tv" and len(elementi) < limite:
            prossimi = await self._get("/Shows/NextUp", {"UserId": utente, "Limit": limite})
            gia = {e["titolo"] for e in elementi}
            for v in prossimi.get("Items", []):
                e = self._da_continuare(v)
                if e["titolo"] not in gia:
                    gia.add(e["titolo"])
                    elementi.append(e)
        return elementi[:limite]

    # --- pulizia ---------------------------------------------------------------------
    async def utenti(self) -> list[str]:
        return [u["Id"] for u in await self._get("/Users", {})]

    async def elementi_utente(self, utente: str) -> list[dict]:
        """Film ed episodi con i dati di visione di un utente."""
        dati = await self._get(
            f"/Users/{utente}/Items",
            {"IncludeItemTypes": "Movie,Episode", "Recursive": "true", "Fields": "Path,ProviderIds",
             "EnableUserData": "true", "Limit": 20000},
        )
        return dati.get("Items", [])

    async def aggiorna_libreria(self) -> None:
        try:
            if not self._token:
                await self._accedi()
            await self.client.post(
                f"{self.config.jellyfin_url}/Library/Refresh",
                headers={"Authorization": f'{INTESTAZIONE}, Token="{self._token}"'},
                timeout=20,
            )
        except httpx.HTTPError as exc:
            log.warning("Non sono riuscito ad aggiornare Jellyfin: %s", exc)
