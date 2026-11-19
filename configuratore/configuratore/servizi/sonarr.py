"""Configurazione di Sonarr (solo con --with-sonarr)."""

from __future__ import annotations

from ..impostazioni import Impostazioni
from ..messaggi import ok
from .arr import Arr, campi_qbittorrent

CARTELLA_SERIE = "/data/media/serie"

NOMI = {
    "renameEpisodes": True,
    "replaceIllegalCharacters": True,
    "standardEpisodeFormat": "{Series Title} - S{season:00}E{episode:00} - {Episode Title} {Quality Full}",
    "seriesFolderFormat": "{Series Title} ({Series Year}) [tvdbid-{TvdbId}]",
    "seasonFolderFormat": "Stagione {season:00}",
    "specialsFolderFormat": "Speciali",
}


def sonarr(imp: Impostazioni) -> Arr:
    return Arr("Sonarr", "http://sonarr:8989/sonarr", imp.obbligatorio("SONARR_API_KEY"))


def configura(imp: Impostazioni, esito) -> None:
    arr = sonarr(imp)
    arr.attendi()
    arr.assicura_root_folder(CARTELLA_SERIE)
    ok(f"Cartella delle serie: {CARTELLA_SERIE}")
    arr.aggiorna_config("naming", NOMI)
    arr.aggiorna_config("mediamanagement", {"copyUsingHardlinks": True, "deleteEmptyFolders": True})
    ok("Rinomina attiva, nomi compatibili con Jellyfin")
    arr.assicura_da_schema(
        "downloadclient",
        "QBittorrent",
        "qBittorrent",
        campi_qbittorrent(imp, "tvCategory", "sonarr"),
        extra={"removeCompletedDownloads": True, "removeFailedDownloads": True, "priority": 1},
    )
    ok("qBittorrent collegato (categoria \"sonarr\")")
