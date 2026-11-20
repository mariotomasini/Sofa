"""Aggiornamenti da GitHub: si scarica e si installa solo quello che è cambiato."""

import io
import json
import zipfile

import httpx

from configuratore import aggiornamenti as agg


def archivio(file: dict[str, str]) -> bytes:
    dati = io.BytesIO()
    with zipfile.ZipFile(dati, "w") as z:
        for nome, contenuto in file.items():
            info = zipfile.ZipInfo(f"mariotomasini-Sofa-abc123/{nome}")
            info.external_attr = (0o755 if nome == "mediabox" else 0o644) << 16
            z.writestr(info, contenuto)
    return dati.getvalue()


NUOVA = {
    "VERSION": "1.3.0\n",
    "mediabox": "#!/bin/bash\necho nuovo\n",
    "cerca/app/main.py": "print('cerca')\n",
    "configuratore/configuratore/x.py": "print('nuovo')\n",
    ".env.example": "IMG_A=2\nIMG_B=2\nIMG_C=1\n",
    ".env": "ADMIN_PASSWORD=da-non-usare\n",
    "generato/docker-compose.yml": "da non toccare\n",
}


def prepara(tmp_path):
    for nome, contenuto in {
        "VERSION": "1.3.0\n",
        "mediabox": "#!/bin/bash\necho vecchio\n",
        "cerca/app/main.py": "print('cerca')\n",
        "configuratore/configuratore/x.py": "print('vecchio')\n",
        ".env.example": "IMG_A=1\nIMG_B=1\nIMG_C=1\n",
        ".env": "ADMIN_PASSWORD=segreta\nIMG_A=1\nIMG_B=scelta-a-mano\nIMG_C=1\n",
        "generato/docker-compose.yml": "mio\n",
    }.items():
        (tmp_path / nome).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / nome).write_text(contenuto)
    return tmp_path


def github(release=None, sha="abc123", file=NUOVA, rotto=False):
    richieste = []

    def gestore(r: httpx.Request) -> httpx.Response:
        richieste.append(r.url.path)
        if rotto:
            raise httpx.ConnectError("niente rete")
        if r.url.path.endswith("/releases/latest"):
            if release:
                return httpx.Response(200, json={"tag_name": release, "name": f"mediabox {release}",
                                                 "published_at": "2026-11-20T10:00:00Z",
                                                 "zipball_url": f"https://api.github.com/repos/x/y/zipball/{release}"})
            return httpx.Response(404, json={"message": "Not Found"})
        if r.url.path.endswith("/commits/main"):
            return httpx.Response(200, json={"sha": sha, "commit": {"message": "Cerca più veloce\n\ndettagli",
                                                                     "committer": {"date": "2026-11-20T09:00:00Z"}}})
        if "/zipball/" in r.url.path:
            return httpx.Response(200, content=archivio(file))
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(gestore)), richieste


def test_senza_release_segue_il_ramo_principale_e_installa_solo_il_necessario(tmp_path):
    cartella = prepara(tmp_path)
    client, _ = github()
    esito = agg.controlla(cartella, client)
    assert esito["disponibile"] and esito["ultima"]["fonte"] == "main"
    assert esito["ultima"]["messaggio"] == "Cerca più veloce"

    piano = agg.installa(cartella, client)
    assert piano["cerca"] is False  # Cerca non è cambiato: niente da ricostruire
    assert piano["configuratore"] is True and piano["configura"] is True
    assert piano["file_cambiati"] == 3  # mediabox, configuratore, .env.example
    assert piano["immagini"] == ["IMG_A"]  # IMG_B era stata cambiata a mano, IMG_C è uguale
    env = (cartella / ".env").read_text()
    assert "ADMIN_PASSWORD=segreta" in env and "IMG_A=2" in env and "IMG_B=scelta-a-mano" in env
    assert (cartella / "generato/docker-compose.yml").read_text() == "mio\n"
    assert (cartella / "mediabox").read_text().endswith("nuovo\n")
    assert (cartella / "mediabox").stat().st_mode & 0o111  # resta eseguibile
    assert json.loads((cartella / agg.PIANO).read_text())["id"] == "abc123"
    assert agg.installata(cartella)["id"] == "abc123"
    assert agg.leggi_stato(cartella)["fase"] == "installo"
    # la pagina delle impostazioni gira con un altro utente: i file di stato devono essere leggibili da tutti
    for nome in (agg.STATO, agg.PIANO, agg.INSTALLATA):
        assert (cartella / nome).stat().st_mode & 0o044 == 0o044

    # stesso commit: niente da fare, e non si scarica niente
    client, richieste = github()
    assert agg.controlla(cartella, client)["disponibile"] is False
    assert agg.installa(cartella, client) is None
    assert not any("zipball" in r for r in richieste)
    assert agg.leggi_stato(cartella)["fase"] == "aggiornato"


def test_con_le_release_conta_il_numero_di_versione(tmp_path):
    cartella = prepara(tmp_path)
    client, _ = github(release="v1.3.0")
    assert agg.controlla(cartella, client)["disponibile"] is False  # stessa versione
    client, _ = github(release="v1.4.0", file={**NUOVA, "VERSION": "1.4.0\n"})
    esito = agg.controlla(cartella, client)
    assert esito["disponibile"] and esito["ultima"]["versione"] == "1.4.0"
    piano = agg.installa(cartella, client)
    assert piano["versione"] == "1.4.0" and agg.installata(cartella)["fonte"] == "release"


def test_senza_internet_lo_dice(tmp_path):
    cartella = prepara(tmp_path)
    client, _ = github(rotto=True)
    esito = agg.controlla(cartella, client)
    assert esito["disponibile"] is False and "Internet" in esito["errore"]


def test_archivio_sbagliato_non_tocca_niente(tmp_path):
    cartella = prepara(tmp_path)
    client, _ = github(file={"README.md": "altro progetto"})
    try:
        agg.installa(cartella, client)
        raise AssertionError("doveva fallire")
    except agg.ErroreAggiornamento as exc:
        assert "mediabox" in str(exc)
    assert (cartella / "mediabox").read_text().endswith("vecchio\n")
    assert agg.leggi_stato(cartella)["fase"] == "errore"
