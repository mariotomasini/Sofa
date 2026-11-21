"""Deezer: copertine, foto degli artisti, classifiche italiane e anteprime da 30 secondi.

L'API pubblica di Deezer non chiede nessuna chiave. Da qui arrivano le immagini e le
classifiche della sezione Musica; i suggerimenti "per te" restano di ListenBrainz
(dagli artisti che ascolti in Jellyfin) e i download di Lidarr.

Le classifiche (/chart/0) seguono il paese da cui arriva la richiesta: dal PC di casa,
in Italia, sono quelle italiane. "Top Italy" è la playlist ufficiale delle classifiche.
"""

from __future__ import annotations

import asyncio

import httpx

from .db import Archivio
from .rete import ErroreServizio, Limitatore

API = "https://api.deezer.com"
TOP_ITALIA = 1116187241  # playlist "Top Italy" di Deezer Charts
UN_GIORNO = 24 * 3600
_limite = Limitatore(per_secondo=8)  # Deezer permette 50 richieste ogni 5 secondi


def brano(t: dict) -> dict:
    album, artista = t.get("album") or {}, t.get("artist") or {}
    return {
        "id": t.get("id"), "titolo": t.get("title_short") or t.get("title") or "", "durata": t.get("duration") or 0,
        "artista": artista.get("name", ""), "artista_id": artista.get("id"),
        "album": album.get("title", ""), "album_id": album.get("id"),
        "copertina": album.get("cover_medium") or album.get("cover") or "",
        "anteprima": bool(t.get("preview")),
    }


def artista(a: dict) -> dict:
    return {"id": a.get("id"), "nome": a.get("name", ""), "foto": a.get("picture_medium") or "",
            "foto_grande": a.get("picture_xl") or a.get("picture_big") or "", "fan": a.get("nb_fan") or 0}


def album(a: dict) -> dict:
    autore = a.get("artist") or {}
    return {"id": a.get("id"), "titolo": a.get("title", ""), "artista": autore.get("name", ""),
            "artista_id": autore.get("id"), "copertina": a.get("cover_medium") or "",
            "anno": (a.get("release_date") or "")[:4], "uscita": a.get("release_date") or "",
            "tipo": a.get("record_type") or "album"}


class Deezer:
    def __init__(self, archivio: Archivio, client: httpx.AsyncClient) -> None:
        self.archivio = archivio
        self.client = client

    async def _get(self, percorso: str, parametri: dict | None = None, durata: float = UN_GIORNO) -> dict:
        chiave = f"deezer:{percorso}:{sorted((parametri or {}).items())}"
        in_cache = await asyncio.to_thread(self.archivio.cache_leggi, chiave)
        if in_cache is not None:
            return in_cache
        await _limite.attendi()
        try:
            r = await self.client.get(f"{API}/{percorso}", params=parametri, timeout=20)
            dati = r.json() if r.content else {}
        except (httpx.HTTPError, ValueError) as exc:
            if (riserva := await asyncio.to_thread(self.archivio.cache_leggi, chiave, scadute=True)) is not None:
                return riserva
            raise ErroreServizio("Non riesco a contattare Deezer.",
                                 "Controlla la connessione a Internet e riprova.") from exc
        if isinstance(dati, dict) and dati.get("error"):
            if (riserva := await asyncio.to_thread(self.archivio.cache_leggi, chiave, scadute=True)) is not None:
                return riserva
            raise ErroreServizio("Deezer non risponde come dovrebbe.", "Riprova tra poco.")
        await asyncio.to_thread(self.archivio.cache_scrivi, chiave, dati, durata)
        return dati

    # --- classifiche ------------------------------------------------------------------------
    async def top_italia(self, quanti: int = 50) -> list[dict]:
        dati = await self._get(f"playlist/{TOP_ITALIA}/tracks", {"limit": quanti}, 6 * 3600)
        return [brano(t) for t in dati.get("data", [])]

    async def album_di_tendenza(self, quanti: int = 20) -> list[dict]:
        return [album(a) for a in (await self._get("chart/0/albums", {"limit": quanti}, 6 * 3600)).get("data", [])]

    async def artisti_di_tendenza(self, quanti: int = 20) -> list[dict]:
        return [artista(a) for a in (await self._get("chart/0/artists", {"limit": quanti}, 6 * 3600)).get("data", [])]

    # --- artisti ------------------------------------------------------------------------------
    async def trova_artista(self, nome: str) -> dict | None:
        """L'artista di Deezer con quel nome (per la foto degli artisti di Jellyfin e di ListenBrainz)."""
        dati = await self._get("search/artist", {"q": nome, "limit": 3}, 30 * UN_GIORNO)
        voci = dati.get("data") or []
        uguale = next((a for a in voci if a.get("name", "").casefold() == nome.casefold()), None)
        scelto = uguale or (voci[0] if voci else None)
        return artista(scelto) if scelto else None

    async def con_foto(self, nomi: list[str]) -> list[dict]:
        trovati = await asyncio.gather(*(self.trova_artista(n) for n in nomi), return_exceptions=True)
        return [t for t in trovati if isinstance(t, dict) and t.get("foto")]

    async def pagina_artista(self, id_artista: int) -> dict:
        info, top, dischi, simili = await asyncio.gather(
            self._get(f"artist/{id_artista}", durata=7 * UN_GIORNO),
            self._get(f"artist/{id_artista}/top", {"limit": 10}, UN_GIORNO),
            self._get(f"artist/{id_artista}/albums", {"limit": 50}, UN_GIORNO),
            self._get(f"artist/{id_artista}/related", {"limit": 20}, 7 * UN_GIORNO),
        )
        elenco = sorted((album(a) for a in dischi.get("data", [])), key=lambda a: a["uscita"], reverse=True)
        return {
            **artista(info),
            "popolari": [brano(t) for t in top.get("data", [])],
            "album": [a for a in elenco if a["tipo"] == "album"],
            "singoli": [a for a in elenco if a["tipo"] != "album"],
            "simili": [artista(a) for a in simili.get("data", [])],
        }

    async def uscite_recenti(self, ids: list[int], dal: str) -> list[dict]:
        """Gli album e i singoli usciti da "dal" (AAAA-MM-GG) degli artisti indicati, dal più recente."""
        risposte = await asyncio.gather(*(self._get(f"artist/{i}/albums", {"limit": 10}, UN_GIORNO) for i in ids),
                                        return_exceptions=True)
        uscite = [album(a) for r in risposte if isinstance(r, dict) for a in r.get("data", [])]
        return sorted((a for a in uscite if a["uscita"] >= dal), key=lambda a: a["uscita"], reverse=True)

    async def pagina_album(self, id_album: int) -> dict:
        a = await self._get(f"album/{id_album}", durata=7 * UN_GIORNO)
        return {**album(a), "copertina_grande": a.get("cover_xl") or a.get("cover_big") or "",
                "brani": [{**brano({**t, "album": {"title": a.get("title"), "id": a.get("id"),
                                                     "cover_medium": a.get("cover_medium")}}),
                           "numero": i + 1} for i, t in enumerate((a.get("tracks") or {}).get("data", []))],
                "durata": a.get("duration") or 0, "etichetta": a.get("label") or ""}

    # --- ricerca e ascolto ----------------------------------------------------------------------
    async def cerca(self, testo: str) -> dict:
        artisti, brani, dischi = await asyncio.gather(
            self._get("search/artist", {"q": testo, "limit": 8}, UN_GIORNO),
            self._get("search/track", {"q": testo, "limit": 10}, UN_GIORNO),
            self._get("search/album", {"q": testo, "limit": 10}, UN_GIORNO),
        )
        return {"artisti": [artista(a) for a in artisti.get("data", [])],
                "brani": [brano(t) for t in brani.get("data", [])],
                "album": [album(a) for a in dischi.get("data", [])]}

    async def anteprima(self, id_brano: int) -> dict:
        """L'indirizzo dell'anteprima da 30 s: Deezer lo firma con una scadenza, quindi si chiede al momento."""
        t = await self._get(f"track/{id_brano}", durata=20 * 60)
        if not t.get("preview"):
            raise ErroreServizio("Per questo brano non c'è l'anteprima.", "Prova un altro brano.")
        return {**brano(t), "url": t["preview"]}
