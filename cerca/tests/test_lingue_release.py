import pytest

from app.lingue_release import lingue_audio, lingue_sottotitoli, valuta
from app.prowlarr import ordina, riassunto

LINGUE = ("it", "es", "en")


@pytest.mark.parametrize("nome, audio, sub", [
    ("Serie.S01E02.1080p.WEB.ITA.ENG", {"it"}, set()),
    ("Serie.S01E02.1080p.WEB.ENG.ITA.SUB", {"en"}, {"it"}),
    ("Serie S01E02 [Sub ENG-ITA] 1080p", {"en"}, {"en", "it"}),
    ("Serie.S01E02.MULTi.Audio.ITA.SPA.Multi.Subs", {"it", "es"}, set()),
    ("Serie.S01E02.iTALiAN.SUBBED", {"en"}, {"it"}),
])
def test_lingue(nome, audio, sub):
    assert lingue_audio(nome) == audio
    assert lingue_sottotitoli(nome) == sub


def test_prima_l_audio_poi_multi_poi_sottotitoli():
    nomi = [
        "Serie.S01E01.1080p.ENG.Multi.Sub",
        "Serie.S01E01.1080p.ENG.Sub.ITA",
        "Serie.S01E01.720p.ITA",
        "Serie.S01E01.1080p.ITA.MULTi.Audio",
        "Serie.S01E01.1080p.ITA.ENG.Sub.ITA",
        "Serie.S01E01.1080p.SPA",
        "Serie.S01E01.1080p.MULTi",
    ]
    ordinati = sorted(nomi, key=lambda n: valuta(n, LINGUE), reverse=True)
    assert ordinati == [
        "Serie.S01E01.1080p.ITA.MULTi.Audio",
        "Serie.S01E01.1080p.ITA.ENG.Sub.ITA",
        "Serie.S01E01.720p.ITA",
        "Serie.S01E01.1080p.MULTi",
        "Serie.S01E01.1080p.SPA",
        "Serie.S01E01.1080p.ENG.Sub.ITA",
        "Serie.S01E01.1080p.ENG.Multi.Sub",
    ]


def test_risultati_con_solo_sottotitoli_dopo_l_audio_italiano():
    tutti = [riassunto({"title": "Serie S01E01 ENG ITA SUB 1080p", "seeders": 900, "protocol": "torrent"}),
             riassunto({"title": "Serie S01E01 ITA 720p", "seeders": 3, "protocol": "torrent"})]
    assert [r["titolo"] for r in ordina(tutti)] == ["Serie S01E01 ITA 720p", "Serie S01E01 ENG ITA SUB 1080p"]
    assert tutti[0]["italiano"] is False and tutti[0]["sottotitoli_italiano"] is True
    # con lo spagnolo come prima lingua vince lo spagnolo
    es = [riassunto({"title": n, "seeders": 1, "protocol": "torrent"}, ("es", "it")) for n in
          ("Serie S01E01 ITA", "Serie S01E01 SPA")]
    assert ordina(es)[0]["titolo"] == "Serie S01E01 SPA"
