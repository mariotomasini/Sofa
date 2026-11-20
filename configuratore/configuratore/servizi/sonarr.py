"""Configurazione di Sonarr (solo con --with-sonarr)."""

from __future__ import annotations

from ..impostazioni import Impostazioni
from ..messaggi import ok
from .arr import Arr, campi_qbittorrent
from .formati import NOME_PROFILO, assicura_profilo, imposta_dimensioni, preferisci_usenet

CARTELLA_SERIE = "/data/media/serie"
CARTELLA_ANIME = "/data/media/anime"

NOMI = {
    "renameEpisodes": True,
    "replaceIllegalCharacters": True,
    "standardEpisodeFormat": "{Series Title} - S{season:00}E{episode:00} - {Episode Title} {Quality Full}",
    # Anime: anche il numero assoluto dell'episodio (es. One Piece 1071)
    "animeEpisodeFormat": "{Series Title} - S{season:00}E{episode:00} - {absolute:000} - {Episode Title} {Quality Full}",
    "seriesFolderFormat": "{Series Title} ({Series Year}) [tvdbid-{TvdbId}]",
    "seasonFolderFormat": "Stagione {season:00}",
    "specialsFolderFormat": "Speciali",
}


def qualita_ammessa(qualita: dict) -> bool:
    """SD, 720p e 1080p sì; 4K, remux e file grezzi no."""
    if qualita.get("source", "") not in {"television", "web", "webRip", "bluray", "dvd"}:
        return False
    return 0 < qualita.get("resolution", 0) <= 1080


def sonarr(imp: Impostazioni) -> Arr:
    return Arr("Sonarr", "http://sonarr:8989/sonarr", imp.obbligatorio("SONARR_API_KEY"))


def configura(imp: Impostazioni, esito) -> None:
    arr = sonarr(imp)
    arr.attendi()
    arr.assicura_root_folder(CARTELLA_SERIE)
    ok(f"Cartella delle serie: {CARTELLA_SERIE}")
    if imp.anime:
        arr.assicura_root_folder(CARTELLA_ANIME)
        ok(f"Cartella degli anime: {CARTELLA_ANIME} (quando aggiungi un anime scegli questa cartella e tipo \"Anime\")")
    arr.aggiorna_config("naming", NOMI)
    # "doNotPrefer": una PROPER inglese non deve passare davanti a una release ITA
    arr.aggiorna_config(
        "mediamanagement",
        {"copyUsingHardlinks": True, "deleteEmptyFolders": True, "downloadPropersAndRepacks": "doNotPrefer"},
    )
    ok("Rinomina attiva, nomi compatibili con Jellyfin")
    arr.assicura_da_schema(
        "downloadclient",
        "QBittorrent",
        "qBittorrent",
        campi_qbittorrent(imp, "tvCategory", "sonarr"),
        extra={"removeCompletedDownloads": True, "removeFailedDownloads": True, "priority": 1},
    )
    ok("qBittorrent collegato (categoria \"sonarr\")")
    profilo = assicura_profilo(arr, qualita_ammessa)
    da_spostare = [x["id"] for x in arr.get("series") if x.get("qualityProfileId") != profilo]
    if da_spostare:
        arr.put("series/editor", {"seriesIds": da_spostare, "qualityProfileId": profilo}, "aggiornare le serie seguite")
    ok(f"Profilo qualità \"{NOME_PROFILO}\": audio italiano prima di tutto, poi 1080p, 720p, SD")
    max_gb = imp.dimensione_max_gb
    imposta_dimensioni(arr, max_gb)
    ok(f"Dimensione: film 1080p di 2 ore al massimo {max_gb:g} GB, preferiti intorno a {max_gb / 2:g} GB")
    if imp.tgarr:
        preferisci_usenet(arr)
        ok("A parità di lingua e qualità vince Telegram (tgarr)")
