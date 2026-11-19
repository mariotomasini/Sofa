"""Jellyfin: avviso di un film nuovo, così compare subito nella libreria.

Radarr vede i film in /data/media/film, Jellyfin in /data/film: il percorso
va tradotto prima di avvisare Jellyfin.
"""

from __future__ import annotations

import logging

import httpx

from .config import Config

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

    async def _accedi(self) -> None:
        r = await self.client.post(
            f"{self.config.jellyfin_url}/Users/AuthenticateByName",
            json={"Username": self.config.utente, "Pw": self.config.password},
            headers={"Authorization": INTESTAZIONE},
            timeout=20,
        )
        r.raise_for_status()
        self._token = r.json()["AccessToken"]

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
