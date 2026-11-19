"""Configurazione di Prowlarr: collegamento a Radarr/Sonarr e a qBittorrent."""

from __future__ import annotations

from ..impostazioni import Impostazioni
from ..messaggi import info, ok
from .arr import Arr, campi_qbittorrent

INDIRIZZO = "http://prowlarr:9696/prowlarr"


def prowlarr(imp: Impostazioni) -> Arr:
    return Arr("Prowlarr", INDIRIZZO, imp.obbligatorio("PROWLARR_API_KEY"), versione="v1")


def configura(imp: Impostazioni, esito) -> None:
    arr = prowlarr(imp)
    arr.attendi()
    arr.aggiorna_config("ui", {"uiLanguage": imp.get("LINGUA", "it"), "firstDayOfWeek": 1})

    app = [("Radarr", "http://radarr:7878/radarr", imp.obbligatorio("RADARR_API_KEY"))]
    if imp.sonarr:
        app.append(("Sonarr", "http://sonarr:8989/sonarr", imp.obbligatorio("SONARR_API_KEY")))
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

    indexer = arr.get("indexer")
    if indexer:
        info(f"Indexer configurati: {len(indexer)}")
    else:
        esito.da_fare(
            "Aggiungi almeno un indexer in Prowlarr (pagina /prowlarr → Indexer → Aggiungi): "
            "senza indexer Cerca mostra solo TMDB e Internet Archive."
        )
