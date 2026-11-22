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
    (119, "Prime Video", "prv"),
    (337, "Disney+", "dnp"),
    (1899, "HBO Max", "mxx"),
    (39, "NOW", "ntv"),
    (29, "Sky Go", "skg"),
    (350, "Apple TV+", "atp"),
    (531, "Paramount+", "pmp"),
    (283, "Crunchyroll", "cru"),
    (222, "RaiPlay", "rai"),
    (359, "Mediaset Infinity", "msp"),
    (109, "TIMvision", "tvi"),
    (524, "discovery+", "dpe"),
]
CODICI = {i: codice for i, _, codice in PIATTAFORME}
# I loghi delle piattaforme, dalle immagini pubbliche di JustWatch (nessuna chiave):
# codice → "id/nome" di images.justwatch.com/icon/<id>/s100/<nome>.webp
ICONE = {
    "nfx": "207360008/netflix", "prv": "322992749/amazonprimevideo", "dnp": "313118777/disneyplus",
    "mxx": "332884837/max", "ntv": "243753092/nowtv", "skg": "346742420/skygo", "atp": "357333613/appletvplus",
    "pmp": "242706661/paramountplus", "cru": "324213205/crunchyroll", "rai": "87462522/raiplay",
    "msp": "312083440/mediasetplay", "tvi": "344848278/timvision", "dpe": "241126382/discoverypluseurope",
}


def logo(id_tmdb: int) -> str:
    """Il logo vero della piattaforma (id TMDB), "" se non lo conosciamo."""
    icona = ICONE.get(CODICI.get(id_tmdb, ""))
    if not icona:
        return ""
    numero, nome = icona.split("/")
    return f"{IMMAGINI}/icon/{numero}/s100/{nome}.webp"
# Le piattaforme in abbonamento più diffuse: da qui vengono Top 10 e novità
PRINCIPALI = ["nfx", "prv", "dnp", "mxx", "ntv", "skg", "atp", "pmp", "tvi", "rai", "msp"]

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

CAMPI = """edges { node { objectType content(country: $paese, language: $lingua) {
      title originalTitle originalReleaseYear posterUrl shortDescription runtime
      genres { shortName } scoring { imdbScore } externalIds { tmdbId imdbId }%s
    } } }"""
DOMANDA = """
query Titoli($paese: Country!, $lingua: Language!, $quanti: Int!, $filtro: TitleFilter, $ordine: PopularTitlesSorting!) {
  popularTitles(country: $paese, first: $quanti, filter: $filtro, sortBy: $ordine) {
    %s
  }
}
""" % (CAMPI % "")


def corpo(*, serie: bool, ordine: str = "TRENDING", quanti: int = 40, piattaforme: list[str] | None = None,
          generi: list[str] | None = None, escludi: list[str] | None = None, dal_anno: int | None = None,
          paese: str = "IT", lingua: str = "it") -> dict:
    """La richiesta per JustWatch: solo titoli in abbonamento, gratis o con pubblicità."""
    filtro = _filtro(serie=serie, piattaforme=piattaforme, generi=generi, escludi=escludi, dal_anno=dal_anno)
    return {"query": DOMANDA, "variables": {"paese": paese, "lingua": lingua, "quanti": quanti,
                                            "filtro": filtro, "ordine": ordine}}


def _filtro(*, serie: bool, piattaforme: list[str] | None = None, generi: list[str] | None = None,
            escludi: list[str] | None = None, dal_anno: int | None = None) -> dict:
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
    return filtro


def corpo_vista(sezioni: dict[str, dict], *, paese: str = "IT", lingua: str = "it") -> dict:
    """Tante righe in una sola richiesta (una "vista" di piattaforma): ogni sezione ha il suo nome.

    sezioni: {"top": {"serie": False, "piattaforme": ["nfx"], "quanti": 10}, ...}
    La prima sezione porta anche le immagini grandi (per il riquadro in evidenza).
    """
    definizioni = ["$paese: Country!", "$lingua: Language!"]
    variabili: dict = {"paese": paese, "lingua": lingua}
    parti = []
    for i, (nome, opzioni) in enumerate(sezioni.items()):
        opzioni = dict(opzioni)
        quanti, ordine = opzioni.pop("quanti", 20), opzioni.pop("ordine", "TRENDING")
        definizioni += [f"$f{i}: TitleFilter", f"$o{i}: PopularTitlesSorting!", f"$q{i}: Int!"]
        variabili.update({f"f{i}": _filtro(**opzioni), f"o{i}": ordine, f"q{i}": quanti})
        campi = CAMPI % (" backdrops { backdropUrl }" if i == 0 else "")
        parti.append(f"{nome}: popularTitles(country: $paese, first: $q{i}, filter: $f{i}, sortBy: $o{i}) {{ {campi} }}")
    return {"query": f"query Vista({', '.join(definizioni)}) {{ {' '.join(parti)} }}", "variables": variabili}


def titoli_vista(risposta: dict) -> dict[str, list[dict]]:
    dati = (risposta or {}).get("data") or {}
    return {nome: titoli({"data": {"popularTitles": blocco}}) for nome, blocco in dati.items() if blocco}


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
        **({"sfondo": immagine(c["backdrops"][0].get("backdropUrl"), "s1440")} if c.get("backdrops") else {}),
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
