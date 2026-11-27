"""AnimeUnity: ricerca, puntate, link diretti, e la scelta di cosa scaricare (prima l'italiano)."""

import asyncio
import json

import httpx

from app.animeunity import (
    AnimeUnity,
    AnimeUnitySerie,
    candidati_forti,
    con_download_cerca,
    da_scaricare,
    estrai_csrf,
    estrai_download,
    indovina_stagione,
    nome_file,
    normalizza,
    numero_puntata,
    puntata_sonarr,
    scegli_collegamenti,
)
from app.config import Config
from app.db import Archivio

HOME = '<html><head><meta name="csrf-token" content="tok&amp;123"></head></html>'
LETTORE = """<script>window.video = {id: 1}; window.downloadUrl = 'https://au-d1.scws-content.net/download/1/x.mp4?token=abc&expires=9';</script>"""


def voce(id, titolo, anno="2023", puntate=12, dub=0, tipo="TV"):
    return normalize_rec({"id": id, "slug": titolo.lower(), "title_it": titolo, "date": anno,
                          "episodes_count": puntate, "dub": dub, "type": tipo})


def normalize_rec(r):
    return normalizza(r)


def test_estrazioni():
    assert estrai_csrf(HOME) == "tok&123"
    assert estrai_download(LETTORE).startswith("https://au-d1.scws-content.net/download/1/x.mp4?token=abc")
    assert estrai_download("<script></script>") == ""
    assert numero_puntata("12") == 12 and numero_puntata("12.5") is None and numero_puntata("") is None
    assert normalizza({"id": 3, "title": "Frieren (ITA)", "dub": 0})["doppiato"]


def test_nome_file_con_la_lingua():
    e = {"seasonNumber": 1, "episodeNumber": 5}
    assert nome_file("Frieren: Oltre", e) == "Frieren Oltre - S01E05.mp4"
    assert nome_file("Frieren", e, doppiato=True) == "Frieren - S01E05 - ITA.mp4"


def test_collegamenti_automatici_per_stagione_e_lingua():
    voci = [voce(1, "Frieren"), voce(2, "Frieren (ITA)", dub=1), voce(3, "Frieren 2", anno="2026"),
            voce(4, "Frieren Film", tipo="Movie"), voce(5, "Altro anime")]
    assert {v["id"] for v in candidati_forti(voci, ["Frieren"])} == {1, 2, 3}
    scelti = scegli_collegamenti(voci, ["Frieren"], {1: 28, 2: 10}, assoluta=False, italiano=True)
    assert {(v["id"], v["stagione"], v["doppiato"]) for v in scelti} == {(1, 1, False), (2, 1, True), (3, 2, False)}


def test_numerazione_assoluta_per_le_serie_lunghe():
    one_piece = voce(9, "One Piece", anno="1999", puntate=1100)
    assert indovina_stagione(one_piece, [one_piece], {1: 61, 2: 16, 3: 14}, assoluta=True) == 0
    episodi = [{"id": 70, "seasonNumber": 2, "episodeNumber": 1, "absoluteEpisodeNumber": 62}]
    assert puntata_sonarr(episodi, 0, 62)["id"] == 70
    assert puntata_sonarr(episodi, 2, 1)["id"] == 70
    assert puntata_sonarr(episodi, 1, 62) is None


EPISODI = [
    {"id": 1, "seasonNumber": 1, "episodeNumber": 1, "hasFile": True},   # già c'è, in inglese
    {"id": 2, "seasonNumber": 1, "episodeNumber": 2, "hasFile": True},   # già c'è, in italiano
    {"id": 3, "seasonNumber": 1, "episodeNumber": 3, "hasFile": False},
    {"id": 4, "seasonNumber": 1, "episodeNumber": 4, "hasFile": False},  # AnimeUnity non ce l'ha
    {"id": 5, "seasonNumber": 0, "episodeNumber": 1, "hasFile": False},
]
SUB = {"id": 10, "doppiato": False}
DUB = {"id": 11, "doppiato": True}


def test_da_scaricare_preferisce_l_italiano_e_sostituisce():
    fonti = {1: [(SUB, {"id": 101}), (DUB, {"id": 201})], 2: [(DUB, {"id": 202})], 3: [(SUB, {"id": 103})]}
    scelte, mancano = da_scaricare(EPISODI, fonti, file_italiani={2}, fatte={}, in_corso=set(), solo=None,
                                   da=(1, 2), italiano=True)
    # la 1 c'è in inglese ma esiste doppiata: si sostituisce (anche se è prima di "da")
    # la 2 è già in italiano; la 3 arriva sottotitolata; la 4 manca su AnimeUnity
    assert [(e["id"], c["id"]) for e, c, _ in scelte] == [(1, 11), (3, 10)]
    assert [e["id"] for e in mancano] == [4]


def test_da_scaricare_non_ripete():
    fonti = {1: [(DUB, {"id": 201})], 3: [(SUB, {"id": 103})]}
    scelte, mancano = da_scaricare(EPISODI, fonti, file_italiani=set(), fatte={"1": "ita", "3": "sub"},
                                   in_corso=set(), solo=None, da=(1, 1), italiano=True)
    assert scelte == [] and [e["id"] for e in mancano] == [4]
    # scelta a mano: si riscarica comunque, ma non quelle già in download
    scelte, _ = da_scaricare(EPISODI, fonti, file_italiani=set(), fatte={"3": "sub"}, in_corso={1},
                             solo={3}, da=None, italiano=True)
    assert [e["id"] for e, _, _ in scelte] == [3]


def test_stato_delle_puntate_con_i_download_diretti():
    stagioni = [{"numero": 1, "puntate": [{"id": 3, "stato": "libera"}, {"id": 4, "stato": "libera"}]}]
    con_download_cerca(stagioni, {3: {"id": 8, "stato": "in_corso", "scaricati": 50, "totale": 200},
                                  4: {"id": 9, "stato": "errore", "messaggio": "Solo streaming."}})
    p3, p4 = stagioni[0]["puntate"]
    assert (p3["stato"], p3["percentuale"], p3["fonte"]) == ("download", 25.0, "AnimeUnity")
    assert (p4["stato"], p4["messaggio"]) == ("problema", "Solo streaming.")


def sito_finto():
    def gestore(r: httpx.Request) -> httpx.Response:
        assert "Mozilla" in r.headers["User-Agent"]
        if r.url.path == "/":
            return httpx.Response(200, text=HOME)
        if r.url.path == "/livesearch":
            assert r.headers["X-CSRF-TOKEN"] == "tok&123"
            return httpx.Response(200, json={"records": [{"id": 7, "title": "Frieren", "episodes_count": 3}]})
        if r.url.path == "/info_api/7/":
            return httpx.Response(200, json={"episodes_count": 3})
        if r.url.path == "/info_api/7/1":
            assert r.url.params["start_range"] == "1" and r.url.params["end_range"] == "3"
            return httpx.Response(200, json={"episodes": [{"id": 70 + n, "number": str(n)} for n in (1, 2, 3)]})
        if r.url.path == "/embed-url/71":
            return httpx.Response(200, text="https://vixcloud.co/embed/555?token=x")
        if r.url.host == "vixcloud.co":
            return httpx.Response(200, text=LETTORE)
        return httpx.Response(404)

    au = AnimeUnity(Config(animeunity_url="https://animeunity.test"))
    au._client = httpx.AsyncClient(transport=httpx.MockTransport(gestore),
                                   headers={"User-Agent": "Mozilla/5.0 test"})
    return au


def test_il_sito():
    au = sito_finto()

    async def prova():
        assert [v["id"] for v in await au.cerca("Frieren")] == [7]
        assert [p["numero"] for p in await au.puntate(7)] == ["1", "2", "3"]
        url, intestazioni = await au.link_video(71)
        assert url.endswith("expires=9") and intestazioni["Referer"].startswith("https://vixcloud.co/embed/555")

    asyncio.run(prova())


class SerieFinta:
    attivo = True

    def __init__(self):
        self.annullate, self.cercate = [], []

    async def dati(self, serie_id):
        return {"title": "Frieren", "seriesType": "anime", "images": []}

    async def episodi(self, serie_id):
        return [{"id": 30 + n, "seasonNumber": 1, "episodeNumber": n, "hasFile": n == 1,
                 "airDateUtc": "2023-10-01T00:00:00Z", "episodeFileId": 900 if n == 1 else 0} for n in (1, 2, 3, 4)]

    async def file_puntate(self, serie_id):
        return [{"id": 900, "languages": [{"name": "Japanese"}]}]

    async def annulla_puntate(self, ids):
        self.annullate += ids

    async def scarica_puntate(self, ids):
        self.cercate += ids


class DownloadFinto:
    def __init__(self):
        self.avviati = []

    async def da_animeunity(self, **kw):
        self.avviati.append(kw)


def test_serie_collegata_scarica_e_ricorda(tmp_path):
    sito = sito_finto()
    archivio = Archivio(tmp_path / "a.db")
    serie, download = SerieFinta(), DownloadFinto()
    anime = AnimeUnitySerie(sito, archivio, serie, download, Config(lingue_audio=("it", "en")))

    async def puntate_doppiate(anime_id):
        return [{"id": 70 + n, "numero": str(n)} for n in (1, 2, 3)]

    sito.puntate = puntate_doppiate
    anime.collega(5, {"id": 7, "titolo": "Frieren (ITA)", "doppiato": True}, 1)
    anime.segui_da(5, (1, 2))

    async def prova():
        esito = await anime.scarica(5)
        # la 1 c'è in giapponese → sostituita con quella doppiata; 2 e 3 nuove; la 4 non c'è
        assert esito == {"avviate": [31, 32, 33], "mancano": [34]}
        assert download.avviati[0]["nome_file"] == "Frieren - S01E01 - ITA.mp4"
        assert serie.annullate == [31, 32, 33]  # Sonarr non le cerca più come torrent
        assert serie.cercate == [34]  # uscita da giorni e AnimeUnity non ce l'ha: la cerca Sonarr
        # al giro dopo non si riscarica niente
        assert (await anime.scarica(5))["avviate"] == []

    asyncio.run(prova())
    assert json.loads(archivio.stato_leggi("animeunity:5"))["fatte"] == {"31": "ita", "32": "ita", "33": "ita"}
    assert anime.serie_collegate() == [5]


def test_download_diretto_chiede_il_link_a_ogni_partenza(tmp_path):
    from app.download import GestoreDownload

    richieste = []

    def gestore(r: httpx.Request) -> httpx.Response:
        richieste.append(r)
        return httpx.Response(200, content=b"video" * 10)

    config = Config(cartella_download=tmp_path)
    archivio = Archivio(tmp_path / "a.db")
    client = httpx.AsyncClient(transport=httpx.MockTransport(gestore))
    g = GestoreDownload(config, archivio, client, None, None, None, None)
    chiesti = []

    async def risolvi(voce):
        chiesti.append(voce["url"])
        return "https://cdn.test/x.mp4?token=nuovo", {"Referer": "https://vixcloud.co/embed/1"}

    g.risolutori["animeunity"] = risolvi

    async def passa(id_download):
        archivio.aggiorna_download(id_download, stato="importato")

    g._passa_a_radarr = passa

    async def prova():
        id_d = await g.da_animeunity(serie_id=5, titolo_serie="Frieren", poster="", puntata_au=71,
                                     episodio={"id": 31, "seasonNumber": 1, "episodeNumber": 1},
                                     nome_file="Frieren - S01E01 - ITA.mp4", doppiato=True)
        await g._compiti[id_d]
        return id_d

    id_d = asyncio.run(prova())
    voce = archivio.download(id_d)
    assert chiesti == ["animeunity:71"] and richieste[0].headers["Referer"] == "https://vixcloud.co/embed/1"
    assert voce["titolo"] == "Frieren · Stagione 1 · Episodio 1 (ITA)" and voce["stato"] == "importato"
    assert (tmp_path / "cerca" / "animeunity" / "Frieren" / "Frieren - S01E01 - ITA.mp4").read_bytes() == b"video" * 10
    assert archivio.download_puntate(5) == {}  # importato: non più attivo
