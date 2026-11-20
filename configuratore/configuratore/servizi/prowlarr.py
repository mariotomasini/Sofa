"""Configurazione di Prowlarr: indexer, collegamento a Radarr/Sonarr e a qBittorrent."""

from __future__ import annotations

import copy
import re
from urllib.parse import urlsplit, urlunsplit

from ..impostazioni import Impostazioni
from ..messaggi import ErroreMediabox, info, ok
from .arr import Arr, campi_qbittorrent

INDIRIZZO = "http://prowlarr:9696/prowlarr"


def prowlarr(imp: Impostazioni) -> Arr:
    return Arr("Prowlarr", INDIRIZZO, imp.obbligatorio("PROWLARR_API_KEY"), versione="v1")


def collega_flaresolverr(arr: Arr) -> None:
    """FlareSolverr si usa solo per gli indexer con l'etichetta "flaresolverr"."""
    etichette = {t["label"]: t["id"] for t in arr.get("tag")}
    if "flaresolverr" not in etichette:
        etichette["flaresolverr"] = arr.post("tag", {"label": "flaresolverr"}, "creare l'etichetta flaresolverr")["id"]
    arr.assicura_da_schema(
        "indexerProxy",
        "FlareSolverr",
        "FlareSolverr",
        {"host": "http://flaresolverr:8191/", "requestTimeout": 60},
        extra={"tags": [etichette["flaresolverr"]]},
    )
    ok("FlareSolverr pronto: usalo con l'etichetta \"flaresolverr\" sugli indexer protetti da Cloudflare")


# Priorità in Prowlarr (passa anche a Radarr/Sonarr): numero più basso = preferito
PRIORITA_TORZNAB = 1
PRIORITA_NORMALE = 25


def _semplice(nome: str) -> str:
    return re.sub(r"[^a-z0-9]", "", nome.lower())


def leggi_indexer(testo: str) -> list[tuple[str, dict[str, str]]]:
    """PROWLARR_INDEXER: "Nome, Altro|apikey=xxx|username=yyy" → [(nome, campi)]."""
    elenco = []
    for voce in re.split(r"[,\n]", testo):
        parti = [p.strip() for p in voce.split("|")]
        if not parti[0]:
            continue
        campi = dict(p.split("=", 1) for p in parti[1:] if "=" in p)
        elenco.append((parti[0], {k.strip(): v.strip() for k, v in campi.items()}))
    return elenco


def leggi_torznab(testo: str) -> list[tuple[str, str, str, str]]:
    """INDEXER_TORZNAB: "Nome|https://host/percorso/api|chiave; …" → [(nome, base, apiPath, chiave)]."""
    elenco = []
    for voce in re.split(r"[;\n]", testo):
        parti = [p.strip() for p in voce.split("|")]
        if len(parti) < 2 or not parti[0] or not parti[1]:
            continue
        url = urlsplit(parti[1])
        percorso = url.path.rstrip("/")
        api = "/api"
        if percorso.endswith("/api"):
            percorso = percorso[: -len("/api")]
        base = urlunsplit((url.scheme, url.netloc, percorso, "", ""))
        if url.query:
            api += "?" + url.query
        elenco.append((parti[0], base, api, parti[2] if len(parti) > 2 else ""))
    return elenco


def prepara_indexer(schema: dict, nome: str, campi: dict, profilo_app: int, priorita: int, etichette: list[int]) -> dict:
    dati = copy.deepcopy(schema)
    dati.pop("presets", None)
    dati.update({"name": nome, "enable": True, "appProfileId": profilo_app, "priority": priorita, "tags": etichette})
    for campo in dati.get("fields", []):
        if campo["name"] in campi:
            campo["value"] = campi[campo["name"]]
        elif campo["name"] == "baseUrl" and not campo.get("value") and dati.get("indexerUrls"):
            campo["value"] = dati["indexerUrls"][0]
    return dati


def trova_schema(schemi: list[dict], nome: str) -> dict | None:
    cercato = _semplice(nome)
    for s in schemi:
        if cercato in {_semplice(s.get("definitionName") or ""), _semplice(s.get("name") or "")}:
            return s
    return None


def aggiungi_indexer(arr: Arr, imp: Impostazioni, esito) -> None:
    """Aggiunge da soli gli indexer scritti nel file .env (quelli già presenti non si toccano)."""
    voci = leggi_indexer(imp.get("PROWLARR_INDEXER"))
    torznab = leggi_torznab(imp.get("INDEXER_TORZNAB"))
    if not voci and not torznab:
        return
    presenti = {_semplice(i["name"]) for i in arr.get("indexer")}
    profili = arr.get("appprofile")
    profilo_app = profili[0]["id"] if profili else 1
    etichette = [t["id"] for t in arr.get("tag") if t["label"] == "flaresolverr"]
    schemi = arr.get("indexer/schema", "leggere l'elenco degli indexer", timeout=180)

    da_aggiungere = []
    for nome, base, api, chiave in torznab:
        schema = next((s for s in schemi if s.get("implementation") == "Torznab"), None)
        if schema:
            campi = {"baseUrl": base, "apiPath": api, "apiKey": chiave}
            da_aggiungere.append((nome, prepara_indexer(schema, nome, campi, profilo_app, PRIORITA_TORZNAB, etichette)))
    for nome, campi in voci:
        schema = trova_schema(schemi, nome)
        if schema is None:
            esito.da_fare(f"Prowlarr non conosce l'indexer \"{nome}\": controlla il nome in Prowlarr → Indexer → Aggiungi.")
            continue
        nome = schema.get("name") or nome
        da_aggiungere.append((nome, prepara_indexer(schema, nome, campi, profilo_app, PRIORITA_NORMALE, etichette)))

    for nome, dati in da_aggiungere:
        if _semplice(nome) in presenti:
            continue
        try:
            arr.post("indexer", dati, f"aggiungere l'indexer {nome}")
            presenti.add(_semplice(nome))
            ok(f"Indexer aggiunto: {nome}")
        except ErroreMediabox as e:
            esito.da_fare(
                f"Indexer \"{nome}\" non aggiunto: Prowlarr non riesce a usarlo ({e.cosa[:160]}). "
                "Se serve un account, scrivi i dati nel file .env (es. Nome|apikey=…) e riesegui \"mediabox configura\"."
            )


def configura(imp: Impostazioni, esito) -> None:
    arr = prowlarr(imp)
    arr.attendi()
    arr.aggiorna_config("ui", {"uiLanguage": imp.get("LINGUA", "it"), "firstDayOfWeek": 1})

    app = [("Radarr", "http://radarr:7878/radarr", imp.obbligatorio("RADARR_API_KEY"))]
    if imp.sonarr:
        app.append(("Sonarr", "http://sonarr:8989/sonarr", imp.obbligatorio("SONARR_API_KEY")))
    if imp.lidarr:
        app.append(("Lidarr", "http://lidarr:8686/lidarr", imp.obbligatorio("LIDARR_API_KEY")))
    for nome, indirizzo, chiave in app:
        arr.assicura_da_schema(
            "applications",
            nome,
            nome,
            {"prowlarrUrl": INDIRIZZO, "baseUrl": indirizzo, "apiKey": chiave},
            extra={"syncLevel": "fullSync"},
        )
        ok(f"Collegato a {nome} (sincronizzazione automatica degli indexer)")

    # Utile per chi avvia un download direttamente dall'interfaccia di Prowlarr.
    arr.assicura_da_schema(
        "downloadclient",
        "QBittorrent",
        "qBittorrent",
        campi_qbittorrent(imp, "category", "manuale"),
        extra={"priority": 1},
    )
    ok("qBittorrent collegato (categoria \"manuale\")")

    if imp.flaresolverr:
        collega_flaresolverr(arr)

    aggiungi_indexer(arr, imp, esito)

    indexer = arr.get("indexer")
    if indexer:
        info(f"Indexer configurati: {len(indexer)}")
    else:
        esito.da_fare(
            "Aggiungi almeno un indexer in Prowlarr (pagina /prowlarr → Indexer → Aggiungi): "
            "senza indexer Cerca mostra solo TMDB e Internet Archive."
        )
