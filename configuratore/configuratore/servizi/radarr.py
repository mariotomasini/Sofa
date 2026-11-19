"""Configurazione di Radarr: cartella film, nomi, qBittorrent, qualità, italiano."""

from __future__ import annotations

from ..impostazioni import Impostazioni
from ..messaggi import ok
from .arr import Arr, campi_qbittorrent

CARTELLA_FILM = "/data/media/film"
NOME_PROFILO = "Migliore disponibile"

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


def costruisci_profilo(schema: dict, id_lingua: int | None) -> dict:
    profilo = dict(schema)
    cutoff = None
    for elemento in profilo["items"]:
        if elemento.get("quality"):
            elemento["allowed"] = qualita_ammessa(elemento["quality"])
            if elemento["quality"].get("name") == "Bluray-1080p":
                cutoff = elemento["quality"]["id"]
        else:
            ammessi = [qualita_ammessa(i["quality"]) for i in elemento.get("items", [])]
            elemento["allowed"] = bool(ammessi) and all(ammessi)
            for i in elemento.get("items", []):
                i["allowed"] = elemento["allowed"]
    if cutoff is None:
        ammessi = [e for e in profilo["items"] if e.get("allowed")]
        ultimo = ammessi[-1]
        cutoff = ultimo["quality"]["id"] if ultimo.get("quality") else ultimo["id"]
    profilo.update({"name": NOME_PROFILO, "upgradeAllowed": True, "cutoff": cutoff})
    if id_lingua is not None and "language" in profilo:
        profilo["language"] = {"id": id_lingua, "name": "Any"}
    return profilo


def assicura_profilo(arr: Arr) -> int:
    esistente = next((p for p in arr.get("qualityprofile") if p["name"] == NOME_PROFILO), None)
    if esistente:
        return esistente["id"]
    profilo = costruisci_profilo(arr.get("qualityprofile/schema"), arr.id_lingua("Any"))
    creato = arr.post("qualityprofile", profilo, "creare il profilo qualità")
    return creato["id"]


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

    assicura_profilo(arr)
    ok(f"Profilo qualità \"{NOME_PROFILO}\" (preferisce 1080p, accetta 720p e SD)")
