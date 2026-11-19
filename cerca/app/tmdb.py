"""TMDB: ricerca dei film e disponibilità legale in Italia (dati JustWatch)."""

from __future__ import annotations

import httpx

from .config import Config
from .db import Archivio
from .rete import ErroreServizio, Limitatore

API = "https://api.themoviedb.org/3"
IMMAGINI = "https://image.tmdb.org/t/p"

# TMDB oggi tollera circa 50 richieste al secondo: restiamo molto sotto.
_limitatore = Limitatore(per_secondo=20)

UN_GIORNO = 24 * 3600
TIPI_OFFERTA = {
    "ads": "Gratis con pubblicità",
    "free": "Gratis",
    "flatrate": "In abbonamento",
    "rent": "Noleggio",
    "buy": "Acquisto",
}


def url_immagine(percorso: str | None, misura: str = "w342") -> str:
    return f"{IMMAGINI}/{misura}{percorso}" if percorso else ""


def anno(data: str | None) -> str:
    return (data or "")[:4]


def riassunto_film(f: dict) -> dict:
    """Solo i campi che servono alla pagina."""
    return {
        "id": f["id"],
        "titolo": f.get("title") or f.get("original_title") or "",
        "titolo_originale": f.get("original_title") or "",
        "anno": anno(f.get("release_date")),
        "poster": url_immagine(f.get("poster_path")),
        "trama": f.get("overview") or "",
        "voto": round(f.get("vote_average") or 0, 1),
        "popolarita": f.get("popularity") or 0,
    }


def interpreta_provider(risposta: dict, paese: str) -> dict:
    """Trasforma la risposta di /watch/providers in gruppi leggibili."""
    dati = (risposta.get("results") or {}).get(paese) or {}
    gruppi = []
    for chiave, etichetta in TIPI_OFFERTA.items():
        elenco = [
            {
                "nome": p.get("provider_name", ""),
                "logo": url_immagine(p.get("logo_path"), "w92"),
                "id": p.get("provider_id"),
            }
            for p in sorted(dati.get(chiave) or [], key=lambda p: p.get("display_priority", 99))
        ]
        if elenco:
            gruppi.append({"tipo": chiave, "etichetta": etichetta, "provider": elenco})
    gratis = [p for g in gruppi if g["tipo"] in ("ads", "free") for p in g["provider"]]
    return {"link": dati.get("link", ""), "gruppi": gruppi, "gratis": gratis}


class TMDB:
    def __init__(self, config: Config, archivio: Archivio, client: httpx.AsyncClient) -> None:
        self.config = config
        self.archivio = archivio
        self.client = client

    def _autenticazione(self) -> tuple[dict, dict]:
        chiave = self.config.tmdb_chiave
        if not chiave or chiave == "da-impostare":
            raise ErroreServizio(
                "Manca la chiave di TMDB.",
                "Mettila in TMDB_API_KEY nel file .env di mediabox e poi esegui \"mediabox configura\".",
            )
        # Il "Read Access Token" (lungo) va nell'intestazione, la chiave corta nei parametri
        if len(chiave) > 40:
            return {"Authorization": f"Bearer {chiave}"}, {}
        return {}, {"api_key": chiave}

    async def _get(self, percorso: str, parametri: dict, durata_cache: float) -> dict:
        chiave_cache = f"tmdb:{percorso}:{sorted(parametri.items())}"
        in_cache = self.archivio.cache_leggi(chiave_cache)
        if in_cache is not None:
            return in_cache
        intestazioni, auth = self._autenticazione()
        await _limitatore.attendi()
        try:
            r = await self.client.get(f"{API}{percorso}", params={**parametri, **auth}, headers=intestazioni)
        except httpx.HTTPError as exc:
            raise ErroreServizio(
                "Non riesco a contattare TMDB.",
                "Controlla che il PC di mediabox sia collegato a Internet e riprova tra poco.",
            ) from exc
        if r.status_code == 401:
            raise ErroreServizio(
                "TMDB rifiuta la chiave API.",
                "Controlla TMDB_API_KEY nel file .env (themoviedb.org → Impostazioni → API), "
                "poi esegui \"mediabox configura\".",
            )
        if r.status_code == 429:
            raise ErroreServizio("TMDB chiede di rallentare.", "Aspetta qualche secondo e riprova.")
        if r.status_code == 404:
            raise ErroreServizio("TMDB non conosce questo film.", "Torna indietro e scegline un altro.")
        if not r.is_success:
            raise ErroreServizio(f"TMDB ha risposto con un errore ({r.status_code}).", "Riprova tra qualche minuto.")
        dati = r.json()
        self.archivio.cache_scrivi(chiave_cache, dati, durata_cache)
        return dati

    async def cerca(self, testo: str) -> list[dict]:
        dati = await self._get(
            "/search/movie",
            {"query": testo, "language": self.config.lingua_tmdb, "include_adult": "false", "region": self.config.paese},
            UN_GIORNO,
        )
        risultati = [riassunto_film(f) for f in dati.get("results", [])]
        return risultati[:20]

    async def dettagli(self, tmdb_id: int) -> dict:
        dati = await self._get(f"/movie/{tmdb_id}", {"language": self.config.lingua_tmdb}, UN_GIORNO)
        film = riassunto_film(dati)
        film.update(
            durata=dati.get("runtime") or 0,
            imdb_id=dati.get("imdb_id") or "",
            generi=[g["name"] for g in dati.get("genres", [])],
            sfondo=url_immagine(dati.get("backdrop_path"), "w1280"),
        )
        # Il titolo originale serve anche per Internet Archive e Prowlarr
        if not film["titolo_originale"]:
            film["titolo_originale"] = film["titolo"]
        return film

    async def provider(self, tmdb_id: int, durata_cache: float = 12 * 3600) -> dict:
        dati = await self._get(f"/movie/{tmdb_id}/watch/providers", {}, durata_cache)
        return interpreta_provider(dati, self.config.paese)
