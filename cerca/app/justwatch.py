"""JustWatch: cosa è di tendenza sulle piattaforme di streaming in Italia.

Non serve nessuna chiave. Da qui arrivano le righe della home senza chiave TMDB:
titoli in italiano, locandine, trama e l'id TMDB (che usano Radarr e Cerca).
Sono film e serie che la gente sta guardando adesso su Netflix, Prime Video e
le altre: proporre quelli ha più senso, e si trovano più facilmente.
"""

from __future__ import annotations

INDIRIZZO = "https://apis.justwatch.com/graphql"
IMMAGINI = "https://images.justwatch.com"

# Le piattaforme: id TMDB (così le scelte valgono anche con la chiave), nome, codice JustWatch
PIATTAFORME = [
    (8, "Netflix", "nfx"),
    (119, "Amazon Prime Video", "prv"),
    (337, "Disney Plus", "dnp"),
    (39, "NOW", "ntv"),
    (350, "Apple TV+", "atp"),
    (531, "Paramount Plus", "pmp"),
    (109, "TIMvision", "tvi"),
    (222, "RaiPlay", "rai"),
    (359, "Mediaset Infinity", "msp"),
    (283, "Crunchyroll", "cru"),
]
CODICI = {i: codice for i, _, codice in PIATTAFORME}
# Le piattaforme in abbonamento più diffuse: da qui vengono Top 10 e novità
PRINCIPALI = ["nfx", "prv", "dnp", "ntv", "atp", "pmp", "tvi", "rai", "msp"]

# Generi: id TMDB → codice JustWatch (JustWatch unisce azione e avventura)
GENERI = {
    28: "act", 12: "act", 10759: "act", 16: "ani", 35: "cmy", 80: "crm", 99: "doc", 18: "drm",
    10751: "fml", 10762: "fml", 14: "fnt", 10765: "scf", 36: "hst", 10768: "war", 27: "hrr", 10402: "msc",
    9648: "trl", 53: "trl", 10749: "rma", 878: "scf", 10752: "war", 37: "wsn",
}
# Codice JustWatch → id TMDB dei film (per i filtri dei generi e per Kids)
DA_CODICE = {"act": 28, "ani": 16, "cmy": 35, "crm": 80, "doc": 99, "drm": 18, "fml": 10751, "fnt": 14,
             "hst": 36, "hrr": 27, "msc": 10402, "trl": 53, "rma": 10749, "scf": 878, "war": 10752, "wsn": 37}
NO_KIDS = ["hrr", "trl", "crm", "war"]
REALITY = "rly"  # reality e talent (X Factor…): non sono le serie che si cercano qui

DOMANDA = """
query Titoli($paese: Country!, $lingua: Language!, $quanti: Int!, $filtro: TitleFilter, $ordine: PopularTitlesSorting!) {
  popularTitles(country: $paese, first: $quanti, filter: $filtro, sortBy: $ordine) {
    edges { node { objectType content(country: $paese, language: $lingua) {
      title originalTitle originalReleaseYear posterUrl shortDescription runtime
      genres { shortName } scoring { imdbScore } externalIds { tmdbId imdbId }
    } } }
  }
}
"""


def corpo(*, serie: bool, ordine: str = "TRENDING", quanti: int = 40, piattaforme: list[str] | None = None,
          generi: list[str] | None = None, escludi: list[str] | None = None, dal_anno: int | None = None,
          paese: str = "IT", lingua: str = "it") -> dict:
    """La richiesta per JustWatch: solo titoli in abbonamento, gratis o con pubblicità."""
    filtro: dict = {
        "objectTypes": ["SHOW" if serie else "MOVIE"],
        "monetizationTypes": ["FLATRATE", "FREE", "ADS"],
        "packages": piattaforme or PRINCIPALI,
    }
    if generi:
        filtro["genres"] = generi
    escludi = list(dict.fromkeys([*(escludi or []), *([REALITY] if serie else [])]))
    if escludi:
        filtro["excludeGenres"] = escludi
    if dal_anno:
        filtro["releaseYear"] = {"min": dal_anno}
    return {"query": DOMANDA, "variables": {"paese": paese, "lingua": lingua, "quanti": quanti,
                                            "filtro": filtro, "ordine": ordine}}


def immagine(percorso: str | None, misura: str = "s332") -> str:
    if not percorso:
        return ""
    return IMMAGINI + percorso.replace("{profile}", misura).replace("{format}", "webp")


def riassunto(nodo: dict) -> dict | None:
    """Un titolo di JustWatch nello stesso formato dei film di Cerca. None se è da scartare."""
    c = nodo.get("content") or {}
    esterni = c.get("externalIds") or {}
    tmdb = str(esterni.get("tmdbId") or "")
    serie = nodo.get("objectType") == "SHOW"
    # titoli "sensati": con id TMDB e IMDb (niente extra, trailer o contenuti speciali) e con la locandina
    if not tmdb.isdigit() or not esterni.get("imdbId") or not c.get("posterUrl") or not c.get("title"):
        return None
    if not serie and (c.get("runtime") or 0) < 60:
        return None
    codici = [g.get("shortName") for g in c.get("genres") or []]
    voto = (c.get("scoring") or {}).get("imdbScore") or 0
    return {
        "id": int(tmdb),
        "imdb_id": esterni["imdbId"],
        "titolo": c["title"].strip(),
        "titolo_originale": (c.get("originalTitle") or c["title"]).strip(),
        "anno": str(c.get("originalReleaseYear") or ""),
        "poster": immagine(c["posterUrl"]),
        "trama": (c.get("shortDescription") or "").strip(),
        "voto": round(float(voto), 1),
        "genre_ids": [DA_CODICE[x] for x in codici if x in DA_CODICE],
        **({"serie": True} if serie else {}),
    }


def titoli(risposta: dict) -> list[dict]:
    nodi = (((risposta or {}).get("data") or {}).get("popularTitles") or {}).get("edges") or []
    visti: set[int] = set()
    elenco = []
    for nodo in nodi:
        t = riassunto(nodo.get("node") or {})
        if t and t["id"] not in visti:
            visti.add(t["id"])
            elenco.append(t)
    return elenco
