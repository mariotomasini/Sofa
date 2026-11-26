"""Test del configuratore: generazione dei file, qBittorrent, Radarr, HDHomeRun."""

import json
import re
import zlib
from pathlib import Path

import bcrypt
import pytest

from configuratore.genera import genera, normalizza_percorso
from configuratore.hdhomerun import e_risposta, pacchetto_richiesta
from configuratore.impostazioni import Impostazioni, leggi_env
from configuratore.messaggi import ErroreMediabox
from configuratore.prepara import (
    hash_qbittorrent,
    impostazioni_qbittorrent,
    leggi_ini,
    prepara_jellyfin,
    verifica_hash_qbittorrent,
)
from configuratore.servizi.formati import costruisci_profilo
from configuratore.servizi.radarr import qualita_ammessa

RADICE = Path(__file__).parents[2]
FIXTURE = Path(__file__).parent / "fixtures"


@pytest.fixture
def cartella(tmp_path):
    """Una cartella mediabox finta con .env.example vero e un .env di prova."""
    (tmp_path / ".env.example").write_text((RADICE / ".env.example").read_text())
    (tmp_path / ".env").write_text(
        "TMDB_API_KEY=abc\nADMIN_PASSWORD=Pa$$word123\nRADARR_API_KEY=r\nPROWLARR_API_KEY=p\n"
        "SONARR_API_KEY=s\nMEDIA_DIR=D:\\Mediabox\\\nHOST_IP=192.168.1.10\n"
    )
    return tmp_path


def test_leggi_env_con_virgolette_e_commenti(tmp_path):
    f = tmp_path / ".env"
    f.write_text('# commento\nA="uno"\nB=due=tre\n\nC=\n')
    assert leggi_env(f) == {"A": "uno", "B": "due=tre", "C": ""}


def test_env_mancante_da_errore_chiaro(tmp_path):
    with pytest.raises(ErroreMediabox) as e:
        Impostazioni.carica(tmp_path)
    assert "installer" in e.value.cosa_fare


def test_percorsi_windows():
    assert normalizza_percorso("D:\\Mediabox\\") == "D:/Mediabox"
    assert normalizza_percorso("/srv/mediabox/dati/") == "/srv/mediabox/dati"


def test_genera_base(cartella):
    imp = Impostazioni.carica(cartella)
    cambiati = genera(imp, cartella)
    assert "docker-compose.yml" in cambiati and "Caddyfile" in cambiati
    compose = (cartella / "generato" / "docker-compose.yml").read_text()
    # il "$" della password va raddoppiato per Docker Compose
    assert 'ADMIN_PASSWORD: "Pa$$$$word123"' in compose
    assert '"D:/Mediabox:/data"' in compose
    assert "sonarr:" not in compose and "tvheadend:" not in compose
    assert "/dev/dri" not in compose
    caddy = (cartella / "generato" / "Caddyfile").read_text()
    assert "handle /sonarr*" not in caddy
    hash_pw = re.search(r"admin (\$2[aby]\$\S+)", caddy).group(1)
    assert bcrypt.checkpw(b"Pa$$word123", hash_pw.encode())
    # seconda esecuzione: niente cambia (niente riavvii inutili)
    assert genera(imp, cartella) == []
    assert (cartella / "generato" / ".da-riavviare").read_text().split() == ["caddy"]


def test_genera_con_opzioni(cartella):
    with open(cartella / ".env", "a") as f:
        f.write("WITH_SONARR=si\nDVB=si\nVAAPI=si\nRENDER_GID=993\n")
    genera(Impostazioni.carica(cartella), cartella)
    compose = (cartella / "generato" / "docker-compose.yml").read_text()
    assert "sonarr:" in compose and "tvheadend:" in compose
    assert '- "993"' in compose and "/dev/dri:/dev/dri" in compose
    assert "TVHeadend" in (cartella / "generato" / "homepage" / "services.yaml").read_text()


def test_vaapi_senza_gruppo_da_errore(cartella):
    with open(cartella / ".env", "a") as f:
        f.write("VAAPI=si\n")
    with pytest.raises(ErroreMediabox):
        genera(Impostazioni.carica(cartella), cartella)


def test_password_con_virgolette_rifiutata(cartella):
    with open(cartella / ".env", "a") as f:
        f.write('ADMIN_PASSWORD=ab"cdefgh\n')
    with pytest.raises(ErroreMediabox):
        genera(Impostazioni.carica(cartella), cartella)


def test_hash_qbittorrent():
    valore = hash_qbittorrent("segreta")
    assert valore.startswith("@ByteArray(") and valore.endswith(")")
    assert verifica_hash_qbittorrent("segreta", f'"{valore}"')
    assert not verifica_hash_qbittorrent("altra", valore)
    assert not verifica_hash_qbittorrent("segreta", "spazzatura")


def test_impostazioni_qbittorrent_unisce_senza_perdere_nulla(cartella):
    imp = Impostazioni.carica(cartella)
    esistente = "[BitTorrent]\nSession\\Port=6881\n\n[Preferences]\nWebUI\\Port=8080\nAltro\\Valore=1\n"
    testo = impostazioni_qbittorrent(imp, esistente)
    ini = leggi_ini(testo)
    assert ini["BitTorrent"]["Session\\Port"] == "6881"
    assert ini["Preferences"]["Altro\\Valore"] == "1"
    assert ini["Preferences"]["WebUI\\AuthSubnetWhitelist"] == "172.28.64.0/24"
    assert ini["Preferences"]["WebUI\\HostHeaderValidation"] == "false"
    assert ini["LegalNotice"]["Accepted"] == "true"
    # la password resta la stessa alla seconda passata
    assert impostazioni_qbittorrent(imp, testo) == testo


def test_base_url_jellyfin(cartella, tmp_path):
    imp = Impostazioni.carica(cartella)
    volumi = tmp_path / "volumi"
    (volumi / "jellyfin_config").mkdir(parents=True)
    assert prepara_jellyfin(imp, volumi) is False  # primo avvio: file nuovo, niente riavvio
    rete = volumi / "jellyfin_config" / "config" / "network.xml"
    assert "<BaseUrl>/jellyfin</BaseUrl>" in rete.read_text()
    rete.write_text(rete.read_text().replace("/jellyfin", ""))
    assert prepara_jellyfin(imp, volumi) is True  # cambiato: va riavviato
    assert "<BaseUrl>/jellyfin</BaseUrl>" in rete.read_text()


def test_profilo_qualita_radarr():
    schema = json.loads((FIXTURE / "radarr_qualityprofile_schema.json").read_text())
    ids = {"Lingua · Italiano": 1, "Lingua · Spagnolo": 4, "Lingua · Inglese": 5, "Lingua · Tedesco": 6,
           "Qualità · 1080p": 2, "Formato dell'utente": 3}
    profilo = costruisci_profilo(schema, qualita_ammessa, ids, ["it", "es", "en"], {"id": -1, "name": "Any"})
    gruppo = profilo["items"][-1]
    assert gruppo["allowed"] and profilo["cutoff"] == gruppo["id"] >= 1000
    ammessi = {i["quality"]["name"] for i in gruppo["items"]}
    assert {"Bluray-1080p", "WEBDL-1080p", "HDTV-720p", "DVD", "SDTV"} <= ammessi
    assert not ammessi & {"Remux-1080p", "Bluray-2160p", "CAM", "BR-DISK", "WEBDL-2160p"}
    assert not any(e.get("allowed") for e in profilo["items"][:-1])
    punti = {f["name"]: f["score"] for f in profilo["formatItems"]}
    assert punti == {"Lingua · Italiano": 64000, "Lingua · Spagnolo": 32000, "Lingua · Inglese": 16000,
                     "Lingua · Tedesco": 0, "Qualità · 1080p": 200, "Formato dell'utente": 0}
    assert profilo["minFormatScore"] == 0 and profilo["cutoffFormatScore"] == 64200
    assert profilo["name"] == "Migliore disponibile" and profilo["upgradeAllowed"]
    assert profilo["language"]["id"] == -1


@pytest.mark.parametrize("nome, ita, sub", [
    ("Dune.Parte.Due.2024.iTALiAN.ENG.AC3.1080p.WEB-DL.x264", True, False),
    ("Oppenheimer 2023 ITA-ENG 1080p BluRay", True, False),
    ("Film.2023.AC3.ITA.ENG.Sub.Ita.Eng.1080p", True, True),
    ("Film.2023.1080p.WEB-DL.ENG.Sub.ITA", False, True),
    ("Film.2023.1080p.SUBITA", False, True),
    ("Capitalism.2023.1080p.WEB-DL.ENG", False, False),
    # solo sottotitoli italiani, scritti in tanti modi: non è audio italiano
    ("Serie.S01E02.1080p.WEB.ENG.ITA.SUB", False, True),
    ("Serie S01E02 [Sub ENG-ITA] 1080p", False, True),
    ("Serie.S01E02.Subs.ENG.SPA.ITA.1080p", False, True),
    ("Serie.S01E02.iTALiAN.SUBBED.720p", False, True),
    ("Serie.S01E01.ENG.HardSub.ITA", False, True),
    ("Serie.S01E01.1080p.ITA.Softsub", False, True),
    # audio italiano vero, anche con i sottotitoli
    ("Serie S01E03 ITA ENG 1080p WEB-DL", True, False),
    ("Serie.S02E01.ITA.ENG.AC3.Subs.ENG.1080p", True, False),
])
def test_regex_italiano(nome, ita, sub):
    from configuratore.servizi.formati import RE_AUDIO_ITA, RE_SUB_ITA
    assert bool(re.search(RE_AUDIO_ITA.removeprefix("(?i)"), nome, re.I)) is ita
    assert bool(re.search(RE_SUB_ITA.removeprefix("(?i)"), nome, re.I)) is sub


def test_leggi_indexer_e_torznab():
    from configuratore.servizi.prowlarr import leggi_indexer, leggi_torznab
    assert leggi_indexer("1337x, Il Corsaro Blu|apikey=abc ,\n") == [("1337x", {}), ("Il Corsaro Blu", {"apikey": "abc"})]
    assert leggi_torznab("Indexarr|http://indexarr:8080/torznab/api|k1; Altro|https://x.y/z") == [
        ("Indexarr", "http://indexarr:8080/torznab", "/api", "k1"),
        ("Altro", "https://x.y/z", "/api", ""),
    ]


def test_prepara_indexer_usa_il_primo_indirizzo():
    from configuratore.servizi.prowlarr import prepara_indexer, trova_schema
    schemi = [{"definitionName": "torrentgalaxy", "name": "TorrentGalaxy", "indexerUrls": ["https://tg.example/"],
               "fields": [{"name": "baseUrl", "value": None}, {"name": "apikey", "value": ""}]}]
    schema = trova_schema(schemi, "Torrent Galaxy")
    dati = prepara_indexer(schema, "TorrentGalaxy", {"apikey": "x"}, 1, 25, [7])
    campi = {c["name"]: c["value"] for c in dati["fields"]}
    assert campi == {"baseUrl": "https://tg.example/", "apikey": "x"}
    assert dati["appProfileId"] == 1 and dati["tags"] == [7] and dati["enable"]


def test_qualita_ammessa():
    assert qualita_ammessa({"resolution": 1080, "source": "bluray", "modifier": "none"})
    assert not qualita_ammessa({"resolution": 2160, "source": "webdl", "modifier": "none"})
    assert not qualita_ammessa({"resolution": 0, "source": "cam", "modifier": "none"})


def test_pacchetto_hdhomerun():
    p = pacchetto_richiesta()
    assert p[:4] == bytes([0, 2, 0, 12])
    corpo = bytes([0, 3, 0, 0])
    risposta = corpo + zlib.crc32(corpo).to_bytes(4, "little")
    assert e_risposta(risposta)
    assert not e_risposta(risposta[:-1] + b"\x00")


def test_genera_musica_anime_sottotitoli(cartella):
    with open(cartella / ".env", "a") as f:
        f.write("WITH_ANIME=si\nWITH_LIDARR=si\nLIDARR_API_KEY=l\nWITH_FLARESOLVERR=no\n")
    genera(Impostazioni.carica(cartella), cartella)
    compose = (cartella / "generato" / "docker-compose.yml").read_text()
    # gli anime accendono Sonarr anche senza WITH_SONARR
    assert "  sonarr:" in compose and "  lidarr:" in compose and "  bazarr:" in compose
    assert "flaresolverr:" not in compose
    caddy = (cartella / "generato" / "Caddyfile").read_text()
    assert "handle /lidarr*" in caddy and "handle /bazarr*" in caddy
    servizi = (cartella / "generato" / "homepage" / "services.yaml").read_text()
    assert "Lidarr" in servizi and "Bazarr" in servizi and "anime" in servizi


def test_lidarr_senza_chiave_da_errore(cartella):
    with open(cartella / ".env", "a") as f:
        f.write("WITH_LIDARR=si\n")
    with pytest.raises(ErroreMediabox):
        genera(Impostazioni.carica(cartella), cartella)


def test_librerie_jellyfin():
    from configuratore.servizi.jellyfin import librerie

    class Finta:
        anime = False

    nomi = [n for n, _, _ in librerie(Finta())]
    assert nomi == ["Film", "Serie", "Musica", "Registrazioni"]
    Finta.anime = True
    assert ("Anime", "tvshows", "/data/anime") in librerie(Finta())


def test_chiave_bazarr():
    from configuratore.servizi.bazarr import leggi_chiave, profilo_lingue

    testo = (
        "general:\n  apikey: ''\nauth:\n  type: null\n"
        "  apikey: 0123456789abcdef0123456789abcdef\nsonarr:\n  apikey: ''\n"
    )
    assert leggi_chiave(testo) == "0123456789abcdef0123456789abcdef"
    assert leggi_chiave("auth:\n  apikey: ''\n") == ""
    voci = profilo_lingue(["it", "en"])[0]["items"]
    assert [v["language"] for v in voci] == ["it", "en"]
    assert all("audio_only_include" in v for v in voci)  # senza, Bazarr va in errore


def test_menu_impostazioni(cartella, monkeypatch, capsys):
    from configuratore.menu import menu

    risposte = iter(["2", "l", "it,en,ja", "n", "casa rossi!", "c", "/mnt/grande/mediabox/", "s"])
    monkeypatch.setattr("builtins.input", lambda *_: next(risposte))
    assert menu(cartella) == 0
    valori = leggi_env(cartella / ".env")
    assert valori["WITH_ANIME"] == "si"
    assert valori["LINGUE_SOTTOTITOLI"] == "it,en,ja"
    assert valori["NTFY_TOPIC"] == "casarossi"
    assert valori["MEDIA_DIR"] == "/mnt/grande/mediabox"
    assert valori["SONARR_API_KEY"] == "s"  # chiave già presente: non cambia
    # le altre righe del file restano com'erano
    assert valori["TMDB_API_KEY"] == "abc"


def test_menu_esci_senza_modifiche(cartella, monkeypatch):
    from configuratore.menu import NESSUNA_MODIFICA, menu

    prima = (cartella / ".env").read_text()
    monkeypatch.setattr("builtins.input", lambda *_: "q")
    assert menu(cartella) == NESSUNA_MODIFICA
    assert (cartella / ".env").read_text() == prima


def test_menu_crea_chiave_lidarr(cartella, monkeypatch):
    from configuratore.menu import menu

    risposte = iter(["3", "s"])
    monkeypatch.setattr("builtins.input", lambda *_: next(risposte))
    menu(cartella)
    valori = leggi_env(cartella / ".env")
    assert valori["WITH_LIDARR"] == "si" and len(valori["LIDARR_API_KEY"]) == 32


def test_percorso_valido():
    from configuratore.menu import percorso_valido

    assert percorso_valido("/mnt/disco/", False) == "/mnt/disco"
    assert percorso_valido("relativo/x", False) is None
    assert percorso_valido("D:\\Mediabox\\", True) == "D:/Mediabox"
    assert percorso_valido('"E:\\Film"', True) == "E:/Film"
    assert percorso_valido("D:", True) is None
    assert percorso_valido("/linux", True) is None


def test_avviso_di_attesa(monkeypatch, capsys):
    from configuratore import rete

    orologio = iter([0, 0, 5, 21, 30, 45])
    monkeypatch.setattr(rete.time, "monotonic", lambda: next(orologio))
    attesa = rete.AvvisoAttesa("Radarr", 600)
    for _ in range(5):
        attesa.ogni_tanto()
    righe = capsys.readouterr().out.splitlines()
    assert righe == [
        "  · Attendo che Radarr sia pronto… (la prima accensione può richiedere qualche minuto)",
        "  · Attendo che Radarr sia pronto… 0:21 (al massimo 10:00)",
        "  · Attendo che Radarr sia pronto… 0:45 (al massimo 10:00)",
    ]


def test_un_servizio_che_non_risponde_non_ferma_gli_altri():
    import httpx

    from configuratore.__main__ import errore_imprevisto

    lento = errore_imprevisto("Lidarr", httpx.ReadTimeout("timed out"))
    assert lento.cosa == "Lidarr risponde troppo lentamente." and "mediabox configura" in lento.cosa_fare
    assert "ConnectError" in errore_imprevisto("Radarr", httpx.ConnectError("no")).cosa
    assert "KeyError" in errore_imprevisto("Jellyfin", KeyError("Id")).cosa


def test_genera_con_tgarr(cartella):
    from configuratore.impostazioni import Impostazioni as I

    env = cartella / ".env"
    env.write_text(env.read_text() + "\nWITH_TGARR=si\nTG_PHONE=+390000000\n")
    imp = I.carica(cartella)
    genera(imp, cartella)
    compose = (cartella / "generato" / "docker-compose.yml").read_text()
    assert "tgarr-crawler:" in compose and "tgarr-db:" in compose
    chiavi = leggi_env(env)
    assert all(len(chiavi[k]) == 48 for k in ("TGARR_API_KEY", "TGARR_DB_PASSWORD", "TGARR_MEILI_KEY"))
    assert chiavi["TGARR_API_KEY"] in compose
    # la seconda volta le chiavi restano le stesse
    genera(I.carica(cartella), cartella)
    assert leggi_env(env)["TGARR_API_KEY"] == chiavi["TGARR_API_KEY"]


def test_indexer_scartati_si_riprovano_dopo_una_settimana(tmp_path):
    import time as t

    from configuratore.servizi.prowlarr import leggi_scartati, salva_scartati

    f = tmp_path / "s.json"
    salva_scartati({"vecchio": t.time() - 8 * 24 * 3600, "recente": t.time()}, f)
    assert list(leggi_scartati(f)) == ["recente"]
    assert leggi_scartati(tmp_path / "manca.json") == {}


def test_indexer_pubblici_aperti():
    from configuratore.servizi.prowlarr import e_pubblico_aperto, priorita_pubblico

    video = {"capabilities": {"categories": [{"id": 2000, "subCategories": [{"id": 2040}]}]}}
    aperto = {"privacy": "public", "protocol": "torrent", "fields": [{"name": "baseUrl"}], "language": "it-IT", **video}
    assert e_pubblico_aperto(aperto) and priorita_pubblico(aperto) == 10
    assert not e_pubblico_aperto({**aperto, "privacy": "private"})
    assert not e_pubblico_aperto({**aperto, "fields": [{"name": "username"}]})
    assert not e_pubblico_aperto({**aperto, "capabilities": {"categories": [{"id": 3000}]}})
    assert priorita_pubblico({**aperto, "language": "en-US"}) == 25


def test_limiti_dimensione():
    from configuratore.servizi.formati import limiti_dimensione

    massimo, preferito = limiti_dimensione({"resolution": 1080}, 10)
    assert round(massimo * 120 / 1024, 1) == 10.0 and preferito == round(massimo / 2, 1)
    assert limiti_dimensione({"resolution": 720}, 10)[0] < massimo
    assert limiti_dimensione({"resolution": 480}, 10)[0] < limiti_dimensione({"resolution": 720}, 10)[0]
    assert limiti_dimensione({"resolution": 2160}, 10) is None


def test_dimensione_max_gb():
    assert Impostazioni({"DIMENSIONE_MAX_GB": "8,5"}).dimensione_max_gb == 8.5
    assert Impostazioni({}).dimensione_max_gb == 10.0
    assert Impostazioni({"DIMENSIONE_MAX_GB": "boh"}).dimensione_max_gb == 10.0


@pytest.mark.parametrize("nome, lingua", [
    ("Film.2023.iTALiAN.1080p.WEB-DL", "it"),
    ("Film.2023.SPANISH.1080p.WEB-DL", "es"),
    ("Film 2023 Castellano 1080p", "es"),
    ("Film.2023.1080p.WEB-DL.x264", "en"),
    ("Film.2023.1080p.ENG.Sub.ITA", "en"),
    ("Film.2023.1080p.ENG.SPA", "es"),
])
def test_lingue_release(nome, lingua):
    from configuratore.servizi.formati import formati, nome_lingua

    trovate = set()
    for codice in ("it", "es", "en"):
        _, condizioni = formati(["it", "es", "en"])[nome_lingua(codice)]
        esiti = []
        for _, valore, negata, _ in condizioni:
            presente = bool(re.search(valore.removeprefix("(?i)"), nome, re.I))
            esiti.append(presente != negata)
        if all(esiti):
            trovate.add(codice)
    assert lingua in trovate
    if lingua != "en":
        assert "en" not in trovate


def test_lingue_audio():
    assert Impostazioni({}).lingue_audio == ["it", "es", "en"]
    assert Impostazioni({"LINGUE_AUDIO": "es, it ,xx,es"}).lingue_audio == ["es", "it"]
    assert Impostazioni({"LINGUE_AUDIO": "zz"}).lingue_audio == ["it", "es", "en"]


def test_tgarr_entra_in_prowlarr_anche_senza_risultati(monkeypatch):
    """Prowlarr rifiuta (400) un indexer acceso che alla prova non trova niente: tgarr appena installato."""
    from configuratore.impostazioni import Impostazioni
    from configuratore.riepilogo import Esito
    from configuratore.servizi import tgarr

    class ProwlarrFinto:
        def __init__(self):
            self.indexer = []
            self.chiamate = []

        def attendi(self):
            pass

        def get(self, percorso, *a, **kw):
            if percorso == "indexer":
                return self.indexer
            if percorso == "indexer/schema":
                return [{"name": "Generic Newznab", "implementation": "Newznab",
                         "fields": [{"name": "baseUrl"}, {"name": "apiPath"}, {"name": "apiKey"}]}]
            if percorso == "appprofile":
                return [{"id": 1}]
            raise AssertionError(percorso)

        def post(self, percorso, dati, cosa="", params=None, **kw):
            self.chiamate.append(("POST", dati["enable"], params))
            if dati["enable"]:  # Prowlarr fa la prova anche con forceSave
                raise ErroreMediabox("Prowlarr non accetta", "no results were returned")
            self.indexer.append({**dati, "id": 7})
            return self.indexer[-1]

        def put(self, percorso, dati, cosa="", params=None, **kw):
            assert percorso == "indexer/7" and params == {"forceSave": "true"}  # alla modifica niente prova
            self.chiamate.append(("PUT", dati["enable"], params))
            self.indexer[0] = dati
            return dati

    finto = ProwlarrFinto()
    monkeypatch.setattr(tgarr, "attendi", lambda *a, **kw: None)
    monkeypatch.setattr(tgarr, "collega_arr", lambda *a, **kw: None)
    monkeypatch.setattr(tgarr, "radarr", lambda imp: None)
    monkeypatch.setattr(tgarr, "prowlarr", lambda imp: finto)
    imp = Impostazioni({"TGARR_API_KEY": "chiave", "WITH_SONARR": "no"})
    tgarr.configura(imp, Esito())
    assert [c[:2] for c in finto.chiamate] == [("POST", False), ("PUT", True)]
    assert finto.indexer[0]["enable"] and finto.indexer[0]["name"] == "Telegram (tgarr)"
    campi = {f["name"]: f.get("value") for f in finto.indexer[0]["fields"]}
    assert campi == {"baseUrl": "http://tgarr:8088/newznab", "apiPath": "/api", "apiKey": "chiave"}

    # di nuovo (mediabox configura): c'è già ed è acceso, non si tocca
    finto.chiamate.clear()
    tgarr.configura(imp, Esito())
    assert finto.chiamate == []


@pytest.mark.parametrize("nome, multi", [
    ("Serie.S01E02.MULTi.1080p.WEB", True),
    ("Film.2023.DUAL.1080p", True),
    ("Serie.S01E02.MULTi-SUB.1080p", False),
    ("Serie.S01E02.1080p.Multi.Subs", False),
])
def test_regex_multi_non_conta_i_sottotitoli(nome, multi):
    from configuratore.servizi.formati import RE_MULTI
    assert bool(re.search(RE_MULTI.removeprefix("(?i)"), nome, re.I)) is multi


def _punti(nome, lingue=("it", "es", "en"), risoluzione=1080):
    """Il punteggio che darebbero Radarr e Sonarr a una release con questo nome."""
    from configuratore.servizi.formati import formati
    totale = 0
    for _, (p, condizioni) in formati(list(lingue)).items():
        esiti = []
        for tipo, valore, negata, _ in condizioni:
            vero = (bool(re.search(valore.removeprefix("(?i)"), nome, re.I)) if tipo == "titolo"
                    else valore == risoluzione)
            esiti.append(vero != negata)
        if all(esiti):
            totale += p
    return totale


def test_solo_sottotitoli_non_raggiunge_il_limite():
    """Una puntata con l'italiano solo nei sottotitoli resta sotto il limite: si cerca ancora l'audio italiano."""
    from configuratore.servizi.formati import PUNTI_1080P, PUNTI_LINGUA
    limite = PUNTI_LINGUA[0] + PUNTI_1080P
    assert _punti("Serie.S01E02.1080p.WEB.ENG.ITA.SUB") < limite
    assert _punti("Serie.S01E02.1080p.WEB.ITA.ENG") >= limite
    assert _punti("Serie.S01E02.1080p.WEB.MULTi") < limite  # si cerca ancora una versione con ITA scritto


@pytest.mark.parametrize("migliore, peggiore", [
    # la prima lingua dell'audio vince sempre, anche contro MULTI, multi-sub e sottotitoli
    ("Serie.S01E01.720p.ITA", "Serie.S01E01.1080p.MULTi.Audio.SPA.Multi.Sub.Sub.ITA"),
    ("Serie.S01E01.720p.SPA", "Serie.S01E01.1080p.MULTi.GER.Multi.Subs.ENG.Sub.ITA"),
    # stesso audio: prima i sottotitoli nella prima lingua, poi i multi-sub
    ("Serie.S01E01.1080p.ITA.ENG.Sub.ITA", "Serie.S01E01.1080p.ITA.ENG"),
    ("Serie.S01E01.1080p.ENG.Sub.ITA", "Serie.S01E01.1080p.ENG.Sub.SPA"),
    ("Serie.S01E01.1080p.ENG.Sub.SPA", "Serie.S01E01.1080p.ENG.Multi.Sub"),
    ("Serie.S01E01.1080p.ENG.Multi.Sub", "Serie.S01E01.1080p.ENG"),
    # stessa lingua: multi audio prima del solo audio
    ("Serie.S01E01.1080p.ITA.MULTi.Audio", "Serie.S01E01.1080p.ITA"),
    # "MULTi" senza lingue può avere la prima: tra la prima e la seconda lingua
    ("Serie.S01E01.720p.ITA", "Serie.S01E01.1080p.MULTi.Multi.Sub.Sub.ITA"),
    ("Serie.S01E01.720p.MULTi", "Serie.S01E01.1080p.SPA.Sub.ITA"),
    ("Serie.S01E01.1080p.MULTi", "Serie.S01E01.1080p.ENG"),
    # "MULTi" con le lingue scritte vale per quelle
    ("Serie.S01E01.1080p.MULTi", "Serie.S01E01.1080p.MULTi.SPA.ENG"),
])
def test_prima_l_audio_poi_i_sottotitoli(migliore, peggiore):
    assert _punti(migliore) > _punti(peggiore)


def test_prima_lingua_dalle_impostazioni():
    """Con LINGUE_AUDIO=es,it a vincere è lo spagnolo, anche nei sottotitoli."""
    assert _punti("Serie.S01E01.720p.SPA", ("es", "it")) > _punti("Serie.S01E01.1080p.ITA.Sub.ITA", ("es", "it"))
    assert _punti("Serie.S01E01.ENG.Sub.SPA", ("es", "it")) > _punti("Serie.S01E01.ENG.Sub.ITA", ("es", "it"))


def test_i_bonus_non_superano_mai_una_lingua():
    from configuratore.servizi.formati import PUNTI_1080P, PUNTI_LINGUA, PUNTI_MULTI, PUNTI_MULTI_SUB, PUNTI_SUB
    bonus = PUNTI_MULTI + PUNTI_MULTI_SUB + sum(PUNTI_SUB) + PUNTI_1080P
    assert all(a - b > bonus for a, b in zip(PUNTI_LINGUA, [*PUNTI_LINGUA[1:], 0], strict=True))


def test_minimo_di_fonti_in_prowlarr():
    from configuratore.impostazioni import Impostazioni
    from configuratore.servizi.prowlarr import imposta_min_fonti, min_fonti

    assert min_fonti(Impostazioni({})) == 3
    assert min_fonti(Impostazioni({"MIN_FONTI": "5"})) == 5
    assert min_fonti(Impostazioni({"MIN_FONTI": "tanti"})) == 3 and min_fonti(Impostazioni({"MIN_FONTI": "0"})) == 1

    class ProwlarrFinto:
        def __init__(self):
            self.profili = [{"id": 1, "name": "Standard", "minimumSeeders": 1}]
            self.salvati = []

        def get(self, percorso, *a, **kw):
            return self.profili

        def put(self, percorso, dati, *a, **kw):
            self.salvati.append((percorso, dati["minimumSeeders"]))

    p = ProwlarrFinto()
    imposta_min_fonti(p, 3)
    assert p.salvati == [("appprofile/1", 3)]
    p.profili[0]["minimumSeeders"] = 3
    p.salvati.clear()
    imposta_min_fonti(p, 3)
    assert p.salvati == []  # già giusto: niente da salvare


def test_tailscale_come_servizio_solo_su_linux_e_se_non_c_e_gia(cartella):
    import yaml

    genera(Impostazioni.carica(cartella), cartella)
    compose = yaml.safe_load((cartella / "generato" / "docker-compose.yml").read_text())
    ts = compose["services"]["tailscale"]
    assert ts["network_mode"] == "host" and "/dev/net/tun:/dev/net/tun" in ts["devices"]
    assert ts["image"].startswith("ghcr.io/tailscale/tailscale:v")
    assert "tailscale_stato" in compose["volumes"]
    assert "/var/run/tailscale:/var/run/tailscale" in compose["services"]["impostazioni"]["volumes"]
    # già installato sul PC (vecchio "mediabox remote"): niente doppione, ma la pagina lo vede
    with open(cartella / ".env", "a") as f:
        f.write("TAILSCALE_HOST=si\n")
    genera(Impostazioni.carica(cartella), cartella)
    compose = yaml.safe_load((cartella / "generato" / "docker-compose.yml").read_text())
    assert "tailscale" not in compose["services"]
    assert "/var/run/tailscale:/var/run/tailscale" in compose["services"]["impostazioni"]["volumes"]
    # Windows: Tailscale si installa con "mediabox remote" (rete dell'host non disponibile)
    with open(cartella / ".env", "a") as f:
        f.write("TAILSCALE_HOST=no\nPIATTAFORMA=windows\n")
    genera(Impostazioni.carica(cartella), cartella)
    compose = yaml.safe_load((cartella / "generato" / "docker-compose.yml").read_text())
    assert "tailscale" not in compose["services"]
    assert not any("tailscale" in v for v in compose["services"]["impostazioni"]["volumes"])


def test_piano_delle_impostazioni(tmp_path):
    from configuratore.piano_impostazioni import Piano, leggi, piano, scrivi

    # la chiave TMDB: solo Cerca va ricreato (lo fa Docker), nessun programma da riconfigurare
    assert piano({"TMDB_API_KEY"}) == Piano(prepara=False, configura=[])
    assert piano({"LINGUE_AUDIO"}).configura == ["radarr", "sonarr"]
    assert piano({"LINGUE_SOTTOTITOLI"}).configura == ["bazarr"]
    serie = piano({"WITH_SONARR", "SONARR_API_KEY"})
    assert serie.prepara and serie.configura == ["sonarr", "prowlarr", "bazarr", "jellyfin"]
    assert piano({"MEDIA_DIR"}) == Piano(prepara=True, configura=[])
    # una voce sconosciuta: si rifà tutto, meglio lento che sbagliato
    assert piano({"VOCE_NUOVA"}) == Piano(prepara=True, configura=None)
    # due salvataggi prima che il PC applichi il primo: si sommano
    scrivi(tmp_path, piano({"LINGUE_AUDIO"}))
    assert scrivi(tmp_path, piano({"LINGUE_SOTTOTITOLI"})).configura == ["radarr", "sonarr", "bazarr"]
    assert (tmp_path / "generato" / "impostazioni.piano").read_text() == "prepara=no\nconfigura=radarr,sonarr,bazarr\n"
    scrivi(tmp_path, piano({"VOCE_NUOVA"}))
    assert leggi(tmp_path) == Piano(prepara=True, configura=None)
    assert "Radarr" in piano({"LINGUE_AUDIO"}).descrizione() and "non si toccano" in piano({"LINGUE_AUDIO"}).descrizione()


def test_configura_solo_alcuni_servizi(monkeypatch, cartella):
    from configuratore import __main__ as principale
    from configuratore.servizi import bazarr, jellyfin, prowlarr, qbittorrent, radarr, sonarr

    fatti = []
    for modulo in (qbittorrent, radarr, sonarr, prowlarr, bazarr, jellyfin):
        monkeypatch.setattr(modulo, "configura", lambda imp, esito, m=modulo.__name__: fatti.append(m.rsplit(".", 1)[-1]))
    monkeypatch.setattr(principale, "CARTELLA_MEDIABOX", cartella)
    (cartella / "generato").mkdir(exist_ok=True)
    with open(cartella / ".env", "a") as f:
        f.write("WITH_SONARR=si\n")
    assert principale._configura(Impostazioni.carica(cartella), {"radarr", "sonarr"}) == 0
    assert fatti == ["radarr", "sonarr"]


def test_pagina_iniziale_con_i_numeri_dei_download(cartella):
    import yaml

    with open(cartella / ".env", "a") as f:
        f.write("WITH_SONARR=si\n")
    genera(Impostazioni.carica(cartella), cartella)
    testo = (cartella / "generato" / "homepage" / "services.yaml").read_text()
    gruppi = {nome: voci for g in yaml.safe_load(testo) for nome, voci in g.items()}
    # in cima al Pannello il ritorno a Cerca, che è la pagina principale
    assert list(gruppi) == ["Cerca", "Guarda", "Download", "Manutenzione"]
    torna = {n: v for voce in gruppi["Cerca"] for n, v in voce.items()}
    assert torna["Torna a Cerca"]["href"] == "/cerca/"
    download = {n: v for voce in gruppi["Download"] for n, v in voce.items()}
    assert download["Radarr"]["widget"]["key"] == "{{HOMEPAGE_VAR_RADARR}}"
    assert download["Sonarr"]["widget"]["url"] == "http://sonarr:8989/sonarr"
    # le chiavi vere stanno nel compose, non nella pagina
    compose = (cartella / "generato" / "docker-compose.yml").read_text()
    assert "HOMEPAGE_VAR_RADARR:" in compose
    assert "Lidarr" not in download


def test_cerca_e_la_pagina_principale_e_il_pannello_ha_la_sua_porta(cartella):
    genera(Impostazioni.carica(cartella), cartella)
    caddy = (cartella / "generato" / "Caddyfile").read_text()
    principale, pannello = caddy.split(":8081 {")
    # tutto quello che non è un servizio va a Cerca; i vecchi indirizzi /cerca/... tornano alla radice
    assert "reverse_proxy cerca:8000" in principale.split("# --- Cerca: la pagina principale")[1]
    assert "homepage:3000" not in principale
    assert "redir /cerca " not in principale  # /cerca?q=... sono i risultati di ricerca di Cerca
    assert "redir {re.vecchia_cerca.1}?{query} 301" in principale
    # i servizi restano dove sono, prima della radice
    assert principale.index("handle /jellyfin/*") < principale.index("# --- Cerca: la pagina principale")
    # il Pannello: Homepage, e i link ai servizi tornano all'indirizzo principale (porta 80: senza porta)
    assert "reverse_proxy homepage:3000" in pannello
    assert "redir http://{host}{uri} 302" in pannello
    compose = (cartella / "generato" / "docker-compose.yml").read_text()
    assert '"8081:8081"' in compose
    assert 'PANNELLO_PORT: "8081"' in compose


def test_pannello_con_un_altra_porta_principale(cartella):
    with open(cartella / ".env", "a") as f:
        f.write("HTTP_PORT=8080\nPANNELLO_PORT=9090\n")
    genera(Impostazioni.carica(cartella), cartella)
    caddy = (cartella / "generato" / "Caddyfile").read_text()
    assert "redir http://{host}:8080{uri} 302" in caddy
    compose = (cartella / "generato" / "docker-compose.yml").read_text()
    assert '"9090:8081"' in compose
    assert 'INDIRIZZO_PUBBLICO: "http://mediabox.local:8080"' in compose
