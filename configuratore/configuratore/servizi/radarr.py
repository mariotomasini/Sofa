"""Configurazione di Radarr: cartella film, nomi, qBittorrent, qualità, italiano."""

from __future__ import annotations

from ..impostazioni import Impostazioni
from ..messaggi import ok
from .arr import Arr, campi_qbittorrent
from .formati import NOME_PROFILO, assicura_profilo

CARTELLA_FILM = "/data/media/film"

# Nomi compatibili con Jellyfin: la cartella contiene l'id TMDB, così
# Jellyfin riconosce sempre il film giusto.
NOMI = {
    "renameMovies": True,
    "replaceIllegalCharacters": True,
    "colonReplacementFormat": "dash",
    "standardMovieFormat": "{Movie CleanTitle} ({Release Year}) {Quality Full}",
    "movieFolderFormat": "{Movie CleanTitle} ({Release Year}) [tmdbid-{TmdbId}]",
}


def radarr(imp: Impostazioni) -> Arr:
    return Arr("Radarr", "http://radarr:7878/radarr", imp.obbligatorio("RADARR_API_KEY"))


def qualita_ammessa(qualita: dict) -> bool:
    """SD, 720p e 1080p sì; 4K, remux, dischi interi e copie dal cinema no."""
    risoluzione = qualita.get("resolution", 0)
    sorgente = qualita.get("source", "")
    modificatore = qualita.get("modifier", "")
    if modificatore in {"remux", "brdisk", "rawhd", "screener", "regional"}:
        return False
    if sorgente in {"cam", "telesync", "telecine", "workprint", "unknown"}:
        return False
    if sorgente == "dvd":
        return True
    return 0 < risoluzione <= 1080


def configura(imp: Impostazioni, esito) -> None:
    arr = radarr(imp)
    arr.attendi()

    italiano = arr.id_lingua("Italian")
    if italiano is not None:
        arr.aggiorna_config("ui", {"movieInfoLanguage": italiano, "uiLanguage": italiano, "firstDayOfWeek": 1})
    ok("Lingua italiana per titoli e trame")

    arr.assicura_root_folder(CARTELLA_FILM)
    ok(f"Cartella dei film: {CARTELLA_FILM}")

    arr.aggiorna_config("naming", NOMI)
    arr.aggiorna_config(
        "mediamanagement",
        {
            "copyUsingHardlinks": True,
            "importExtraFiles": True,
            "extraFileExtensions": "srt,sub,idx,ass,ssa",
            "deleteEmptyFolders": True,
            # una PROPER inglese non deve passare davanti a una release ITA
            "downloadPropersAndRepacks": "doNotPrefer",
        },
    )
    ok("Rinomina attiva, nomi compatibili con Jellyfin")

    arr.assicura_da_schema(
        "downloadclient",
        "QBittorrent",
        "qBittorrent",
        campi_qbittorrent(imp, "movieCategory", "radarr"),
        extra={"removeCompletedDownloads": True, "removeFailedDownloads": True, "priority": 1},
    )
    ok("qBittorrent collegato (categoria \"radarr\")")

    qualsiasi = arr.id_lingua("Any")
    profilo = assicura_profilo(arr, qualita_ammessa, {"id": qualsiasi, "name": "Any"} if qualsiasi is not None else None)
    da_spostare = [m["id"] for m in arr.get("movie") if m.get("qualityProfileId") != profilo]
    if da_spostare:
        arr.put("movie/editor", {"movieIds": da_spostare, "qualityProfileId": profilo}, "aggiornare i film")
    ok(f"Profilo qualità \"{NOME_PROFILO}\": audio italiano prima di tutto, poi 1080p, 720p, SD")
    ok("Se non c'è l'italiano scarica la versione migliore e la sostituisce appena esce quella ITA")
