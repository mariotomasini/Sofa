"""Internet Archive: ricerca dei film e scelta della versione migliore."""

from __future__ import annotations

import asyncio
import re
import unicodedata

import httpx

from .db import Archivio
from .rete import ErroreServizio, Limitatore
from .scelta_archive import classifica_item

RICERCA = "https://archive.org/advancedsearch.php"
METADATA = "https://archive.org/metadata"

# Archive chiede di non esagerare: al massimo 2 richieste al secondo.
_limitatore = Limitatore(per_secondo=2)
UN_GIORNO = 24 * 3600
ITEM_DA_ANALIZZARE = 6
PAROLE_SOSPETTE = {"trailer", "teaser", "clip", "sample", "preview", "anteprima", "promo", "featurette"}


def normalizza(testo: str) -> str:
    """Minuscole, senza accenti né punteggiatura: per confrontare i titoli."""
    testo = unicodedata.normalize("NFKD", testo or "")
    testo = "".join(c for c in testo if not unicodedata.combining(c)).lower()
    testo = re.sub(r"[^a-z0-9]+", " ", testo)
    return " ".join(testo.split())


def query_ricerca(titoli: list[str]) -> str:
    varianti = []
    for t in titoli:
        pulito = re.sub(r'["\\():]', " ", t).strip()
        if pulito and pulito not in varianti:
            varianti.append(pulito)
    parte_titoli = " OR ".join(f'title:("{v}")' for v in varianti)
    return f"({parte_titoli}) AND mediatype:(movies)"


ARTICOLI = {"the", "a", "an", "il", "lo", "la", "i", "gli", "le", "un", "una", "l"}
# parole che spesso accompagnano il titolo senza cambiare il film
CONTORNO = re.compile(r"^(\d+p?|hd|sd|dvd|dvdrip|rip|ita|eng|sub|subs|film|movie|full|versione|restaurato|restored|bn)$")


def _titolo_corrisponde(titolo: str, titolo_doc: str) -> bool:
    """Il titolo del film apre quello dell'item, oppure c'è dentro con poche parole in più.

    Così "Matrix" non prende "Lezioni di matematica: Advanced Matrix Theory".
    """
    if not titolo or titolo not in titolo_doc:
        return False
    parole = titolo_doc.split()
    while parole and parole[0] in ARTICOLI:
        parole = parole[1:]
    if " ".join(parole).startswith(titolo.removeprefix("the ")) or titolo_doc.startswith(titolo):
        return True
    extra = [w for w in titolo_doc.split() if w not in set(titolo.split()) and not CONTORNO.match(w)]
    return len(extra) <= 3


def pertinente(doc: dict, titoli: list[str], anno: str) -> bool:
    """Tiene solo gli item che sembrano davvero il film cercato."""
    titolo_doc = normalizza(str(doc.get("title", "")))
    if not titolo_doc:
        return False
    parole_doc = set(titolo_doc.split())
    if parole_doc & PAROLE_SOSPETTE:
        return False
    if not any(_titolo_corrisponde(normalizza(t), titolo_doc) for t in titoli):
        return False
    anno_doc = str(doc.get("year", "") or "")[:4]
    anni_nel_titolo = set(re.findall(r"\b(19\d\d|20\d\d)\b", titolo_doc))
    if anno and anno.isdigit():
        vicini = {str(int(anno) + d) for d in (-1, 0, 1)}
        if anno_doc and anno_doc.isdigit() and anno_doc not in vicini:
            return False
        if anni_nel_titolo and not anni_nel_titolo & vicini:
            return False
    return True


class InternetArchive:
    def __init__(self, archivio: Archivio, client: httpx.AsyncClient) -> None:
        self.archivio = archivio
        self.client = client

    async def _get_json(self, url: str, parametri: dict | None, chiave_cache: str) -> dict:
        in_cache = self.archivio.cache_leggi(chiave_cache)
        if in_cache is not None:
            return in_cache
        await _limitatore.attendi()
        try:
            r = await self.client.get(url, params=parametri, timeout=30)
        except httpx.HTTPError as exc:
            raise ErroreServizio(
                "Internet Archive non risponde.",
                "Può capitare quando è molto carico: riprova tra qualche minuto.",
            ) from exc
        if r.status_code == 429:
            raise ErroreServizio("Internet Archive chiede di rallentare.", "Aspetta un minuto e riprova.")
        if not r.is_success:
            raise ErroreServizio(
                f"Internet Archive ha risposto con un errore ({r.status_code}).", "Riprova tra qualche minuto."
            )
        dati = r.json()
        self.archivio.cache_scrivi(chiave_cache, dati, UN_GIORNO)
        return dati

    async def cerca_item(self, titoli: list[str], anno: str) -> list[dict]:
        parametri = {
            "q": query_ricerca(titoli),
            "fl[]": ["identifier", "title", "year", "downloads", "item_size"],
            "sort[]": "downloads desc",
            "rows": "50",
            "output": "json",
        }
        dati = await self._get_json(RICERCA, parametri, f"ia:cerca:{parametri['q']}")
        documenti = dati.get("response", {}).get("docs", [])
        return [d for d in documenti if pertinente(d, titoli, anno)]

    async def metadata(self, identificativo: str) -> dict:
        return await self._get_json(f"{METADATA}/{identificativo}", None, f"ia:meta:{identificativo}")

    async def migliori_versioni(self, film: dict) -> dict:
        """Cerca il film e restituisce le versioni trovate, dalla migliore."""
        titoli = [film.get("titolo_originale", ""), film.get("titolo", "")]
        titoli = [t for t in titoli if t]
        item = await self.cerca_item(titoli, film.get("anno", ""))
        if not item:
            return {"versioni": [], "item_trovati": 0}
        da_analizzare = item[:ITEM_DA_ANALIZZARE]
        risposte = await asyncio.gather(
            *(self.metadata(d["identifier"]) for d in da_analizzare), return_exceptions=True
        )
        metadati = [m for m in risposte if isinstance(m, dict) and m.get("files")]
        durata = film.get("durata") or 0
        # Scarta spezzoni: almeno il 60% della durata del film (se nota)
        durata_minima = durata * 60 * 0.6 if durata else 40 * 60
        titoli_item = {d["identifier"]: d.get("title", "") for d in da_analizzare}
        scaricamenti = {d["identifier"]: int(d.get("downloads") or 0) for d in da_analizzare}
        versioni = []
        for f in classifica_item(metadati, durata_minima=durata_minima):
            voce = f.come_dizionario()
            voce["titolo_item"] = titoli_item.get(f.identificativo, "")
            voce["scaricamenti"] = scaricamenti.get(f.identificativo, 0)
            versioni.append(voce)
        return {"versioni": versioni, "item_trovati": len(item)}
