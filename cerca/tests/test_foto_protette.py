"""Le foto e i video di Immich non si cancellano mai: né la pulizia né la pagina Spazio."""

from pathlib import Path

import pytest

from app import foto_protette
from app.foto_protette import FotoProtette, controlla, tocca_le_foto
from app.pulizia import _cancella


@pytest.fixture
def dati(tmp_path, monkeypatch):
    foto = tmp_path / "foto"
    video = foto / "library" / "anna" / "2026" / "2026-11-27"
    video.mkdir(parents=True)
    (video / "IMG_0001.MOV").write_bytes(b"video dall'iPhone")
    (video / "IMG_0002.HEIC").write_bytes(b"foto")
    (tmp_path / "media" / "film").mkdir(parents=True)
    monkeypatch.setattr(foto_protette, "CARTELLE_FOTO", (foto, tmp_path / "copia"))
    return tmp_path


def test_dentro_le_foto_e_protetto(dati):
    cartelle = (dati / "foto", dati / "copia")
    assert tocca_le_foto(dati / "foto", cartelle)
    assert tocca_le_foto(dati / "foto/library/anna/2026/2026-11-27/IMG_0001.MOV", cartelle)
    assert tocca_le_foto(dati / "media/film/../../foto/library", cartelle)  # niente scorciatoie con ".."
    assert tocca_le_foto(dati, cartelle)  # chi cancella tutta la cartella dei dati cancellerebbe le foto
    assert tocca_le_foto(dati / "copia/library", cartelle)
    assert not tocca_le_foto(dati / "media/film/Un film (2020)", cartelle)
    assert not tocca_le_foto(dati / "fotografie", cartelle)  # solo la cartella vera, non i nomi simili


def test_un_collegamento_alle_foto_e_protetto(dati):
    collegamento = dati / "media" / "film" / "scorciatoia"
    collegamento.symlink_to(dati / "foto" / "library")
    assert tocca_le_foto(collegamento, (dati / "foto",))


def test_la_pulizia_non_cancella_i_video_dell_iphone(dati, monkeypatch):
    monkeypatch.setattr(foto_protette, "CARTELLE_FOTO", (dati / "foto",))
    video = dati / "foto/library/anna/2026/2026-11-27/IMG_0001.MOV"
    with pytest.raises(FotoProtette):
        _cancella(video)
    with pytest.raises(OSError):  # la pulizia salta gli errori del disco: anche questo
        _cancella(dati / "foto")
    assert video.read_bytes() == b"video dall'iPhone"
    film = dati / "media/film/vecchio.mkv"
    film.write_bytes(b"x")
    _cancella(film)
    assert not film.exists()


def test_controlla_di_base_sulla_cartella_di_mediabox():
    assert tocca_le_foto(Path("/data/foto/library/mario/2026/x.jpg"))
    assert tocca_le_foto(Path("/data"))
    assert tocca_le_foto(Path("/data/foto-ripristinate/library/anna/x.heic"))  # anche quelle riprese dal cloud
    assert not tocca_le_foto(Path("/data/media/serie/Una serie"))
    with pytest.raises(FotoProtette):
        controlla("/data/foto/encoded-video")
