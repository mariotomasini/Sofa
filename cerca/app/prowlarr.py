"""Prowlarr: ricerca negli indexer configurati dall'utente."""

from __future__ import annotations

import asyncio
import hashlib
import re

import httpx

from .archive import normalizza
from .config import Config
from .db import Archivio
from .rete import ErroreServizio, non_raggiungibile

DIECI_MINUTI = 600
ARTICOLI = {"the", "a", "an", "il", "lo", "la", "i", "gli", "le", "l", "un", "una", "uno"}


def _senza_articolo(titolo: str) -> str:
    parole = titolo.split()
    return " ".join(parole[1:]) if len(parole) > 1 and parole[0] in ARTICOLI else titolo


PAROLE_ITALIANO = {"ita", "italian", "italiano", "itaeng", "itaen", "iteng"}


def e_italiano(nome: str) -> bool:
    """Vero se il nome del torrent dice che c'è l'audio italiano (ITA, iTALiAN, ITA-ENG...)."""
    return bool(set(normalizza(nome).split()) & PAROLE_ITALIANO)


RE_EPISODIO = re.compile(r"\bS(\d{1,2})[ ._-]?E(\d{1,3})\b", re.IGNORECASE)
RE_STAGIONE = re.compile(r"\b(?:S(\d{1,2})(?![ ._-]?E\d)|stagion[ei][ ._-]*(\d{1,2})|season[ ._-]*(\d{1,2}))\b",
                         re.IGNORECASE)


def contenuto(nome: str) -> str:
    """Cosa c'è nel torrent di una serie: "Stagione 2 · Episodio 3", "Stagione 1" o "" se non si capisce."""
    if m := RE_EPISODIO.search(nome):
        return f"Stagione {int(m.group(1))} · Episodio {int(m.group(2))}"
    if m := RE_STAGIONE.search(nome):
        return f"Stagione {int(next(g for g in m.groups() if g))}"
    if re.search(r"\b(complet[ao]|completa|complete|integrale)\b", nome, re.IGNORECASE):
        return "Serie completa"
    return ""


def corrisponde(nome: str, titoli: list[str], anno: str) -> bool:
    """Vero se il nome del torrent parla proprio di questo film.

    Gli indexer rispondono anche con risultati a caso (parole sparse, altri film):
    il titolo (italiano o originale, anche senza articolo) deve comparire intero nel
    nome, e se il nome ha un anno deve essere quello del film (±1).
    """
    testo = f" {normalizza(nome)} "
    normali = [n for t in titoli if (n := normalizza(t))]
    varianti = {v for n in normali for v in (n, _senza_articolo(n))}
    if not any(f" {v} " in testo for v in varianti):
        return False
    anni = [int(a) for a in re.findall(r"\b(19\d\d|20\d\d)\b", testo)]
    if anno.isdigit() and anni:
        # un anno nel titolo stesso ("Blade Runner 2049") non conta come anno di uscita
        anni_titolo = {int(a) for v in varianti for a in re.findall(r"\b(19\d\d|20\d\d)\b", v)}
        anni = [a for a in anni if a not in anni_titolo]
        if anni and not any(abs(a - int(anno)) <= 1 for a in anni):
            return False
    return True


def id_risultato(r: dict) -> str:
    """Identificativo stabile di un risultato (serve per avviarne il download)."""
    base = f"{r.get('indexerId')}|{r.get('guid')}"
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def riassunto(r: dict) -> dict:
    return {
        "id": id_risultato(r),
        "titolo": r.get("title", ""),
        "dimensione": int(r.get("size") or 0),
        "seed": r.get("seeders"),
        "peer": r.get("leechers"),
        "indexer": r.get("indexer", ""),
        "protocollo": r.get("protocol", ""),
        "pagina": r.get("infoUrl") or "",
        "data": (r.get("publishDate") or "")[:10],
        "italiano": e_italiano(r.get("title", "")),
    }


def ordina(risultati: list[dict]) -> list[dict]:
    """Prima quelli in italiano, poi i torrent con più seed (scaricano prima e meglio)."""
    return sorted(risultati, key=lambda r: (r.get("italiano", False), r["protocollo"] == "torrent", r["seed"] or 0),
                  reverse=True)


class Prowlarr:
    def __init__(self, config: Config, archivio: Archivio, client: httpx.AsyncClient) -> None:
        self.config = config
        self.archivio = archivio
        self.client = client

    def _intestazioni(self) -> dict:
        return {"X-Api-Key": self.config.prowlarr_chiave}

    async def _get(self, percorso: str, parametri: dict | None = None, timeout: float = 20):
        try:
            r = await self.client.get(
                f"{self.config.prowlarr_url}/api/v1/{percorso}",
                params=parametri,
                headers=self._intestazioni(),
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise non_raggiungibile("Prowlarr", "/prowlarr") from exc
        if r.status_code == 401:
            raise ErroreServizio(
                "Prowlarr rifiuta la chiave API.",
                "Esegui \"mediabox restart\" e poi \"mediabox configura\".",
            )
        if not r.is_success:
            raise ErroreServizio(f"Prowlarr ha risposto con un errore ({r.status_code}).", "Riprova tra poco.")
        return await asyncio.to_thread(r.json)  # le ricerche sono grandi: si leggono in un thread

    async def cerca(self, film: dict) -> dict:
        indexer = await self._get("indexer")
        attivi = [i for i in indexer if i.get("enable")]
        if not attivi:
            return {
                "risultati": [],
                "avviso": "Nessun indexer configurato in Prowlarr.",
                "cosa_fare": "Apri Prowlarr dalla pagina iniziale (tessera Prowlarr) e aggiungi i tuoi indexer: "
                "Indexer → Aggiungi indexer.",
            }
        anno = str(film.get("anno") or "")
        # gli indexer italiani usano il titolo italiano, gli altri quello originale: li cerchiamo tutti e due
        titoli = list(dict.fromkeys(t for t in (film.get("titolo_originale"), film.get("titolo")) if t))
        risposte = await asyncio.gather(*(self._cerca_testo(f"{t} {anno}".strip()) for t in titoli))
        grezzi = list({id_risultato(r): r for risposta in risposte for r in risposta}.values())
        # Teniamo i risultati completi per poter avviare il download dopo
        # (centinaia di risultati: si salvano in un thread, senza fermare le pagine)
        await asyncio.to_thread(self.archivio.cache_scrivi, f"prowlarr:film:{film['id']}", grezzi, 6 * 3600)
        torrent = [r for r in grezzi if r.get("protocol") == "torrent"]
        coerenti = [r for r in torrent if corrisponde(r.get("title", ""), titoli, anno)]
        risultati = ordina([riassunto(r) for r in coerenti])
        risposta = {"risultati": risultati[:60], "indexer": len(attivi), "scartati": len(torrent) - len(coerenti)}
        if not risultati and torrent:
            risposta.update(
                avviso="Gli indexer hanno risposto, ma con torrent di altri film.",
                cosa_fare="Questo film forse non c'è sui tuoi indexer: prova ad aggiungerne altri in Prowlarr.",
            )
        return risposta

    async def _cerca_testo(self, testo: str, categoria: int = 2000) -> list[dict]:
        chiave_cache = f"prowlarr:{categoria}:{testo}"
        grezzi = await asyncio.to_thread(self.archivio.cache_leggi, chiave_cache)
        if grezzi is None:
            grezzi = await self._get(
                "search",
                {"query": testo, "type": "search", "categories": [categoria], "limit": 100},
                timeout=90,
            )
            await asyncio.to_thread(self.archivio.cache_scrivi, chiave_cache, grezzi, DIECI_MINUTI)
        return grezzi

    async def cerca_serie(self, serie_id: int, titoli: list[str]) -> dict:
        """Le puntate di una serie (categoria TV), con il titolo italiano e quello originale."""
        indexer = await self._get("indexer")
        attivi = [i for i in indexer if i.get("enable")]
        if not attivi:
            return {"risultati": [], "avviso": "Nessun indexer configurato in Prowlarr.",
                    "cosa_fare": "Apri Prowlarr → Indicizzatori → Aggiungi indicizzatore."}
        titoli = list(dict.fromkeys(t.strip() for t in titoli if t and t.strip()))[:4]
        risposte = await asyncio.gather(*(self._cerca_testo(t, categoria=5000) for t in titoli))
        grezzi = list({id_risultato(r): r for risposta in risposte for r in risposta}.values())
        await asyncio.to_thread(self.archivio.cache_scrivi, f"prowlarr:serie:{serie_id}", grezzi, 6 * 3600)
        torrent = [r for r in grezzi if r.get("protocol") == "torrent"]
        coerenti = [r for r in torrent if corrisponde(r.get("title", ""), titoli, "")]
        risultati = ordina([{**riassunto(r), "contenuto": contenuto(r.get("title", ""))} for r in coerenti])
        risposta = {"risultati": risultati[:60], "indexer": len(attivi)}
        if not risultati:
            risposta.update(avviso="Nessuna puntata trovata sui tuoi indexer.",
                            cosa_fare="Prova più tardi, o aggiungi altri indexer in Prowlarr.")
        return risposta

    def risultato_originale(self, tmdb_id: int, id_ris: str, tipo: str = "film") -> dict:
        grezzi = self.archivio.cache_leggi(f"prowlarr:{tipo}:{tmdb_id}") or []
        for r in grezzi:
            if id_risultato(r) == id_ris:
                return r
        raise ErroreServizio(
            "Questo risultato non è più disponibile.",
            "Ricarica la pagina del film per rifare la ricerca e riprova.",
        )

    async def scarica_torrent(self, risultato: dict) -> tuple[str | None, bytes | None]:
        """Restituisce (magnet, None) oppure (None, contenuto del file .torrent)."""
        magnet = risultato.get("magnetUrl") or ""
        if magnet.startswith("magnet:"):
            return magnet, None
        url = risultato.get("downloadUrl") or magnet
        if not url:
            raise ErroreServizio("Questo risultato non ha un link per il download.", "Scegline un altro.")
        try:
            r = await self.client.get(url, follow_redirects=False, timeout=60)
            salti = 0
            while r.is_redirect and salti < 5:
                destinazione = r.headers.get("location", "")
                if destinazione.startswith("magnet:"):
                    return destinazione, None
                r = await self.client.get(destinazione, follow_redirects=False, timeout=60)
                salti += 1
        except httpx.HTTPError as exc:
            raise ErroreServizio(
                "Non riesco a scaricare il torrent da Prowlarr.",
                "L'indexer potrebbe non rispondere: riprova o scegli un altro risultato.",
            ) from exc
        if not r.is_success or not r.content:
            raise ErroreServizio(
                f"L'indexer non ha restituito il torrent (errore {r.status_code}).", "Scegli un altro risultato."
            )
        return None, r.content
