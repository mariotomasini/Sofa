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

    def _parametri(self, **parametri) -> list[tuple[str, str]]:
        sale = secrets.token_hex(6)
        token = hashlib.md5((self.password + sale).encode()).hexdigest()  # noqa: S324 - lo chiede l'API Subsonic
        params = [("u", self.utente), ("t", token), ("s", sale), ("v", "1.16.1"), ("c", CLIENTE), ("f", "json")]
        for chiave, valore in parametri.items():
            for v in valore if isinstance(valore, list | tuple) else [valore]:
                params.append((chiave, str(v)))
        return params

    async def apri(self, metodo: str, intestazioni: dict, **parametri) -> httpx.Response:
        """Una risposta da passare così com'è (l'audio di un brano, una copertina)."""
        richiesta = self.client.build_request("GET", f"{self.config.navidrome_url}{BASE}/rest/{metodo}",
                                              params=self._parametri(**parametri), headers=intestazioni,
                                              timeout=httpx.Timeout(30, read=None))
        return await self.client.send(richiesta, stream=True)

    async def _rest(self, metodo: str, **parametri) -> dict:
        params = self._parametri(**parametri)
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

    # --- per ascoltare da Cerca ---

    async def le_mie_playlist(self) -> list[dict]:
        """Le playlist di questo utente (quelle importate e le scoperte della settimana)."""
        dati = await self._rest("getPlaylists")
        return [{"id": p.get("id"), "nome": p.get("name", ""), "brani": p.get("songCount") or 0,
                 "durata": p.get("duration") or 0, "copertina": p.get("coverArt") or "",
                 "aggiornata": p.get("changed") or ""}
                for p in (dati.get("playlists") or {}).get("playlist") or []
                if (p.get("owner") or self.utente) == self.utente]

    async def brani_playlist(self, id_playlist: str) -> dict:
        p = (await self._rest("getPlaylist", id=id_playlist)).get("playlist") or {}
        return {"id": p.get("id"), "nome": p.get("name", ""), "copertina": p.get("coverArt") or "",
                "brani": [brano(e) for e in p.get("entry") or []]}

    async def preferiti_e_frequenti(self) -> list[str]:
        """Gli artisti che questa persona ama: preferiti, album più ascoltati e brani delle sue playlist."""
        artisti: list[str] = []
        stellati = (await self._rest("getStarred2")).get("starred2") or {}
        artisti += [s.get("artist", "") for s in stellati.get("song") or []]
        artisti += [a.get("name", "") for a in stellati.get("artist") or []]
        frequenti = (await self._rest("getAlbumList2", type="frequent", size=50)).get("albumList2") or {}
        artisti += [a.get("artist", "") for a in frequenti.get("album") or []]
        for p in (await self.le_mie_playlist())[:10]:
            artisti += [b["artista"] for b in (await self.brani_playlist(p["id"]))["brani"]]
        return [a for a in artisti if a]


def brano(e: dict) -> dict:
    return {"id": e.get("id"), "titolo": e.get("title", ""), "artista": e.get("artist", ""),
            "album": e.get("album", ""), "durata": e.get("duration") or 0, "copertina": e.get("coverArt") or ""}
