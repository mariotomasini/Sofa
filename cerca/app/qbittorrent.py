"""qBittorrent: aggiunta torrent e controllo dell'avanzamento.

Le richieste dalla rete interna di mediabox non chiedono login (lo ha
impostato il configuratore); se servisse, Cerca accede con la password.
"""

from __future__ import annotations

import httpx

from .config import Config
from .rete import ErroreServizio, hash_da_magnet, non_raggiungibile

STATI_FINITI = {"uploading", "stalledUP", "pausedUP", "stoppedUP", "queuedUP", "forcedUP", "checkingUP"}
STATI_ERRORE = {"error", "missingFiles"}
# finito ma qBittorrent sta ancora spostando i file: il percorso cambierà, non si importa ancora
STATI_IN_MOVIMENTO = {"moving"}


class QBittorrent:
    def __init__(self, config: Config, client: httpx.AsyncClient) -> None:
        self.config = config
        self.client = client
        self.base = f"{config.qbit_url}/api/v2"

    async def _accedi(self) -> None:
        r = await self.client.post(
            f"{self.base}/auth/login",
            data={"username": self.config.utente, "password": self.config.password},
            headers={"Referer": self.config.qbit_url},
        )
        if r.text.strip() != "Ok.":
            raise ErroreServizio(
                "qBittorrent non accetta la password di amministrazione.",
                "Esegui \"mediabox configura\" per reimpostarla.",
            )

    async def _richiesta(self, metodo: str, percorso: str, **kw) -> httpx.Response:
        try:
            r = await self.client.request(metodo, f"{self.base}/{percorso}", **kw)
            if r.status_code in (401, 403):
                await self._accedi()
                r = await self.client.request(metodo, f"{self.base}/{percorso}", **kw)
        except httpx.HTTPError as exc:
            raise non_raggiungibile("qBittorrent", "/qbit") from exc
        return r

    async def aggiungi(
        self,
        *,
        categoria: str,
        etichetta: str,
        magnet_o_url: str | None = None,
        file_torrent: bytes | None = None,
        cartella: str | None = None,
    ) -> None:
        dati = {"category": categoria, "tags": etichetta, "autoTMM": "false" if cartella else "true"}
        if cartella:
            dati["savepath"] = cartella
        file = None
        if file_torrent:
            file = {"torrents": ("file.torrent", file_torrent, "application/x-bittorrent")}
        elif magnet_o_url:
            dati["urls"] = magnet_o_url
        else:
            raise ErroreServizio("Non hai indicato nessun torrent.", "Incolla un magnet o scegli un file .torrent.")
        r = await self._richiesta("POST", "torrents/add", data=dati, files=file)
        rifiutato = r.status_code in (409, 415) or r.text.strip() == "Fails."
        if rifiutato and magnet_o_url and not file_torrent and await self._gia_presente(magnet_o_url, etichetta):
            return
        if rifiutato:
            raise ErroreServizio(
                "qBittorrent non riesce ad aprire questo torrent.",
                "Controlla che il link o il file siano corretti (un magnet inizia con \"magnet:?\").",
            )
        if not r.is_success:
            raise ErroreServizio(f"qBittorrent ha rifiutato il torrent (errore {r.status_code}).", "Riprova.")

    async def _gia_presente(self, magnet: str, etichetta: str) -> bool:
        """qBittorrent rifiuta un torrent che ha già (aggiunto da Radarr, o da un download annullato a
        metà): invece di un errore si usa quello, con l'etichetta di Cerca per seguirlo."""
        hash_torrent = hash_da_magnet(magnet)
        if not hash_torrent:
            return False
        r = await self._richiesta("GET", "torrents/info", params={"hashes": hash_torrent})
        if not r.is_success or not r.json():
            return False
        await self.aggiungi_etichetta(hash_torrent, etichetta)
        return True

    async def per_etichetta(self, etichetta: str) -> dict | None:
        r = await self._richiesta("GET", "torrents/info", params={"tag": etichetta})
        elenco = r.json() if r.is_success else []
        return elenco[0] if elenco else None

    async def tutti(self) -> list[dict]:
        r = await self._richiesta("GET", "torrents/info")
        if not r.is_success:
            raise ErroreServizio("qBittorrent non risponde.", "Controlla con \"mediabox status\".")
        return r.json()

    async def per_categoria(self, categoria: str) -> list[dict]:
        r = await self._richiesta("GET", "torrents/info", params={"category": categoria})
        return r.json() if r.is_success else []

    async def file(self, hash_torrent: str) -> list[dict]:
        r = await self._richiesta("GET", "torrents/files", params={"hash": hash_torrent})
        return r.json() if r.is_success else []

    async def priorita_file(self, hash_torrent: str, indici: list[int], priorita: int) -> None:
        if not indici:
            return
        await self._richiesta(
            "POST",
            "torrents/filePrio",
            data={"hash": hash_torrent, "id": "|".join(map(str, indici)), "priority": str(priorita)},
        )

    async def in_fondo(self, hash_torrent: str) -> None:
        """Sposta il torrent in fondo alla coda dei download."""
        await self._richiesta("POST", "torrents/bottomPrio", data={"hashes": hash_torrent})

    async def aggiungi_etichetta(self, hash_torrent: str, etichetta: str) -> None:
        await self._richiesta("POST", "torrents/addTags", data={"hashes": hash_torrent, "tags": etichetta})

    async def elimina(self, hash_torrent: str, con_file: bool) -> None:
        await self._richiesta(
            "POST",
            "torrents/delete",
            data={"hashes": hash_torrent, "deleteFiles": "true" if con_file else "false"},
        )
