"""Navidrome con l'API Subsonic: cercare i brani, creare le playlist, segnare i preferiti.

Ogni persona di casa ha il suo utente in Navidrome (lo crea il configuratore): playlist e
preferiti vanno con il suo utente. L'amministratore usa utente e password di mediabox, gli
altri il nome in minuscolo come password (configuratore/servizi/navidrome.py).
"""

from __future__ import annotations

import hashlib
import secrets

import httpx

from .archive import normalizza
from .rete import ErroreServizio, non_raggiungibile

BASE = "/navidrome"  # ND_BASEURL in docker-compose
CLIENTE = "cerca"


def credenziali(config, utente: dict | None) -> tuple[str, str]:
    """Utente e password di Navidrome per chi usa Cerca."""
    if not utente or utente.get("admin"):
        return config.utente, config.password
    nome = (utente.get("nome") or "").strip()
    return nome, nome.lower()


def stesso_brano(song: dict, artista: str, titolo: str) -> bool:
    t, a = normalizza(titolo), normalizza(artista)
    titolo_nd, artista_nd = normalizza(song.get("title", "")), normalizza(song.get("artist", ""))
    if not t or not titolo_nd:
        return False
    titolo_ok = titolo_nd == t or titolo_nd.startswith(t + " ") or t.startswith(titolo_nd + " ")
    artista_ok = not a or a in artista_nd or artista_nd in a
    return titolo_ok and artista_ok


class Navidrome:
    def __init__(self, config, client: httpx.AsyncClient, utente: str, password: str) -> None:
        self.config = config
        self.client = client
        self.utente = utente
        self.password = password

    @property
    def attivo(self) -> bool:
        return bool(self.config.navidrome_url)

    async def _rest(self, metodo: str, **parametri) -> dict:
        sale = secrets.token_hex(6)
        token = hashlib.md5((self.password + sale).encode()).hexdigest()  # noqa: S324 - lo chiede l'API Subsonic
        params = [("u", self.utente), ("t", token), ("s", sale), ("v", "1.16.1"), ("c", CLIENTE), ("f", "json")]
        for chiave, valore in parametri.items():
            for v in valore if isinstance(valore, list | tuple) else [valore]:
                params.append((chiave, str(v)))
        try:
            r = await self.client.get(f"{self.config.navidrome_url}{BASE}/rest/{metodo}", params=params, timeout=30)
            dati = r.json().get("subsonic-response") or {}
        except (httpx.HTTPError, ValueError) as exc:
            raise non_raggiungibile("Navidrome", "/navidrome") from exc
        if dati.get("status") != "ok":
            errore = dati.get("error") or {}
            if errore.get("code") in (40, 41):
                raise ErroreServizio(
                    f"Navidrome non accetta l'utente \"{self.utente}\".",
                    "Se hai cambiato la password in Navidrome, rimetti quella di prima (il nome in minuscolo, "
                    "o la password di mediabox per l'amministratore).")
            raise ErroreServizio(f"Navidrome ha risposto con un errore: {errore.get('message') or 'sconosciuto'}.")
        return dati

    async def cerca_brano(self, artista: str, titolo: str) -> str | None:
        """L'id del brano se è già nella musica di casa."""
        dati = await self._rest("search3", query=f"{artista} {titolo}".strip(), songCount=20, artistCount=0,
                                albumCount=0)
        for song in (dati.get("searchResult3") or {}).get("song") or []:
            if stesso_brano(song, artista, titolo):
                return song.get("id")
        return None

    async def playlist(self, nome: str) -> str | None:
        dati = await self._rest("getPlaylists")
        for p in (dati.get("playlists") or {}).get("playlist") or []:
            if p.get("name") == nome and (p.get("owner") or self.utente) == self.utente:
                return p.get("id")
        return None

    async def crea_playlist(self, nome: str, brani: list[str]) -> str:
        dati = await self._rest("createPlaylist", name=nome, songId=brani)
        id_playlist = (dati.get("playlist") or {}).get("id")
        return id_playlist or await self.playlist(nome) or ""

    async def aggiungi(self, id_playlist: str, brani: list[str]) -> None:
        if brani:
            await self._rest("updatePlaylist", playlistId=id_playlist, songIdToAdd=brani)

    async def preferiti(self, brani: list[str]) -> None:
        if brani:
            await self._rest("star", id=brani)

    async def scansiona(self) -> None:
        await self._rest("startScan")
