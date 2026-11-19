"""Configurazione di Lidarr (musica, solo con WITH_LIDARR=si)."""

from __future__ import annotations

from ..impostazioni import Impostazioni
from ..messaggi import ok
from .arr import Arr, campi_qbittorrent

CARTELLA_MUSICA = "/data/media/musica"

# Artista/Album (Anno)/NN - Brano: la struttura che Jellyfin legge meglio
NOMI = {
    "renameTracks": True,
    "replaceIllegalCharacters": True,
    "standardTrackFormat": "{Album Title} ({Release Year})/{track:00} - {Track Title}",
    "multiDiscTrackFormat": "{Album Title} ({Release Year})/{Medium Format} {medium:00}/{track:00} - {Track Title}",
    "artistFolderFormat": "{Artist Name}",
}


def lidarr(imp: Impostazioni) -> Arr:
    return Arr("Lidarr", "http://lidarr:8686/lidarr", imp.obbligatorio("LIDARR_API_KEY"), versione="v1")


def assicura_root_folder(arr: Arr) -> None:
    if any(r.get("path", "").rstrip("/") == CARTELLA_MUSICA for r in arr.get("rootfolder")):
        return
    qualita = arr.get("qualityprofile")
    metadati = arr.get("metadataprofile")
    profilo_q = next((p for p in qualita if p["name"] == "Any"), qualita[0])
    profilo_m = next((p for p in metadati if p["name"] == "Standard"), metadati[0])
    arr.post(
        "rootfolder",
        {
            "name": "Musica",
            "path": CARTELLA_MUSICA,
            "defaultQualityProfileId": profilo_q["id"],
            "defaultMetadataProfileId": profilo_m["id"],
            "defaultMonitorOption": "all",
            "defaultNewItemMonitorOption": "all",
            "defaultTags": [],
        },
        f"aggiungere la cartella {CARTELLA_MUSICA}",
    )


def configura(imp: Impostazioni, esito) -> None:
    arr = lidarr(imp)
    arr.attendi()
    assicura_root_folder(arr)
    ok(f"Cartella della musica: {CARTELLA_MUSICA}")
    arr.aggiorna_config("naming", NOMI)
    arr.aggiorna_config("mediamanagement", {"copyUsingHardlinks": True, "deleteEmptyFolders": True})
    ok("Rinomina attiva: Artista/Album (Anno)/NN - Brano")
    arr.assicura_da_schema(
        "downloadclient",
        "QBittorrent",
        "qBittorrent",
        campi_qbittorrent(imp, "musicCategory", "lidarr"),
        extra={"removeCompletedDownloads": True, "removeFailedDownloads": True, "priority": 1},
    )
    ok("qBittorrent collegato (categoria \"lidarr\")")
