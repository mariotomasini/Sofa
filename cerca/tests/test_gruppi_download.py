"""Pagina Download: una scheda per film o serie, con lo stato in breve e le voci dentro."""

from app.automatici import copertina, gruppo_serie
from app.gruppi_download import raggruppa

ADESSO = 1_000_000.0


def auto(gruppo, titolo, stato, tipo="serie", voce="", percentuale=0, ids=(1,), **altro):
    return {"gruppo": gruppo, "gruppo_titolo": titolo, "tipo": tipo, "stato": stato, "voce": voce,
            "percentuale": percentuale, "ids": list(ids), "poster": f"https://img/{gruppo}.jpg", **altro}


def test_una_scheda_per_serie_con_tutte_le_puntate():
    coda = [
        auto("serie:7", "Dark", "download", voce="St. 1 · Ep. 3", percentuale=40, velocita=1000, rimanente=60),
        auto("serie:7", "Dark", "download", voce="St. 1 · Ep. 4", percentuale=60, velocita=500, rimanente=120),
        auto("serie:7", "Dark", "coda", voce="St. 1 · Ep. 5"),
        auto("serie:7", "Dark", "cerco", voce="5 puntate da trovare", ids=(11, 12, 13, 14, 15)),
        auto("film:3", "Heartland (2026)", "problema", tipo="film", voce="Film"),
        auto("film:4", "Ballerina (2025)", "download", tipo="film", voce="Film", percentuale=45.4),
    ]
    attesa = [auto("film:9", "Dune (2021)", "pronto", tipo="film", voce="Film")]
    cerca = [
        {"id": 1, "titolo": "Mare fuori · Stagione 1", "serie": 8, "stato": "in_corso", "percentuale": 10,
         "poster": "p.jpg"},
        {"id": 2, "titolo": "Matrix", "anno": "1999", "tmdb_id": 603, "stato": "importato", "aggiornato": ADESSO - 100},
        {"id": 3, "titolo": "Vecchio", "tmdb_id": 1, "stato": "importato", "aggiornato": ADESSO - 10 * 86400},
        {"id": 4, "titolo": "torrent.misterioso", "stato": "da_associare"},
    ]
    gruppi = raggruppa(coda, attesa, cerca, "Italiano", adesso=ADESSO)
    per_chiave = {g["chiave"]: g for g in gruppi}

    # prima i problemi, poi i download; i pronti in fondo; i pronti vecchi spariscono
    assert [g["chiave"] for g in gruppi] == ["film:3", "film:4", "serie:7", "serie:8", "dl:4", "film:9", "tmdb:603"]
    dark = per_chiave["serie:7"]
    assert dark["titolo"] == "Dark" and dark["poster"] == "https://img/serie:7.jpg"
    assert dark["stato_testo"] == "2 in download · 1 in coda · 5 da trovare"
    assert dark["percentuale"] == 50 and dark["velocita"] == 1500 and dark["rimanente"] == 120
    assert dark["quante"] == 8 and [v["voce"] for v in dark["voci"]][:2] == ["St. 1 · Ep. 3", "St. 1 · Ep. 4"]
    assert per_chiave["film:4"]["stato_testo"] == "In download · 45%"
    assert per_chiave["film:3"]["stato_testo"].startswith("C'è un problema")
    assert per_chiave["film:9"]["stato_testo"] == "Aspetto la versione in italiano"
    # i download di Cerca: le puntate vanno nella serie, i film per titolo
    assert per_chiave["serie:8"]["titolo"] == "Mare fuori" and per_chiave["serie:8"]["voci"][0]["origine"] == "cerca"
    matrix = per_chiave["tmdb:603"]
    assert matrix["titolo"] == "Matrix (1999)" and matrix["stato_testo"] == "Pronto in Jellyfin"
    assert per_chiave["tmdb:603"]["percentuale"] == 100
    assert per_chiave["dl:4"]["stato_testo"] == "Dimmi che film è"


def test_copertina_e_gruppo_da_sonarr():
    serie = {"id": 7, "title": "Dark", "images": [
        {"coverType": "banner", "remoteUrl": "https://image.tmdb.org/t/p/original/b.jpg"},
        {"coverType": "poster", "remoteUrl": "https://image.tmdb.org/t/p/original/p.jpg"}]}
    assert copertina(serie) == "https://image.tmdb.org/t/p/w342/p.jpg"
    assert copertina({}) == ""
    assert gruppo_serie(serie, None, "St. 1 · Ep. 1") == {
        "gruppo": "serie:7", "gruppo_titolo": "Dark", "poster": "https://image.tmdb.org/t/p/w342/p.jpg",
        "voce": "St. 1 · Ep. 1"}


def test_download_di_cerca_in_coda_in_qbittorrent():
    from app.gruppi_download import con_coda_qbit, raggruppa

    cerca = [{"id": 1, "stato": "in_corso", "hash": "AB", "titolo": "Artista · Album", "categoria": "musica"},
             {"id": 2, "stato": "in_corso", "hash": "cd", "titolo": "Artista · Brano", "categoria": "musica"}]
    torrent = [{"hash": "ab", "state": "queuedDL"}, {"hash": "cd", "state": "stalledDL", "dlspeed": 0}]
    voci = con_coda_qbit(cerca, torrent, {"ab": 5, "cd": 1})
    assert voci[0]["stato_qbit"] == "coda" and voci[0]["anticipabile"] and "5° posto" in voci[0]["stato_testo"]
    assert "nessuno lo condivide" in voci[1]["stato_testo"] and "stato_qbit" not in voci[1]
    gruppi = {g["titolo"]: g for g in raggruppa([], [], voci)}
    assert gruppi["Artista · Album"]["stato"] == "coda" and gruppi["Artista · Album"]["stato_testo"] == "1 in coda"
    assert gruppi["Artista · Brano"]["stato"] == "download"
