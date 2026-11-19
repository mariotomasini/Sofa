"""TMDB: ricerca dei film e disponibilità legale in Italia (dati JustWatch)."""

from __future__ import annotations

import datetime as dt

import httpx

from .config import Config
from .db import Archivio
from .rete import ErroreServizio, Limitatore

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


def riassunto_serie(f: dict) -> dict:
    return {
        "id": f["id"],
        "titolo": f.get("name") or f.get("original_name") or "",
        "titolo_originale": f.get("original_name") or "",
        "anno": anno(f.get("first_air_date")),
        "poster": url_immagine(f.get("poster_path")),
        "trama": f.get("overview") or "",
        "popolarita": f.get("popularity") or 0,
        "serie": True,
    }


# Generi TMDB adatti ai bambini: animazione e famiglia
GENERI_KIDS = {16, 10751}
# Generi che non vanno mai in Kids, anche se animati (horror, thriller, crime, guerra)
GENERI_NO_KIDS = {27, 53, 80, 10752}


# Generi mostrati in Kids (gli altri restano fuori)
GENERI_PER_KIDS = (16, 10751, 12, 35, 14, 10402)
# Generi di partenza dei "consigliati" quando in Jellyfin non hai ancora visto nulla
GENERI_INIZIALI = {"movie": (35, 28), "tv": (18, 80)}
# In TMDB alcuni generi delle serie hanno un id diverso da quello dei film
EQUIVALENTI_TV = {28: 10759, 12: 10759, 14: 10765, 878: 10765, 10752: 10768}


def adatto_ai_bambini(f: dict) -> bool:
    generi = set(f.get("genre_ids") or [])
    return not f.get("adult") and bool(generi & GENERI_KIDS) and not generi & GENERI_NO_KIDS


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


def riassunto(f: dict, tipo: str) -> dict:
    return riassunto_serie(f) if tipo == "tv" else riassunto_film(f)


def tipo_tmdb(mondo: str) -> str:
    return "tv" if mondo == "serie" else "movie"


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
                "Aggiungila dalla pagina Impostazioni di mediabox (/impostazioni), voce \"La chiave di TMDB\".",
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
            url = f"{self.config.tmdb_api}{percorso}"
            r = await self.client.get(url, params={**parametri, **auth}, headers=intestazioni)
        except httpx.HTTPError as exc:
            raise ErroreServizio(
                "Non riesco a contattare TMDB.",
                "Controlla che il PC di mediabox sia collegato a Internet e riprova tra poco.",
            ) from exc
        if r.status_code == 401:
            if self.config.tmdb_chiave_pubblica:
                raise ErroreServizio(
                    "La chiave TMDB inclusa in mediabox non funziona più.",
                    "Aggiorna mediabox (mediabox update) oppure metti una chiave tua dalla pagina Impostazioni "
                    "(/impostazioni): è gratuita su themoviedb.org → Impostazioni → API.",
                )
            raise ErroreServizio(
                "TMDB rifiuta la chiave API.",
                "Controlla la chiave nella pagina Impostazioni di mediabox (/impostazioni): "
                "deve essere tutta, senza spazi (themoviedb.org → Impostazioni → API).",
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

    async def tendenze(self) -> list[dict]:
        """Film di tendenza questa settimana."""
        dati = await self._get("/trending/movie/week", {"language": self.config.lingua_tmdb}, 6 * 3600)
        return [riassunto_film(f) for f in dati.get("results", []) if f.get("poster_path")][:18]

    async def al_cinema(self) -> list[dict]:
        """Film usciti da poco nei cinema italiani."""
        dati = await self._get(
            "/movie/now_playing",
            {"language": self.config.lingua_tmdb, "region": self.config.paese},
            6 * 3600,
        )
        film = [riassunto_film(f) for f in dati.get("results", []) if f.get("poster_path")]
        return sorted(film, key=lambda f: f["popolarita"], reverse=True)[:18]

    # --- serie TV ------------------------------------------------------------------
    async def tendenze_serie(self) -> list[dict]:
        dati = await self._get("/trending/tv/week", {"language": self.config.lingua_tmdb}, 6 * 3600)
        return [riassunto_serie(f) for f in dati.get("results", []) if f.get("poster_path")][:18]

    async def cerca_serie(self, testo: str) -> list[dict]:
        dati = await self._get(
            "/search/tv", {"query": testo, "language": self.config.lingua_tmdb, "include_adult": "false"}, UN_GIORNO
        )
        return [riassunto_serie(f) for f in dati.get("results", [])][:20]

    # --- Kids ------------------------------------------------------------------------
    async def _scopri_kids(self, genere: int) -> list[dict]:
        dati = await self._get(
            "/discover/movie",
            {
                "language": self.config.lingua_tmdb,
                "region": self.config.paese,
                "with_genres": str(genere),
                "without_genres": ",".join(str(g) for g in sorted(GENERI_NO_KIDS)),
                "include_adult": "false",
                # classificazione USA fino a PG: la più completa su TMDB
                "certification_country": "US",
                "certification.lte": "PG",
                "sort_by": "popularity.desc",
            },
            12 * 3600,
        )
        return [riassunto_film(f) for f in dati.get("results", []) if f.get("poster_path") and adatto_ai_bambini(f)][:18]

    async def kids_cartoni(self) -> list[dict]:
        return await self._scopri_kids(16)

    async def kids_famiglia(self) -> list[dict]:
        return await self._scopri_kids(10751)

    async def cerca_kids(self, testo: str) -> list[dict]:
        dati = await self._get(
            "/search/movie",
            {"query": testo, "language": self.config.lingua_tmdb, "include_adult": "false", "region": self.config.paese},
            UN_GIORNO,
        )
        return [riassunto_film(f) for f in dati.get("results", []) if adatto_ai_bambini(f)][:20]

    # --- home in stile Netflix --------------------------------------------------------
    async def generi(self, tipo: str = "movie", lingua: str | None = None) -> list[dict]:
        """Elenco dei generi di TMDB: [{"id", "nome"}]. Cambia di rado: cache di una settimana."""
        dati = await self._get(f"/genre/{tipo}/list", {"language": lingua or self.config.lingua_tmdb}, 7 * UN_GIORNO)
        return [{"id": g["id"], "nome": (g.get("name") or "").strip()} for g in dati.get("genres", []) if g.get("name")]

    async def generi_kids(self) -> list[dict]:
        nomi = {g["id"]: g["nome"] for g in await self.generi("movie")}
        return [{"id": g, "nome": nomi[g]} for g in GENERI_PER_KIDS if g in nomi]

    async def scopri(self, tipo: str, genere: int, pagina: int = 1, kids: bool = False) -> list[dict]:
        """I titoli più popolari di un genere."""
        parametri = {
            "language": self.config.lingua_tmdb,
            "with_genres": str(genere),
            "include_adult": "false",
            "sort_by": "popularity.desc",
            "page": str(max(1, min(pagina, 20))),
            # niente titoli sconosciuti con un voto a caso
            "vote_count.gte": "50",
        }
        if kids:
            parametri |= {
                "without_genres": ",".join(str(g) for g in sorted(GENERI_NO_KIDS)),
                "certification_country": "US",
                "certification.lte": "PG",
            }
        dati = await self._get(f"/discover/{tipo}", parametri, 12 * 3600)
        risultati = [f for f in dati.get("results", []) if f.get("poster_path")]
        if kids:
            risultati = [f for f in risultati if adatto_ai_bambini(f)]
        return [riassunto(f, tipo) for f in risultati]

    async def top_settimana(self, tipo: str) -> list[dict]:
        dati = await self._get(f"/trending/{tipo}/week", {"language": self.config.lingua_tmdb}, 6 * 3600)
        return [riassunto(f, tipo) for f in dati.get("results", []) if f.get("poster_path")][:10]

    async def appena_usciti(self, tipo: str, oggi: dt.date | None = None) -> list[dict]:
        """I 10 titoli usciti negli ultimi due mesi più visti (film nei cinema italiani, serie nuove)."""
        oggi = oggi or dt.date.today()
        dal, al = (oggi - dt.timedelta(days=60)).isoformat(), oggi.isoformat()
        if tipo == "tv":
            parametri = {"first_air_date.gte": dal, "first_air_date.lte": al}
        else:
            parametri = {"primary_release_date.gte": dal, "primary_release_date.lte": al, "region": self.config.paese}
        dati = await self._get(
            f"/discover/{tipo}",
            {**parametri, "language": self.config.lingua_tmdb, "include_adult": "false", "sort_by": "popularity.desc"},
            6 * 3600,
        )
        return [riassunto(f, tipo) for f in dati.get("results", []) if f.get("poster_path")][:10]

    async def id_genere(self, tipo: str, nomi: list[str]) -> dict[str, int]:
        """Traduce i nomi dei generi di Jellyfin (italiano o inglese) negli id di TMDB."""
        tabella: dict[str, int] = {}
        for lingua in ("en-US", self.config.lingua_tmdb):
            propri = await self.generi(tipo, lingua)
            for g in propri:
                tabella[g["nome"].casefold()] = g["id"]
            if tipo == "tv":  # "Azione" su una serie: lo cerchiamo tra i generi dei film e lo convertiamo
                validi = {g["id"] for g in propri}
                for g in await self.generi("movie", lingua):
                    convertito = EQUIVALENTI_TV.get(g["id"], g["id"])
                    if convertito in validi:
                        tabella.setdefault(g["nome"].casefold(), convertito)
        return {n: tabella[n.casefold()] for n in nomi if n.casefold() in tabella}

    async def provider(self, tmdb_id: int, durata_cache: float = 12 * 3600) -> dict:
        dati = await self._get(f"/movie/{tmdb_id}/watch/providers", {}, durata_cache)
        return interpreta_provider(dati, self.config.paese)
