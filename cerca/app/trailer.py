"""Trailer per le anteprime al passaggio: un video YouTube per film o serie.

Da dove arrivano:
- con la chiave TMDB: i video di TMDB, prima in italiano e poi in inglese (film e serie);
- senza chiave: Radarr, che per ogni film conosce il trailer su YouTube (solo film).
JustWatch avrebbe un campo per i video, ma oggi è sempre vuoto: non lo usiamo.
"""

from __future__ import annotations

import logging

from .rete import ErroreServizio

log = logging.getLogger("cerca.trailer")

UNA_SETTIMANA = 7 * 86400
UN_GIORNO = 86400
TIPI = ("movie", "tv")


def migliore(video: list[dict]) -> str:
    """Il video YouTube più adatto: trailer ufficiale prima, poi teaser, poi qualsiasi clip."""
    youtube = [v for v in video or [] if v.get("site") == "YouTube" and v.get("key")]
    if not youtube:
        return ""

    def punteggio(v: dict) -> tuple:
        tipo = {"Trailer": 0, "Teaser": 1}.get(v.get("type", ""), 2)
        return (tipo, not v.get("official", False), -(v.get("size") or 0))

    return sorted(youtube, key=punteggio)[0]["key"]


async def trova(servizi, tipo: str, id_titolo: int) -> str:
    """L'id YouTube del trailer, o "" se non c'è. Il risultato (anche vuoto) resta in memoria."""
    if tipo not in TIPI or id_titolo <= 0:
        return ""
    chiave = f"trailer:{tipo}:{id_titolo}"
    in_memoria = servizi.archivio.cache_leggi(chiave)
    if in_memoria is not None:
        return in_memoria.get("youtube", "")
    youtube = ""
    try:
        if servizi.config.con_tmdb and hasattr(servizi.tmdb, "_get"):
            for lingua in (servizi.config.lingua_tmdb, "en-US"):
                dati = await servizi.tmdb._get(f"/{tipo}/{id_titolo}/videos", {"language": lingua}, UNA_SETTIMANA)
                youtube = migliore(dati.get("results", []))
                if youtube:
                    break
        if not youtube and tipo == "movie" and servizi.config.radarr_chiave:
            youtube = await servizi.radarr.trailer(id_titolo)
    except ErroreServizio as exc:
        # niente trailer non è un errore da mostrare: riproveremo più tardi
        log.info("Trailer non disponibile per %s %s: %s", tipo, id_titolo, exc.cosa)
        servizi.archivio.cache_scrivi(chiave, {"youtube": ""}, 3600)
        return ""
    servizi.archivio.cache_scrivi(chiave, {"youtube": youtube}, UNA_SETTIMANA if youtube else UN_GIORNO)
    return youtube
