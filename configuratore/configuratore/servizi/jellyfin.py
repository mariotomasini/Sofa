"""Configurazione di Jellyfin via API.

Passi: wizard iniziale, lingua e paese, base URL /jellyfin, librerie,
transcodifica VAAPI, plugin Intro Skipper, TV in diretta, chiave API per
Radarr e Cerca.
"""

from __future__ import annotations

import time

import httpx

from ..impostazioni import Impostazioni
from ..messaggi import ErroreMediabox, avviso, info, ok
from ..rete import controlla

INDIRIZZO = "http://jellyfin:8096"
BASE_URL = "/jellyfin"
NOME_APP = "mediabox"

LIBRERIE = [
    # nome, tipo di raccolta (None = contenuti misti), cartella dentro Jellyfin
    ("Film", "movies", "/data/film"),
    ("Serie", "tvshows", "/data/serie"),
    ("Musica", "music", "/data/musica"),
    ("Registrazioni", None, "/data/registrazioni"),
]
LIBRERIA_ANIME = ("Anime", "tvshows", "/data/anime")


def librerie(imp: Impostazioni) -> list[tuple[str, str | None, str]]:
    elenco = list(LIBRERIE)
    if imp.anime:
        elenco.insert(2, LIBRERIA_ANIME)
    return elenco

CODEC_HW = ["h264", "hevc", "mpeg2video", "vc1", "vp8", "vp9", "av1"]


def _intestazione(token: str = "") -> dict[str, str]:
    valore = (
        f'MediaBrowser Client="{NOME_APP}", Device="configuratore", '
        f'DeviceId="mediabox-configuratore", Version="1.0"'
    )
    if token:
        valore += f', Token="{token}"'
    return {"Authorization": valore}


class Jellyfin:
    def __init__(self, indirizzo: str = INDIRIZZO) -> None:
        self.indirizzo = indirizzo
        self.prefisso = ""
        self.token = ""

    # --- connessione -------------------------------------------------------
    def attendi(self, timeout: float = 600) -> dict:
        """Attende Jellyfin sull'indirizzo /jellyfin.

        Durante l'avvio Jellyfin può rispondere per qualche secondo anche senza
        /jellyfin: in quel caso continuiamo ad aspettare."""
        scadenza = time.monotonic() + timeout
        annunciato = False
        senza_prefisso: dict | None = None
        while time.monotonic() < scadenza:
            for prefisso in (BASE_URL, ""):
                try:
                    r = httpx.get(f"{self.indirizzo}{prefisso}/System/Info/Public", timeout=10)
                except httpx.HTTPError:
                    continue
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                    if prefisso == BASE_URL:
                        self.prefisso = prefisso
                        return r.json()
                    senza_prefisso = r.json()
            if not annunciato:
                info("Attendo che Jellyfin sia pronto…")
                annunciato = True
            time.sleep(3)
        if senza_prefisso is not None:
            self.prefisso = ""
            return senza_prefisso
        raise ErroreMediabox(
            "Jellyfin non si è avviato in tempo.",
            "Guarda i messaggi con \"mediabox logs jellyfin\", poi riprova con \"mediabox configura\".",
        )

    def _url(self, percorso: str) -> str:
        return f"{self.indirizzo}{self.prefisso}{percorso}"

    def richiesta(self, metodo: str, percorso: str, cosa: str, **kwargs) -> httpx.Response:
        kwargs.setdefault("timeout", 60)
        r = httpx.request(metodo, self._url(percorso), headers=_intestazione(self.token), **kwargs)
        return controlla(r, cosa)

    def get(self, percorso: str, cosa: str, **kw) -> httpx.Response:
        return self.richiesta("GET", percorso, cosa, **kw)

    def post(self, percorso: str, cosa: str, **kw) -> httpx.Response:
        return self.richiesta("POST", percorso, cosa, **kw)

    def accedi(self, utente: str, password: str) -> None:
        r = httpx.post(
            self._url("/Users/AuthenticateByName"),
            json={"Username": utente, "Pw": password},
            headers=_intestazione(),
            timeout=30,
        )
        if r.status_code == 401:
            raise ErroreMediabox(
                f"Jellyfin non accetta utente \"{utente}\" con la password del file .env.",
                "Se hai cambiato la password da Jellyfin, rimetti la stessa in ADMIN_PASSWORD nel file .env "
                "e riesegui \"mediabox configura\".",
            )
        controlla(r, "accedere a Jellyfin")
        self.token = r.json()["AccessToken"]

    def riavvia(self) -> None:
        info("Riavvio Jellyfin per applicare le modifiche…")
        try:
            self.post("/System/Restart", "riavviare Jellyfin")
        except (httpx.HTTPError, ErroreMediabox):
            pass
        time.sleep(10)
        self.attendi()


# --- passi -------------------------------------------------------------------

def wizard_iniziale(jf: Jellyfin, imp: Impostazioni) -> None:
    lingua = imp.get("LINGUA", "it")
    paese = imp.get("PAESE", "IT")
    jf.post(
        "/Startup/Configuration",
        "impostare la lingua di Jellyfin",
        json={
            "ServerName": "mediabox",
            "UICulture": lingua,
            "MetadataCountryCode": paese,
            "PreferredMetadataLanguage": lingua,
        },
    )
    # Jellyfin 12 risponde a /health prima di aver creato l'utente iniziale:
    # finché GET /Startup/User non dà 200, il POST successivo può fallire con 500.
    for _ in range(40):
        try:
            if httpx.get(jf._url("/Startup/User"), headers=_intestazione(), timeout=10).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1.5)
    r = httpx.post(
        jf._url("/Startup/User"),
        json={"Name": imp.utente, "Password": imp.password},
        headers=_intestazione(),
        timeout=30,
    )
    # 403 = l'utente ha già una password (wizard interrotto a metà): va bene,
    # l'accesso con la password del .env lo verifica subito dopo.
    if r.status_code != 403:
        controlla(r, "creare l'utente di Jellyfin")
    jf.post(
        "/Startup/RemoteAccess",
        "impostare l'accesso remoto di Jellyfin",
        json={"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False},
    )
    jf.post("/Startup/Complete", "completare il wizard di Jellyfin")
    ok(f"Wizard iniziale completato (utente \"{imp.utente}\")")


def assicura_amministratore(jf: Jellyfin) -> None:
    """Alcune versioni lasciano l'utente del wizard senza diritti di amministratore."""
    io = jf.get("/Users/Me", "leggere l'utente di Jellyfin").json()
    politica = io.get("Policy") or {}
    if politica.get("IsAdministrator"):
        return
    politica["IsAdministrator"] = True
    jf.post(f"/Users/{io['Id']}/Policy", "rendere amministratore l'utente di Jellyfin", json=politica)
    ok("Utente promosso ad amministratore")


def imposta_sistema(jf: Jellyfin, imp: Impostazioni) -> None:
    lingua = imp.get("LINGUA", "it")
    conf = jf.get("/System/Configuration", "leggere la configurazione di Jellyfin").json()
    conf.update({
        "UICulture": lingua,
        "MetadataCountryCode": imp.get("PAESE", "IT"),
        "PreferredMetadataLanguage": lingua,
        "ServerName": "mediabox",
        "QuickConnectAvailable": True,
    })
    jf.post("/System/Configuration", "salvare la configurazione di Jellyfin", json=conf)
    ok("Lingua italiana e paese Italia")


def controlla_base_url(jf: Jellyfin) -> None:
    """Il base URL /jellyfin lo scrive "prepara" prima dell'avvio (Jellyfin lo
    applica solo ripartendo). Qui verifichiamo solo che sia attivo."""
    if jf.prefisso != BASE_URL:
        raise ErroreMediabox(
            "Jellyfin non risponde ancora sull'indirizzo /jellyfin.",
            "Esegui \"mediabox restart\" e poi \"mediabox configura\".",
        )


def crea_librerie(jf: Jellyfin, imp: Impostazioni) -> None:
    lingua = imp.get("LINGUA", "it")
    paese = imp.get("PAESE", "IT")
    esistenti = {
        lib["Name"]: lib for lib in jf.get("/Library/VirtualFolders", "leggere le librerie").json()
    }
    for nome, tipo, cartella in librerie(imp):
        if nome in esistenti:
            info(f"Libreria \"{nome}\" già presente")
            if nome == "Film":
                attiva_saghe(jf, esistenti[nome])
            continue
        parametri = {"name": nome, "refreshLibrary": "false"}
        if tipo:
            parametri["collectionType"] = tipo
        corpo = {
            "LibraryOptions": {
                "PreferredMetadataLanguage": lingua,
                "MetadataCountryCode": paese,
                "EnableRealtimeMonitor": True,
                "EnableChapterImageExtraction": False,
                "SaveLocalMetadata": False,
                # Film della stessa saga (es. tutti i "Rocky") raggruppati da soli
                "AutomaticallyAddToCollection": tipo == "movies",
                "PathInfos": [{"Path": cartella}],
            }
        }
        jf.post("/Library/VirtualFolders", f"creare la libreria {nome}", params=parametri, json=corpo)
        ok(f"Libreria \"{nome}\" → {cartella}")
    try:
        jf.post("/Library/Refresh", "aggiornare le librerie")
    except ErroreMediabox:
        pass


def attiva_saghe(jf: Jellyfin, libreria: dict) -> None:
    """Per le installazioni già fatte: attiva le saghe automatiche nella libreria Film."""
    opzioni = libreria.get("LibraryOptions") or {}
    if opzioni.get("AutomaticallyAddToCollection"):
        return
    opzioni["AutomaticallyAddToCollection"] = True
    jf.post(
        "/Library/VirtualFolders/LibraryOptions",
        "attivare le saghe automatiche",
        json={"Id": libreria["ItemId"], "LibraryOptions": opzioni},
    )
    ok("Saghe automatiche attive nella libreria Film")


def imposta_transcodifica(jf: Jellyfin, imp: Impostazioni) -> None:
    conf = jf.get("/System/Configuration/encoding", "leggere la transcodifica").json()
    if imp.vaapi:
        conf.update({
            "HardwareAccelerationType": "vaapi",
            "VaapiDevice": "/dev/dri/renderD128",
            "EnableHardwareEncoding": True,
            "HardwareDecodingCodecs": CODEC_HW,
            "EnableDecodingColorDepth10Hevc": True,
            "EnableDecodingColorDepth10Vp9": True,
            "EnableTonemapping": True,
        })
        messaggio = "Transcodifica hardware VAAPI attiva (/dev/dri/renderD128)"
    else:
        conf["HardwareAccelerationType"] = "none"
        messaggio = "Transcodifica solo con il processore (nessuna scheda grafica Intel disponibile)"
    jf.post("/System/Configuration/encoding", "salvare la transcodifica", json=conf)
    ok(messaggio)


def installa_intro_skipper(jf: Jellyfin, imp: Impostazioni) -> bool:
    """Installa il plugin Intro Skipper. Restituisce True se serve un riavvio."""
    installati = jf.get("/Plugins", "leggere i plugin").json()
    if any("intro" in p.get("Name", "").lower() and "skip" in p.get("Name", "").lower() for p in installati):
        info("Intro Skipper già installato")
        return False
    url_repo = imp.get("INTRO_SKIPPER_REPO")
    repo = jf.get("/Repositories", "leggere i repository dei plugin").json()
    if not any(r.get("Url") == url_repo for r in repo):
        repo.append({"Name": "Intro Skipper", "Url": url_repo, "Enabled": True})
        jf.post("/Repositories", "aggiungere il repository di Intro Skipper", json=repo)
    try:
        pacchetti = jf.get("/Packages", "leggere il catalogo dei plugin", timeout=120).json()
    except ErroreMediabox:
        pacchetti = []
    pacchetto = next(
        (p for p in pacchetti if "intro" in p.get("name", "").lower() and "skip" in p.get("name", "").lower()),
        None,
    )
    if not pacchetto:
        avviso(
            "Non riesco a scaricare il plugin Intro Skipper (serve Internet). "
            "Puoi installarlo dopo da Jellyfin: Pannello di controllo → Plugin → Catalogo."
        )
        return False
    jf.post(
        f"/Packages/Installed/{pacchetto['name']}",
        "installare Intro Skipper",
        params={"assemblyGuid": pacchetto.get("guid", ""), "repositoryUrl": url_repo},
    )
    ok("Plugin Intro Skipper installato")
    return True


def imposta_tv(jf: Jellyfin, imp: Impostazioni, esito) -> None:
    """Collega a Jellyfin l'HDHomeRun e/o TVHeadend, se presenti."""
    conf = jf.get("/System/Configuration/livetv", "leggere la TV in diretta").json()
    conf.update({
        "RecordingPath": "/data/registrazioni",
        "MovieRecordingPath": "/data/registrazioni/Film",
        "SeriesRecordingPath": "/data/registrazioni/Serie",
        "EnableRecordingSubfolders": True,
    })
    jf.post("/System/Configuration/livetv", "impostare le cartelle delle registrazioni", json=conf)
    sintonizzatori = conf.get("TunerHosts", []) or []
    guide = conf.get("ListingProviders", []) or []
    url_presenti = {t.get("Url") for t in sintonizzatori}
    guide_presenti = {g.get("Path") for g in guide}

    def aggiungi_tuner(corpo: dict, descrizione: str) -> None:
        if corpo["Url"] in url_presenti:
            info(f"{descrizione} già collegato")
            return
        jf.post("/LiveTv/TunerHosts", f"collegare {descrizione}", json=corpo)
        ok(f"{descrizione} collegato a Jellyfin")

    def aggiungi_guida(percorso: str, descrizione: str) -> None:
        if percorso in guide_presenti:
            return
        jf.post(
            "/LiveTv/ListingProviders",
            f"aggiungere la guida TV ({descrizione})",
            json={"Type": "xmltv", "Path": percorso, "EnableAllTuners": True},
        )
        ok(f"Guida TV collegata ({descrizione})")

    if imp.hdhomerun:
        aggiungi_tuner(
            {
                "Type": "hdhomerun",
                "Url": f"http://{imp.hdhomerun}",
                "FriendlyName": "HDHomeRun",
                "ImportFavoritesOnly": False,
                "AllowHWTranscoding": True,
                "EnableStreamLooping": False,
                "TunerCount": 0,
            },
            f"HDHomeRun ({imp.hdhomerun})",
        )
        if imp.get("EPG_XMLTV_URL"):
            aggiungi_guida(imp.get("EPG_XMLTV_URL"), "XMLTV")
        else:
            esito.da_fare(
                "Guida TV per l'HDHomeRun: Jellyfin non legge la guida dall'antenna. "
                "Metti l'indirizzo di una guida XMLTV in EPG_XMLTV_URL nel file .env (vedi README)."
            )
    if imp.dvb:
        aggiungi_tuner(
            {
                "Type": "m3u",
                "Url": "http://tvheadend:9981/tv/playlist/channels?profile=pass",
                "FriendlyName": "TVHeadend",
                "ImportFavoritesOnly": False,
                "AllowHWTranscoding": True,
                "TunerCount": 0,
            },
            "TVHeadend",
        )
        aggiungi_guida("http://tvheadend:9981/tv/xmltv/channels", "TVHeadend")
    if imp.hdhomerun or imp.dvb:
        try:
            jf.post("/ScheduledTasks/Running/" + _id_task_guida(jf), "aggiornare la guida TV")
        except (ErroreMediabox, StopIteration):
            pass


def _id_task_guida(jf: Jellyfin) -> str:
    compiti = jf.get("/ScheduledTasks", "leggere le attività pianificate").json()
    return next(t["Id"] for t in compiti if t.get("Key") == "RefreshGuide")


def chiave_api(jf: Jellyfin, nome: str = NOME_APP) -> str:
    """Restituisce (creandola se serve) una chiave API per Radarr e Cerca."""
    def cerca() -> str:
        chiavi = jf.get("/Auth/Keys", "leggere le chiavi API").json().get("Items", [])
        return next((k["AccessToken"] for k in chiavi if k.get("AppName") == nome), "")

    chiave = cerca()
    if not chiave:
        jf.post("/Auth/Keys", "creare la chiave API", params={"app": nome})
        chiave = cerca()
    return chiave


def collega_radarr(imp: Impostazioni, chiave: str, nome_servizio: str = "radarr") -> None:
    """Fa sì che Radarr/Sonarr avvisino Jellyfin quando arriva un file nuovo."""
    from .arr import Arr

    dentro = "/data/media/"
    if nome_servizio == "radarr":
        arr = Arr("Radarr", "http://radarr:7878/radarr", imp.obbligatorio("RADARR_API_KEY"))
    elif nome_servizio == "sonarr":
        arr = Arr("Sonarr", "http://sonarr:8989/sonarr", imp.obbligatorio("SONARR_API_KEY"))
    else:
        arr = Arr("Lidarr", "http://lidarr:8686/lidarr", imp.obbligatorio("LIDARR_API_KEY"), versione="v1")
    arr.assicura_da_schema(
        "notification",
        "MediaBrowser",
        "Jellyfin",
        {
            "host": "jellyfin",
            "port": 8096,
            "useSsl": False,
            "urlBase": BASE_URL,
            "apiKey": chiave,
            "notify": False,
            "updateLibrary": True,
            # Radarr vede /data/media/film, Jellyfin vede /data/film
            "mapFrom": dentro,
            "mapTo": "/data/",
        },
        extra={
            "onDownload": True,
            "onUpgrade": True,
            "onRename": True,
            "onMovieDelete": True,
            "onMovieFileDelete": True,
            "onSeriesDelete": True,
            "onEpisodeFileDelete": True,
            "onReleaseImport": True,
            "onTrackRetag": True,
        },
    )
    ok(f"{arr.nome} avvisa Jellyfin quando arriva qualcosa di nuovo")


def configura(imp: Impostazioni, esito) -> None:
    jf = Jellyfin()
    stato = jf.attendi()
    controlla_base_url(jf)
    if not stato.get("StartupWizardCompleted"):
        wizard_iniziale(jf, imp)
    else:
        info("Wizard iniziale già completato")
    jf.accedi(imp.utente, imp.password)
    assicura_amministratore(jf)
    imposta_sistema(jf, imp)
    ok("Jellyfin risponde su /jellyfin")
    crea_librerie(jf, imp)
    imposta_transcodifica(jf, imp)
    if not imp.vaapi:
        if imp.windows:
            esito.nota("Su Windows Jellyfin usa solo il processore per la transcodifica (limite di Docker Desktop).")
        else:
            esito.nota("Nessuna scheda grafica Intel trovata: la transcodifica usa il processore.")
    if imp.hdhomerun or imp.dvb:
        imposta_tv(jf, imp, esito)
    chiave = chiave_api(jf)
    collega_radarr(imp, chiave, "radarr")
    if imp.sonarr:
        collega_radarr(imp, chiave, "sonarr")
    if imp.lidarr:
        collega_radarr(imp, chiave, "lidarr")
    try:
        if installa_intro_skipper(jf, imp):
            jf.riavvia()
    except ErroreMediabox as exc:
        avviso(f"Intro Skipper non installato: {exc.cosa}")
        esito.da_fare("Installa il plugin Intro Skipper da Jellyfin → Pannello di controllo → Plugin.")
