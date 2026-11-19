"""Jellyfin: avviso di un film nuovo, così compare subito nella libreria.

Radarr vede i film in /data/media/film, Jellyfin in /data/film: il percorso
va tradotto prima di avvisare Jellyfin.
"""

from __future__ import annotations

import logging

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
