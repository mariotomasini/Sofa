"""Test della libreria della pagina Spazio (elenco ed eliminazione a mano)."""

from pathlib import Path

from app.libreria import (
    dentro_una_libreria,
    estranei,
    puntate_per_stagione,
    riassumi_film,
    riassumi_serie,
    torrent_da_togliere,
)


def test_solo_film_con_file_dal_piu_pesante():
    film = [
        {"id": 1, "title": "Piccolo", "year": 2001, "hasFile": True, "sizeOnDisk": 100, "path": "/a"},
        {"id": 2, "title": "Voluto", "year": 2020, "hasFile": False, "sizeOnDisk": 0},
        {"id": 3, "title": "Grande", "year": 1999, "hasFile": True, "sizeOnDisk": 900,
         "images": [{"coverType": "poster", "remoteUrl": "http://p"}]},
    ]
    r = riassumi_film(film)
    assert [f["titolo"] for f in r] == ["Grande", "Piccolo"]
    assert r[0]["poster"] == "http://p" and r[0]["anno"] == "1999"


def test_serie_con_stagioni_e_anime():
    serie = [
        {"id": 5, "title": "Vuota", "statistics": {"episodeFileCount": 0}},
        {"id": 6, "title": "Naruto", "seriesType": "anime", "monitored": True,
         "statistics": {"episodeFileCount": 3, "sizeOnDisk": 300},
         "seasons": [{"seasonNumber": 2, "statistics": {"episodeFileCount": 1, "sizeOnDisk": 100}},
                     {"seasonNumber": 1, "statistics": {"episodeFileCount": 2, "sizeOnDisk": 200}},
                     {"seasonNumber": 3, "statistics": {"episodeFileCount": 0}}]},
    ]
    r = riassumi_serie(serie)
    assert len(r) == 1 and r[0]["anime"] and r[0]["seguita"]
    assert [s["numero"] for s in r[0]["stagioni"]] == [1, 2]


def test_puntate_per_stagione_anche_file_doppi():
    episodi = [
        {"id": 10, "episodeFileId": 100, "episodeNumber": 2, "title": "B"},
        {"id": 11, "episodeFileId": 101, "episodeNumber": 3, "title": "C"},
        {"id": 12, "episodeFileId": 101, "episodeNumber": 4, "title": "D"},
        {"id": 13, "episodeFileId": 0, "episodeNumber": 5},
    ]
    file = [{"id": 101, "seasonNumber": 1, "size": 20, "relativePath": "S01/e3-4.mkv"},
            {"id": 100, "seasonNumber": 1, "size": 10, "relativePath": "S01/e2.mkv"}]
    (stagione,) = puntate_per_stagione(episodi, file)
    assert stagione["dimensione"] == 30
    assert [p["numero"] for p in stagione["puntate"]] == ["2", "3-4"]
    assert stagione["puntate"][1]["titolo"] == "C / D" and stagione["puntate"][1]["nome_file"] == "e3-4.mkv"


def test_estranei_ignora_noti_e_nascosti(tmp_path: Path):
    film = tmp_path / "film"
    (film / "Noto (2000)").mkdir(parents=True)
    (film / "Copiato a mano").mkdir()
    (film / ".nascosto").mkdir()
    r = estranei([film, tmp_path / "manca"], {str(film / "Noto (2000)") + "/"})
    assert [p.name for p in r] == ["Copiato a mano"]


def test_si_elimina_solo_dentro_le_librerie(tmp_path: Path):
    librerie = [tmp_path / "film", tmp_path / "serie"]
    assert dentro_una_libreria(tmp_path / "film" / "X", librerie)
    assert not dentro_una_libreria(tmp_path / "film", librerie)
    assert not dentro_una_libreria(tmp_path / "film" / "X" / "y.mkv", librerie)
    assert not dentro_una_libreria(tmp_path / "film" / ".." / "musica", librerie)
    assert not dentro_una_libreria(tmp_path / "musica" / "X", librerie)


def test_torrent_da_togliere_lascia_i_pacchetti_con_altre_puntate():
    storia = [
        {"downloadId": "AAA", "episodeId": 1},
        {"downloadId": "BBB", "episodeId": 2},
        {"downloadId": "BBB", "episodeId": 3},
        {"downloadId": "", "episodeId": 4},
    ]
    assert torrent_da_togliere(storia, None) == {"aaa", "bbb"}
    assert torrent_da_togliere(storia, {1, 2}) == {"aaa"}
    assert torrent_da_togliere(storia, {2, 3}) == {"bbb"}
