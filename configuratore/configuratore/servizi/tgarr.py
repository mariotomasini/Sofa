"""Telegram come fonte di download, con tgarr.

tgarr legge i canali e i gruppi (anche privati) a cui è iscritto l'account
Telegram collegato e si presenta come:
- un indexer Newznab, che aggiungiamo in Prowlarr (e da lì va a Radarr/Sonarr);
- un client SABnzbd, che aggiungiamo in Radarr e Sonarr.
I file finiscono in <MEDIA_DIR>/downloads/tgarr e Radarr/Sonarr li importano.
"""

from __future__ import annotations

from ..impostazioni import Impostazioni
from ..messaggi import ok
from ..rete import attendi
from .arr import Arr
from .prowlarr import PRIORITA_TORZNAB, prepara_indexer, prowlarr
from .radarr import radarr
from .sonarr import sonarr

INDIRIZZO = "http://tgarr:8088"
NOME = "Telegram (tgarr)"
# Il percorso che tgarr comunica e quello che vedono Radarr/Sonarr
PERCORSO_TGARR = "/downloads/"
PERCORSO_ARR = "/data/downloads/"


def campi_sabnzbd(imp: Impostazioni, campo_categoria: str, categoria: str) -> dict:
    return {
        "host": "tgarr",
        "port": 8088,
        "useSsl": False,
        "urlBase": "/sabnzbd",
        "apiKey": imp.obbligatorio("TGARR_API_KEY"),
        "username": "",
        "password": "",
        campo_categoria: categoria,
    }


def collega_arr(arr: Arr, imp: Impostazioni, campo_categoria: str, categoria: str) -> None:
    arr.attendi()
    arr.assicura_da_schema(
        "downloadclient",
        "Sabnzbd",
        NOME,
        campi_sabnzbd(imp, campo_categoria, categoria),
        extra={"removeCompletedDownloads": True, "removeFailedDownloads": True, "priority": 1},
    )
    mappe = arr.get("remotepathmapping")
    if not any(m.get("host") == "tgarr" and m.get("remotePath") == PERCORSO_TGARR for m in mappe):
        arr.post(
            "remotepathmapping",
            {"host": "tgarr", "remotePath": PERCORSO_TGARR, "localPath": PERCORSO_ARR},
            "collegare la cartella dei download di tgarr",
        )
    ok(f"{arr.nome}: download da Telegram con tgarr (categoria \"{categoria}\")")


def configura(imp: Impostazioni, esito) -> None:
    attendi("tgarr", f"{INDIRIZZO}/newznab/api?t=caps", accetta=lambda r: r.status_code < 500)

    collega_arr(radarr(imp), imp, "movieCategory", "movies")
    if imp.sonarr:
        collega_arr(sonarr(imp), imp, "tvCategory", "tv")

    p = prowlarr(imp)
    p.attendi()
    campi = {"baseUrl": f"{INDIRIZZO}/newznab", "apiPath": "/api", "apiKey": imp.obbligatorio("TGARR_API_KEY")}
    esistente = next((i for i in p.get("indexer") if i.get("name") == NOME), None)
    if esistente is None:
        schemi = p.get("indexer/schema", "leggere l'elenco degli indexer", timeout=180)
        schema = next((s for s in schemi if s.get("name") == "Generic Newznab"), None) or next(
            s for s in schemi if s.get("implementation") == "Newznab"
        )
        profili = p.get("appprofile")
        etichette: list[int] = []
        dati = prepara_indexer(schema, NOME, campi, profili[0]["id"] if profili else 1, PRIORITA_TORZNAB, etichette)
        p.post("indexer", dati, "aggiungere Telegram (tgarr) come indexer", params={"forceSave": "true"})
    ok("Prowlarr: Telegram aggiunto come indexer (priorità massima), passa da solo a Radarr e Sonarr")

    porta = imp.get("TGARR_PORT", "8765")
    host = imp.get("NOME_HOST", "mediabox.local")
    esito.da_fare(
        f"Telegram: apri http://{host}:{porta}, entra con la password di amministrazione e collega il tuo "
        "account Telegram (QR o numero + codice). Poi iscrivi quell'account ai canali e ai gruppi privati "
        "che vuoi usare: tgarr li indicizza da solo."
    )
