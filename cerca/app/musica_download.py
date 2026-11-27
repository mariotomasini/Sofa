"""Scaricare la musica: un brano o un album, con un tocco.

Da Deezer arrivano artista, titolo e album; Prowlarr cerca nella categoria Audio
dei tuoi indexer, Cerca sceglie il torrent migliore e qBittorrent lo salva subito
nella cartella della musica (Musica/Artista/…), che Jellyfin legge da sola.
Per un brano preso da un album intero si scarica solo quel file.

Non serve Lidarr: funziona con gli indexer che hai già.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from .archive import normalizza
from .rete import ErroreServizio

CATEGORIA_AUDIO = 3000
CATEGORIA_QBIT = "musica"
ESTENSIONI_AUDIO = (".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".alac", ".wma")
RE_DISCOGRAFIA = re.compile(r"\b(discograph\w*|discografia|collection|collezione|anthology|complete works|"
                            r"greatest hits|best of|the best|raccolta)\b", re.I)
RE_MP3 = re.compile(r"\b(mp3|320\s?kbps|320k|320)\b", re.I)
MAX_BRANO = 3 * 1024**3
MAX_ALBUM = 6 * 1024**3


def _contiene(nome: str, testo: str) -> bool:
    return bool(testo) and f" {testo} " in f" {nome} "


def adatto(r: dict, artista: str, titoli: list[str]) -> bool:
    """Il torrent parla di quell'artista e di quel brano o album."""
    nome = normalizza(r.get("title", ""))
    return _contiene(nome, normalizza(artista)) and any(_contiene(nome, normalizza(t)) for t in titoli if t)


def scegli(risultati: list[dict], artista: str, titoli: list[str], massimo: int, min_fonti: int = 3) -> dict | None:
    """Il torrent da scaricare: giusto, con abbastanza fonti per finire, non una discografia intera,
    meglio MP3 (più leggero)."""
    candidati = [r for r in risultati
                 if r.get("protocol") == "torrent" and (r.get("seeders") or 0) > 0
                 and (r.get("size") or 0) <= massimo and adatto(r, artista, titoli)]
    if not candidati:
        return None

    def chiave(r: dict) -> tuple:
        nome = r.get("title", "")
        seed = r.get("seeders") or 0
        return (seed >= min_fonti, not RE_DISCOGRAFIA.search(nome), bool(RE_MP3.search(nome)), seed)

    return max(candidati, key=chiave)


def nome_cartella(testo: str) -> str:
    """Un nome di cartella valido anche su Windows."""
    pulito = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", testo).strip(" .")
    return " ".join(pulito.split())[:80] or "Sconosciuto"


def file_da_saltare(file: list[dict], titolo: str) -> list[int]:
    """Gli indici dei brani da non scaricare: tutti gli audio tranne quello col titolo giusto.
    Se nessun file ha quel titolo si scarica tutto (meglio un album intero che niente)."""
    voluto = normalizza(titolo)
    audio = [f for f in file if f.get("name", "").lower().endswith(ESTENSIONI_AUDIO)]
    giusti = [f for f in audio if _contiene(normalizza(Path(f["name"]).stem), voluto)]
    if not giusti:
        return []
    tenuti = {f["index"] for f in giusti}
    return [f["index"] for f in audio if f["index"] not in tenuti]


class ScaricaMusica:
    def __init__(self, config, deezer, prowlarr, download) -> None:
        self.config = config
        self.deezer = deezer
        self.prowlarr = prowlarr
        self.download = download

    async def _cerca(self, testi: list[str]) -> list[dict]:
        """Le ricerche partono insieme: una dopo l'altra, con indexer lenti, ci volevano minuti."""
        testi = list(dict.fromkeys(t.strip() for t in testi if t and t.strip()))
        risposte = await asyncio.gather(*(self.prowlarr._cerca_testo(t, categoria=CATEGORIA_AUDIO) for t in testi),
                                        return_exceptions=True)
        if risposte and all(isinstance(r, BaseException) for r in risposte):
            raise risposte[0]
        risultati: dict[str, dict] = {}
        for risposta in risposte:
            if isinstance(risposta, BaseException):
                continue
            for r in risposta:
                risultati.setdefault(f"{r.get('indexerId')}|{r.get('guid')}", r)
        return list(risultati.values())

    def _non_trovato(self, cosa: str) -> ErroreServizio:
        return ErroreServizio(
            f"Non trovo {cosa} sui tuoi indexer.",
            "I brani molto nuovi o di artisti piccoli spesso non ci sono: prova l'album intero, "
            "riprova tra qualche giorno o aggiungi in Prowlarr un indexer che ha musica.")

    def _in_elenco(self, artista: str, titolo: str, copertina: str, origine: str, fonte: str = "prowlarr") -> int:
        """La voce compare subito nella pagina Download ("Cerco sugli indexer"), prima della ricerca,
        che con molti indexer dura anche un minuto o due."""
        return self.download.archivio.nuovo_download(
            tmdb_id=None, titolo=f"{artista} · {titolo}", anno="", poster=copertina, fonte=fonte,
            tipo="torrent", url=origine, categoria=CATEGORIA_QBIT, stato="ricerca")

    async def _avvia(self, id_download: int, scelto: dict, artista: str, titolo: str, copertina: str,
                     solo_brano: str = "") -> int:
        magnet, file_torrent = await self.prowlarr.scarica_torrent(scelto)
        cartella = str(Path(self.config.cartella_musica) / nome_cartella(artista))
        return await self.download.da_torrent(
            fonte="prowlarr", categoria=CATEGORIA_QBIT, film=None, titolo=f"{artista} · {titolo}",
            magnet=magnet, file_torrent=file_torrent, poster=copertina, cartella=cartella, solo_brano=solo_brano,
            id_download=id_download)

    async def _dati_brano(self, id_brano: int) -> tuple[str, str, str, str]:
        t = await self.deezer._get(f"track/{id_brano}", durata=7 * 24 * 3600)
        artista = (t.get("artist") or {}).get("name", "")
        titolo = t.get("title_short") or t.get("title") or ""
        album = (t.get("album") or {})
        if not artista or not titolo:
            raise ErroreServizio("Deezer non mi dà i dati di questo brano.", "Riprova tra poco.")
        return artista, titolo, album.get("title", ""), album.get("cover_medium") or ""

    async def _dati_album(self, id_album: int) -> tuple[str, str, str, str]:
        a = await self.deezer._get(f"album/{id_album}", durata=7 * 24 * 3600)
        artista = (a.get("artist") or {}).get("name", "")
        titolo = a.get("title", "")
        if not artista or not titolo:
            raise ErroreServizio("Deezer non mi dà i dati di questo album.", "Riprova tra poco.")
        return artista, titolo, (a.get("release_date") or "")[:4], a.get("cover_medium") or ""

    async def _scarica_brano(self, id_download: int, artista: str, titolo: str, nome_album: str, copertina: str) -> None:
        risultati = await self._cerca([f"{artista} {titolo}", f"{artista} {nome_album}"])
        # prima un torrent del brano stesso (o del singolo); se no l'album che lo contiene, solo quel file
        scelto = scegli(risultati, artista, [titolo], MAX_BRANO, self.config.min_fonti)
        if scelto is None and nome_album and normalizza(nome_album) != normalizza(titolo):
            scelto = scegli(risultati, artista, [nome_album], MAX_ALBUM, self.config.min_fonti)
        if scelto is None:
            raise self._non_trovato(f"\"{titolo}\" di {artista}")
        # in un album o in una raccolta si scarica solo il file di questo brano
        await self._avvia(id_download, scelto, artista, titolo, copertina, solo_brano=titolo)

    async def _scarica_album(self, id_download: int, artista: str, titolo: str, anno: str, copertina: str) -> None:
        scelto = scegli(await self._cerca([f"{artista} {titolo}", f"{artista} {titolo} {anno}"]),
                        artista, [titolo], MAX_ALBUM, self.config.min_fonti)
        if scelto is None:
            raise self._non_trovato(f"l'album \"{titolo}\" di {artista}")
        await self._avvia(id_download, scelto, artista, titolo, copertina)

    async def brano(self, id_brano: int, fonte: str = "prowlarr") -> dict:
        """fonte="importazione": dall'importazione delle playlist, senza un avviso per ogni brano."""
        artista, titolo, nome_album, copertina = await self._dati_brano(id_brano)
        id_download = self._in_elenco(artista, titolo, copertina, f"deezer:brano:{id_brano}", fonte)
        self.download.cerca_in_sottofondo(
            id_download, self._scarica_brano(id_download, artista, titolo, nome_album, copertina))
        return {"id": id_download,
                "messaggio": f"\"{titolo}\" di {artista} è nella pagina Download: lo cerco sui tuoi indexer e "
                             "appena finisce lo trovi in Jellyfin, nella Musica."}

    async def album(self, id_album: int) -> dict:
        artista, titolo, anno, copertina = await self._dati_album(id_album)
        id_download = self._in_elenco(artista, titolo, copertina, f"deezer:album:{id_album}")
        self.download.cerca_in_sottofondo(
            id_download, self._scarica_album(id_download, artista, titolo, anno, copertina))
        return {"id": id_download,
                "messaggio": f"L'album \"{titolo}\" di {artista} è nella pagina Download: lo cerco sui tuoi indexer "
                             "e appena finisce lo trovi in Jellyfin, nella Musica."}

    async def riprova(self, voce: dict) -> bool:
        """Rifà la ricerca di una voce rimasta senza torrent. False se la voce non è di questo tipo."""
        origine = (voce.get("url") or "").split(":")
        if len(origine) != 3 or origine[0] != "deezer" or not origine[2].isdigit() or voce.get("hash"):
            return False
        id_download, id_deezer = voce["id"], int(origine[2])
        if origine[1] == "album":
            artista, titolo, anno, copertina = await self._dati_album(id_deezer)
            lavoro = self._scarica_album(id_download, artista, titolo, anno, copertina)
        else:
            artista, titolo, nome_album, copertina = await self._dati_brano(id_deezer)
            lavoro = self._scarica_brano(id_download, artista, titolo, nome_album, copertina)
        self.download.archivio.aggiorna_download(id_download, stato="ricerca", messaggio="")
        self.download.cerca_in_sottofondo(id_download, lavoro)
        return True
