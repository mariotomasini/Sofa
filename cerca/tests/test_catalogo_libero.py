"""Test del catalogo senza chiavi (Radarr, Wikidata, TVmaze)."""

import asyncio
import datetime as dt
from urllib.parse import parse_qs, urlparse

import httpx

from app import catalogo_libero
from app.catalogo_libero import CatalogoLibero, riassunto_radarr, riassunto_tvmaze
from app.config import Config

# nei test niente attese tra una richiesta e l'altra
catalogo_libero._wikidata.intervallo = 0
catalogo_libero._tvmaze.intervallo = 0
catalogo_libero._justwatch.intervallo = 0


class ArchivioFinto:
    def cache_leggi(self, chiave):
        return None

    def cache_scrivi(self, chiave, valore, durata):
        pass


def film_radarr(tmdb, titolo, generi=("Drama",), popolarita=10, al_cinema="2026-11-01", **altri):
    return {"tmdbId": tmdb, "title": titolo, "originalTitle": titolo + " (orig)", "year": 2026,
            "overview": "Trama in italiano.", "genres": list(generi), "popularity": popolarita,
            "inCinemas": al_cinema, "runtime": 100, "imdbId": f"tt{tmdb}",
            "ratings": {"tmdb": {"value": 7.46}},
            "images": [{"coverType": "poster", "remoteUrl": f"https://image.tmdb.org/t/p/original/{tmdb}.jpg"},
                       {"coverType": "fanart", "remoteUrl": f"https://image.tmdb.org/t/p/original/s{tmdb}.jpg"}],
            **altri}


SCOPRI = [
    film_radarr(1, "Tendenza poco vista", popolarita=5, isTrending=True),
    film_radarr(2, "Tendenza molto vista", ("Horror",), popolarita=90, isTrending=True),
    film_radarr(3, "Popolare vecchio", ("Horror",), popolarita=50, al_cinema="2020-01-01", isPopular=True),
]
CARTONI = {
    16: film_radarr(16, "Cartone", ("Animation", "Family")),
    27: film_radarr(27, "Horror animato", ("Animation", "Horror")),
    603: film_radarr(603, "Matrix", ("Action", "Science Fiction")),
}


def show(i, nome, peso, generi=("Drama",), canale="Netflix", premiere="2026-10-01"):
    return {"id": i, "name": nome, "weight": peso, "genres": list(generi), "premiered": premiere,
            "summary": "<p>Una <b>serie</b> &amp; altro.</p>", "image": {"medium": f"https://tvmaze/{i}.jpg"},
            "webChannel": {"name": canale} if canale else None}


def crea(wikidata_rotto=False, richieste=None):
    def gestore(r: httpx.Request) -> httpx.Response:
        url = urlparse(str(r.url))
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if richieste is not None:
            richieste.append((url.netloc, url.path, q))
        if url.netloc == "radarr:7878":
            assert r.headers["X-Api-Key"] == "chiave-radarr"
            if url.path.endswith("/movie/lookup"):
                return httpx.Response(200, json=[film_radarr(603, "Matrix", ("Action", "Science Fiction"))])
            if url.path.endswith("/movie/lookup/tmdb"):
                tmdb = int(q["tmdbId"])
                return httpx.Response(200, json=CARTONI.get(tmdb) or film_radarr(tmdb, f"Film {tmdb}"))
            if url.path.endswith("/importlist/movie"):
                return httpx.Response(200, json=SCOPRI)
        if url.netloc == "query.wikidata.org":
            if wikidata_rotto:
                return httpx.Response(429)
            if "P750" in q["query"]:
                return httpx.Response(200, json={"results": {"bindings": [
                    {"tmdb": {"value": "900"}, "d": {"value": "2026-10-01"}}, {"tmdb": {"value": "901"}}]}})
            return httpx.Response(200, json={"results": {"bindings": [{"tmdb": {"value": "16"}}, {"tmdb": {"value": "27"}},
                                                                     {"tmdb": {"value": "non-numero"}}]}})
        if url.netloc == "api.tvmaze.com":
            if url.path == "/search/shows":
                return httpx.Response(200, json=[{"show": show(70, "Dark", 98)}])
            if url.path == "/schedule/web":
                return httpx.Response(200, json=[
                    {"_embedded": {"show": show(1, "Nuova su Netflix", 80, premiere="2026-11-10")}},
                    {"_embedded": {"show": show(2, "Commedia su Prime", 95, ("Comedy",), "Prime Video", "2019-01-01")}},
                    {"_embedded": {"show": {"id": 3, "name": "Senza immagine", "weight": 99}}},
                ])
            if url.path == "/schedule":
                assert q["country"] == "IT"
                return httpx.Response(200, json=[{"show": show(4, "In TV in Italia", 60, ("Crime",), None, "2015-03-01")}])
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(gestore))
    config = Config(radarr_url="http://radarr:7878/radarr", radarr_chiave="chiave-radarr", tmdb_chiave="")
    return CatalogoLibero(config, ArchivioFinto(), client)


def test_riassunti():
    f = riassunto_radarr(film_radarr(603, "Matrix", ("Action", "Science Fiction")))
    assert f["id"] == 603 and f["titolo"] == "Matrix" and f["voto"] == 7.5
    assert f["poster"] == "https://image.tmdb.org/t/p/w342/603.jpg"
    assert f["genre_ids"] == [28, 878]
    s = riassunto_tvmaze(show(70, "Dark", 98))
    assert s["serie"] and s["trama"] == "Una serie & altro." and s["canale"] == "Netflix"


def test_film_da_radarr():
    async def prova():
        c = crea()
        return await c.cerca("matrix"), await c.dettagli(603), await c.tendenze(), await c.al_cinema(dt.date(2026, 11, 19))

    cerca, dettagli, tendenze, cinema = asyncio.run(prova())
    assert cerca[0]["titolo"] == "Matrix"
    assert dettagli["generi"] == ["Azione", "Fantascienza"] and dettagli["sfondo"].endswith("/w1280/s603.jpg")
    assert dettagli["durata"] == 100 and dettagli["imdb_id"] == "tt603"
    assert [f["id"] for f in tendenze] == [2, 1]  # solo quelli di tendenza, dal più visto
    assert [f["id"] for f in cinema] == [2, 1]  # usciti negli ultimi 90 giorni


def test_generi_e_kids_da_wikidata():
    richieste = []

    async def prova():
        c = crea(richieste=richieste)
        return await c.scopri("movie", 16), await c.kids_cartoni()

    tutti, kids = asyncio.run(prova())
    assert [f["id"] for f in tutti] == [16, 27]
    assert [f["id"] for f in kids] == [16]  # l'horror animato non è per bambini
    query = [q["query"] for n, _, q in richieste if n == "query.wikidata.org"]
    assert "wd:Q202866" in query[0] and "FILTER NOT EXISTS" in query[1]


def test_senza_wikidata_i_generi_vengono_dai_film_del_momento():
    horror = asyncio.run(crea(wikidata_rotto=True).scopri("movie", 27))
    assert [f["id"] for f in horror] == [2, 3]


def test_piattaforme():
    async def prova():
        c = crea()
        return (await c.piattaforme("movie"), await c.su_piattaforma("movie", 8),
                await c.su_piattaforma("tv", 119), await c.su_piattaforma("movie", 4242))

    film, netflix, prime, sconosciuta = asyncio.run(prova())
    assert film[0] == {"id": 8, "nome": "Netflix", "logo": "", "titolo_riga": "Di tendenza su Netflix"}
    assert [f["id"] for f in netflix] == [900, 901]
    assert [s["titolo"] for s in prime] == ["Commedia su Prime"]
    assert sconosciuta == []


def test_serie_da_tvmaze():
    async def prova():
        c = crea()
        return (await c.cerca_serie("dark"), await c.top_settimana("tv"), await c.scopri("tv", 35),
                await c.appena_usciti("tv", dt.date(2026, 11, 19)))

    cerca, top, commedie, nuove = asyncio.run(prova())
    assert cerca[0]["titolo"] == "Dark"
    assert [s["titolo"] for s in top] == ["Commedia su Prime", "Nuova su Netflix", "In TV in Italia"]
    assert [s["titolo"] for s in commedie] == ["Commedia su Prime"]
    assert [s["titolo"] for s in nuove] == ["Nuova su Netflix"]


def test_dove_vederlo_rimanda_a_justwatch():
    d = asyncio.run(crea().provider(603))
    assert d["senza_chiave"] and d["gruppi"] == [] and d["link"] == "https://www.justwatch.com/it/cerca?q=Matrix"


def test_generi_di_jellyfin():
    c = crea()
    assert asyncio.run(c.id_genere("movie", ["Horror", "Science Fiction", "Commedia", "Boh"])) == {
        "Horror": 27, "Science Fiction": 878, "Commedia": 35}
    assert asyncio.run(c.id_genere("tv", ["Drama", "Fantasy"])) == {"Drama": 18, "Fantasy": 10765}


class ArchivioInMemoria(ArchivioFinto):
    def __init__(self):
        self.dati = {}

    def cache_leggi(self, chiave):
        return self.dati.get(chiave)

    def cache_scrivi(self, chiave, valore, durata):
        self.dati[chiave] = valore


def test_home_usa_solo_schede_pronte_e_prepara_le_altre_in_sottofondo(monkeypatch):
    """Nella home niente raffiche di richieste a Radarr: le schede mancanti arrivano una alla volta."""
    monkeypatch.setattr(catalogo_libero, "PAUSA_TRA_SCHEDE", 0)
    richieste = []
    c = crea(richieste=richieste)
    c.archivio = ArchivioInMemoria()

    def schede():
        return sum(1 for _, p, _ in richieste if p.endswith("/movie/lookup/tmdb"))

    async def prova():
        segno = catalogo_libero.solo_pronti.set(True)
        try:
            prima = await c.su_piattaforma("movie", 8)
            assert prima == [] and schede() == 0  # la home non aspetta nessuna scheda
            await c._preparatore  # le schede si preparano in sottofondo
            assert schede() == 2
            dopo = await c.su_piattaforma("movie", 8)
            assert [f["id"] for f in dopo] == [900, 901] and schede() == 2
            # un genere senza abbastanza schede pronte mostra intanto i film del momento
            horror = await c.scopri("movie", 27)
            assert [f["id"] for f in horror] == [2, 3]
        finally:
            catalogo_libero.solo_pronti.reset(segno)

    asyncio.run(prova())


def test_schede_snellite_danno_gli_stessi_riassunti():
    grande = film_radarr(603, "Matrix", ("Action", "Science Fiction"), credits=[{"nome": "x"}] * 500,
                         isTrending=True)
    grande["images"].append({"coverType": "banner", "remoteUrl": "https://banner"})
    piccolo = catalogo_libero.snellisci_film(grande)
    assert "credits" not in piccolo and len(piccolo["images"]) == 2
    assert riassunto_radarr(piccolo) == riassunto_radarr(grande)
    palinsesto = [{"_embedded": {"show": {**show(1, "Dark", 9), "_links": {"x": 1}}}, "airtime": "21:00"}]
    voce = catalogo_libero.snellisci_tvmaze(palinsesto)[0]
    assert riassunto_tvmaze(voce["show"]) == riassunto_tvmaze(show(1, "Dark", 9))


def test_stessa_richiesta_da_piu_righe_parte_una_volta_sola():
    richieste = []

    async def prova():
        c = crea(richieste=richieste)
        await asyncio.gather(c.tendenze(), c.al_cinema(), c.top_settimana("movie"), c.scopri("movie", 27))
        await c.tendenze()  # ora arriva dalla memoria

    asyncio.run(prova())
    assert sum(1 for _, p, _ in richieste if p.endswith("/importlist/movie")) == 1



# --- JustWatch: le tendenze delle piattaforme -------------------------------------------------

def nodo_jw(tmdb, titolo, tipo="MOVIE", generi=("drm",), durata=110, imdb=True, poster=True, anno=2026):
    return {"node": {"objectType": tipo, "content": {
        "title": titolo, "originalTitle": titolo + " (orig)", "originalReleaseYear": anno,
        "posterUrl": f"/poster/{tmdb}/{{profile}}/x.{{format}}" if poster else None,
        "shortDescription": "Trama.", "runtime": durata, "genres": [{"shortName": g} for g in generi],
        "scoring": {"imdbScore": 7.2}, "externalIds": {"tmdbId": str(tmdb), "imdbId": f"tt{tmdb}" if imdb else None},
    }}}


def crea_con_justwatch(richieste):
    import json as _json

    def gestore(r: httpx.Request) -> httpx.Response:
        if r.url.host != "apis.justwatch.com":
            return httpx.Response(404)
        dati = _json.loads(r.content)
        richieste.append(dati["variables"])
        filtro = dati["variables"]["filtro"]
        if filtro["objectTypes"] == ["SHOW"]:
            nodi = [nodo_jw(50, "Serie italiana", "SHOW")]
        elif filtro.get("genres") == ["ani"]:
            nodi = [nodo_jw(16, "Cartone", generi=("ani", "fml")), nodo_jw(17, "Cartone horror", generi=("ani", "hrr"))]
        else:
            nodi = [nodo_jw(1, "Primo"), nodo_jw(2, "Extra", imdb=False), nodo_jw(3, "Corto", durata=20),
                    nodo_jw(4, "Senza locandina", poster=False), nodo_jw(1, "Primo"), nodo_jw(5, "Secondo")]
        return httpx.Response(200, json={"data": {"popularTitles": {"edges": nodi}}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(gestore))
    config = Config(radarr_url="http://radarr:7878/radarr", radarr_chiave="r", tmdb_chiave="")
    return CatalogoLibero(config, ArchivioFinto(), client)


def test_justwatch_tendenze_sensate_in_italiano():
    richieste = []

    async def prova():
        c = crea_con_justwatch(richieste)
        return (await c.top_settimana("movie"), await c.top_settimana("tv"), await c.su_piattaforma("movie", 8),
                await c.scopri("movie", 16), await c.scopri("movie", 16, kids=True), await c.appena_usciti("movie"))

    top, serie, netflix, cartoni, kids, nuovi = asyncio.run(prova())
    # niente extra senza IMDb, corti, titoli senza locandina, doppioni
    assert [f["id"] for f in top] == [1, 5]
    assert top[0]["poster"] == "https://images.justwatch.com/poster/1/s332/x.webp"
    assert serie[0]["serie"] and serie[0]["titolo"] == "Serie italiana"
    assert serie[0]["titolo_originale"] == "Serie italiana (orig)"
    assert [f["id"] for f in netflix] == [1, 5] and [f["id"] for f in cartoni] == [16, 17]
    assert [f["id"] for f in kids] == [16]  # l'horror animato non è per bambini
    assert [f["id"] for f in nuovi] == [1, 5]
    filtri = [r["filtro"] for r in richieste]
    assert all(f["monetizationTypes"] == ["FLATRATE", "FREE", "ADS"] for f in filtri)  # solo in streaming
    assert {"packages": ["nfx"]}.items() <= next(f for f in filtri if f["packages"] == ["nfx"]).items()
    assert any(f.get("excludeGenres") == catalogo_libero.justwatch.NO_KIDS for f in filtri)
    assert any("releaseYear" in f for f in filtri)
    assert all(r["paese"] == "IT" and r["lingua"] == "it" for r in richieste)


def test_senza_justwatch_restano_le_altre_fonti():
    # crea() risponde 404 a JustWatch: la Top 10 arriva dai film del momento di Radarr
    top = asyncio.run(crea().top_settimana("movie"))
    assert [f["id"] for f in top] == [2, 1]



def test_serie_senza_reality_e_kids_per_bambini():
    from app import justwatch

    c = justwatch.corpo(serie=True, generi=["fml"], escludi=justwatch.NO_KIDS)
    assert c["variables"]["filtro"]["excludeGenres"] == [*justwatch.NO_KIDS, "rly"]
    assert justwatch.corpo(serie=True)["variables"]["filtro"]["excludeGenres"] == ["rly"]
    assert "excludeGenres" not in justwatch.corpo(serie=False)["variables"]["filtro"]


def test_vista_di_una_piattaforma_in_una_sola_richiesta():
    import json as _json

    richieste = []

    def gestore(r: httpx.Request) -> httpx.Response:
        dati = _json.loads(r.content)
        richieste.append(dati)
        risposta = {}
        import re as _re

        for nome in _re.findall(r"(\w+): popularTitles", dati["query"]):
            if nome == "top":
                nodi = [nodo_jw(1, "Primo"), nodo_jw(2, "Secondo")]
                nodi[0]["node"]["content"]["backdrops"] = [{"backdropUrl": "/backdrop/1/{profile}/x.{format}"}]
            elif nome == "nuovi":
                nodi = [nodo_jw(2, "Secondo"), nodo_jw(3, "Terzo")]
            elif nome == "g_drm":
                nodi = [nodo_jw(i, f"Dramma {i}") for i in (1, 3, 4, 5)]
            else:
                nodi = [nodo_jw(9, "Solo uno")]  # troppo pochi: la riga non si mostra
            risposta[nome] = {"edges": nodi}
        return httpx.Response(200, json={"data": risposta})

    client = httpx.AsyncClient(transport=httpx.MockTransport(gestore))
    c = CatalogoLibero(Config(radarr_url="http://radarr", radarr_chiave="r", tmdb_chiave=""), ArchivioFinto(), client)
    v = asyncio.run(c.vista_piattaforma("movie", 8, oggi=dt.date(2026, 11, 20)))
    assert len(richieste) == 1  # tutte le righe insieme
    filtri = richieste[0]["variables"]
    assert all(filtri[k]["packages"] == ["nfx"] for k in filtri if k.startswith("f"))
    assert v["piattaforma"] == {"id": 8, "nome": "Netflix"}
    assert v["in_evidenza"]["id"] == 1 and v["in_evidenza"]["top10"] == 1
    assert v["in_evidenza"]["sfondo"] == "https://images.justwatch.com/backdrop/1/s1440/x.webp"
    assert [r["titolo"] for r in v["righe"]] == ["Top 10 dei film su Netflix oggi", "Novità su Netflix", "Film drammatici"]
    dramma = {f["id"]: f for f in v["righe"][2]["elementi"]}
    assert dramma[1]["top10"] == 1 and "nuovo" not in dramma[1]  # in classifica
    assert dramma[3]["nuovo"] and "top10" not in dramma[3]  # appena arrivato
    serie = asyncio.run(c.vista_piattaforma("tv", 8, oggi=dt.date(2026, 11, 20)))
    assert serie["righe"][0]["titolo"] == "Top 10 delle serie su Netflix oggi"
