"""Test della scelta della versione migliore su Internet Archive."""

import json
from pathlib import Path

import pytest

from app.scelta_archive import (
    chiave_ordinamento,
    classifica_item,
    durata_in_secondi,
    e_bassa_qualita,
    file_video,
    migliore_file,
    migliore_tra_item,
)

FIXTURE = Path(__file__).parent / "fixtures"


def carica(nome: str) -> dict:
    return json.loads((FIXTURE / f"{nome}.json").read_text())


def test_item_1080p_sceglie_l_originale_mkv_in_alta_definizione():
    migliore = migliore_file(carica("Night.Of.The.Living.Dead_1080p"))
    assert migliore.nome == "NightOfTheLivingDead_1080p.mkv"
    assert migliore.altezza == 1080
    assert migliore.originale


def test_a_parita_di_risoluzione_l_originale_batte_il_derivato():
    migliore = migliore_file(carica("NightOfTheLivingDead-MPEG"))
    # mp4 derivato e mpg originale sono entrambi 480: vince l'originale
    assert migliore.nome == "NightOfTheLivingDead.mpg"


def test_scarta_512kb_se_esiste_un_alternativa():
    nomi = [f.nome for f in file_video(carica("NightOfTheLivingDead-MPEG"))]
    assert "NightOfTheLivingDead_512kb.mp4" in nomi
    assert migliore_file(carica("NightOfTheLivingDead-MPEG")).nome != "NightOfTheLivingDead_512kb.mp4"


def test_usa_512kb_se_non_c_e_altro():
    migliore = migliore_file(carica("sintetico_solo_512kb"))
    assert migliore.nome == "film_512kb.mp4"


def test_originale_senza_risoluzione_la_eredita_dal_derivato():
    dati = carica("sintetico_originale_senza_risoluzione")
    per_nome = {f.nome: f for f in file_video(dati)}
    assert per_nome["film.avi"].altezza == 576
    # L'originale (576 ereditato) batte il derivato mp4 (576) perché originale
    assert migliore_file(dati, durata_minima=3000).nome == "film.avi"


def test_scarta_campioni_e_immagini():
    nomi = [f.nome for f in file_video(carica("sintetico_originale_senza_risoluzione"))]
    assert "film_sample.mkv" not in nomi
    assert "__ia_thumb.jpg" not in nomi


def test_dvd_con_iso_sceglie_il_film_intero_e_non_i_pezzi():
    migliore = migliore_file(carica("night_of_the_living_dead_dvd"))
    assert migliore.nome == "Night.mp4"


def test_durata_minima_scarta_il_trailer():
    assert migliore_file(carica("NightOfTheLivingDeadTrailer"), durata_minima=60 * 60) is None


def test_confronto_tra_item_prende_il_migliore():
    elenco = [
        carica(n)
        for n in (
            "NightOfTheLivingDead-MPEG",
            "Night_of_the_Living_Dead_AVI",
            "Night.Of.The.Living.Dead_1080p",
            "NightOfTheLivingDead720p1968",
        )
    ]
    migliore = migliore_tra_item(elenco, durata_minima=60 * 60)
    assert migliore.identificativo == "Night.Of.The.Living.Dead_1080p"
    assert migliore.nome == "NightOfTheLivingDead_1080p.mkv"
    classifica = classifica_item(elenco, durata_minima=60 * 60)
    assert classifica[0].identificativo == "Night.Of.The.Living.Dead_1080p"
    assert [chiave_ordinamento(f) for f in classifica] == sorted(
        (chiave_ordinamento(f) for f in classifica), reverse=True
    )


def test_a_parita_vince_l_item_piu_scaricato():
    a = carica("NightOfTheLivingDead-MPEG")
    b = json.loads(json.dumps(a))
    b["metadata"]["identifier"] = "copia"
    migliore = migliore_tra_item([a, b], popolarita={"copia": 10, "NightOfTheLivingDead-MPEG": 5})
    assert migliore.identificativo == "copia"


def test_url_di_download_e_torrent():
    migliore = migliore_file(carica("Night.Of.The.Living.Dead_1080p"))
    assert migliore.url == (
        "https://archive.org/download/Night.Of.The.Living.Dead_1080p/NightOfTheLivingDead_1080p.mkv"
    )
    assert migliore.url_torrent.endswith("/Night.Of.The.Living.Dead_1080p_archive.torrent")


@pytest.mark.parametrize(
    ("valore", "atteso"),
    [("5752.8", 5752.8), ("1:30:00", 5400.0), ("90:00", 5400.0), (None, None), ("boh", None)],
)
def test_durata(valore, atteso):
    assert durata_in_secondi(valore) == atteso


@pytest.mark.parametrize(
    ("nome", "formato", "atteso"),
    [
        ("film_512kb.mp4", "512Kb MPEG4", True),
        ("film.512kb.mp4", "h.264", True),
        ("film_1512kbps.mp4", "h.264", False),
        ("film.mp4", "h.264", False),
    ],
)
def test_bassa_qualita(nome, formato, atteso):
    assert e_bassa_qualita({"name": nome, "format": formato}) is atteso
