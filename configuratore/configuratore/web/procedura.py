"""Logica della configurazione dal browser, separata dal server web per poterla provare.

File scambiati con l'installer e con lo script mediabox (tutti nella cartella di mediabox):

- .configurazione-pronta   la procedura è finita: l'installer può proseguire
- .da-applicare            impostazioni cambiate dopo l'installazione (contiene la vecchia MEDIA_DIR)
- installazione.log        quello che l'installer scrive mentre lavora
- installazione.stato      in_corso | fatto | errore
- dischi.json              i dischi proposti per i media (lo scrive l'installer)
- generato/esito.json      com'è andata la configurazione dei servizi
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..impostazioni import leggi_env
from ..menu import PREDEFINITI, VOCI, aggiorna_env, attivo, lingue_valide, modifiche_finali, percorso_valido

FLAG_PRONTA = ".configurazione-pronta"
FLAG_APPLICA = ".da-applicare"
LOG = "installazione.log"
STATO = "installazione.stato"
DISCHI = "dischi.json"
ESITO = "generato/esito.json"

# Come raggruppare le voci nella pagina
GRUPPI = {
    "WITH_SONARR": "guardi", "WITH_ANIME": "guardi", "WITH_LIDARR": "guardi", "WITH_BAZARR": "guardi",
    "PULIZIA_VISTI": "pc", "AGGIORNAMENTI_AUTOMATICI": "pc", "WITH_BESZEL": "pc", "WITH_FLARESOLVERR": "pc",
}
TITOLI_VOCI = {
    "WITH_SONARR": ("Serie TV", "Sonarr le scarica e le ordina per stagioni."),
    "WITH_ANIME": ("Anime", "Libreria separata in Jellyfin, con la numerazione degli episodi degli anime."),
    "WITH_LIDARR": ("Musica da scaricare",
                    "Lidarr cerca gli album e li ordina per artista. La musica che hai già si ascolta comunque."),
    "WITH_BAZARR": ("Sottotitoli automatici", "Bazarr cerca i sottotitoli nelle lingue che scegli."),
    "PULIZIA_VISTI": ("Pulizia quando il disco è pieno",
                      "Toglie i film e gli episodi già visti da più di 30 giorni. Mai i preferiti."),
    "AGGIORNAMENTI_AUTOMATICI": ("Aggiornamenti automatici", "Di notte installa le nuove versioni di mediabox."),
    "WITH_BESZEL": ("Grafici del PC", "Processore, memoria, dischi e temperatura, con avvisi."),
    "WITH_FLARESOLVERR": ("Aiuto per i siti protetti", "FlareSolverr serve ad alcuni indexer protetti da Cloudflare."),
}
# Scelte di sistema su Windows, applicate dall'installer
VOCI_WINDOWS = {
    "WIN_RINOMINA_PC": ("Chiama il PC \"MEDIABOX\"",
                        "Così l'indirizzo è http://mediabox.local. Serve un riavvio alla fine."),
    "WIN_RETE_PRIVATA": ("Rete di casa come \"Privata\"", "Altrimenti telefono e TV non vedono mediabox."),
    "WIN_NO_SOSPENSIONE": ("Niente sospensione con la corrente", "Se il PC si addormenta, mediabox si spegne."),
}
TITOLI_RIEPILOGO = {"Indirizzi di mediabox", "Cosa è attivo", "Cosa non è andato", "Cosa resta da fare a mano"}
ANSI = re.compile(r"\x1b\[[0-9;]*m")


class DatiNonValidi(ValueError):
    """Un campo non va bene: il messaggio si mostra così com'è."""

    def __init__(self, campo: str, messaggio: str) -> None:
        super().__init__(messaggio)
        self.campo = campo


# --- lettura ----------------------------------------------------------------------------
def valori(cartella: Path) -> dict[str, str]:
    base = leggi_env(cartella / ".env.example")
    base.update(leggi_env(cartella / ".env"))
    return base


def configurato(cartella: Path) -> bool:
    return bool(valori(cartella).get("ADMIN_PASSWORD"))


def windows(v: dict[str, str]) -> bool:
    return v.get("PIATTAFORMA", "linux").lower() == "windows"


def dischi(cartella: Path, v: dict[str, str]) -> list[dict]:
    """Le cartelle proposte per film e musica. Senza dischi.json resta quella predefinita."""
    elenco = []
    try:
        elenco = json.loads((cartella / DISCHI).read_text(encoding="utf-8"))
        if isinstance(elenco, dict):  # ConvertTo-Json con un solo disco
            elenco = [elenco]
    except (OSError, ValueError):
        pass
    proposte = []
    for d in elenco if isinstance(elenco, list) else []:
        percorso = percorso_valido(str(d.get("percorso", "")), windows(v))
        if percorso:
            proposte.append({
                "percorso": percorso,
                "nome": str(d.get("nome") or percorso),
                "libero_gb": int(d.get("libero_gb") or 0),
                "totale_gb": int(d.get("totale_gb") or 0),
                "consigliato": bool(d.get("consigliato")),
            })
    if not proposte:
        proposte = [{"percorso": v.get("MEDIA_DIR", "/srv/mediabox/dati"), "nome": "Disco del sistema",
                     "libero_gb": 0, "totale_gb": 0, "consigliato": True}]
    if not any(p["consigliato"] for p in proposte):
        max(proposte, key=lambda p: p["libero_gb"])["consigliato"] = True
    return proposte


def scelte_attuali(v: dict[str, str]) -> dict:
    """Quello che la pagina mostra: niente segreti."""
    voci = [
        {"chiave": k, "titolo": TITOLI_VOCI[k][0], "descrizione": TITOLI_VOCI[k][1], "gruppo": GRUPPI[k],
         "attivo": attivo(v, k)}
        for k, _, _ in VOCI
    ]
    risultato = {
        "voci": voci,
        "lingue": v.get("LINGUE_SOTTOTITOLI", "it,en"),
        "ntfy": v.get("NTFY_TOPIC", ""),
        "cartella": v.get("MEDIA_DIR", ""),
        "tmdb_impostata": bool(v.get("TMDB_API_KEY")),
        "windows": windows(v),
        "hardware": {
            "grafica": attivo(v, "VAAPI"),
            "tuner": attivo(v, "DVB"),
            "hdhomerun": v.get("HDHOMERUN_IP", ""),
        },
    }
    if windows(v):
        risultato["voci_windows"] = [
            {"chiave": k, "titolo": t, "descrizione": d, "attivo": v.get(k, "si") != "no"}
            for k, (t, d) in VOCI_WINDOWS.items()
        ]
    return risultato


def indirizzi(v: dict[str, str]) -> dict:
    porta = v.get("HTTP_PORT", "80") or "80"
    suffisso = "" if porta == "80" else f":{porta}"
    tutti = [f"http://{v.get('NOME_HOST') or 'mediabox.local'}{suffisso}"]
    if v.get("HOST_IP"):
        tutti.append(f"http://{v['HOST_IP']}{suffisso}")
    principale = tutti[-1]
    return {"tutti": tutti, "principale": principale, "jellyfin_tv": f"{principale}/jellyfin"}


# --- validazione e salvataggio ---------------------------------------------------------
def valida_password(p1: str, p2: str) -> str:
    if len(p1) < 8:
        raise DatiNonValidi("password", "La password deve avere almeno 8 caratteri.")
    if len(p1.encode()) > 72:
        raise DatiNonValidi("password", "La password è troppo lunga: al massimo 72 caratteri.")
    if '"' in p1 or "\\" in p1:
        raise DatiNonValidi("password", "Togli le virgolette doppie (\") e la barra rovesciata (\\).")
    if p1 != p2:
        raise DatiNonValidi("password2", "Le due password non coincidono.")
    return p1


def pulisci_ntfy(testo: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", testo or "")[:64]


def suggerisci_ntfy() -> str:
    return "mediabox-" + secrets.token_hex(3)


def modifiche_da_scelte(v: dict[str, str], dati: dict) -> dict[str, str]:
    """Traduce le scelte della pagina nelle righe del file .env (solo quelle cambiate)."""
    scelte: dict[str, str] = {}
    voci = dati.get("voci") or {}
    for chiave, _, _ in VOCI:
        if chiave in voci:
            scelte[chiave] = "si" if voci[chiave] else "no"
        else:
            scelte[chiave] = v.get(chiave, PREDEFINITI.get(chiave, "no"))
    if scelte.get("WITH_ANIME") == "si":
        scelte["WITH_SONARR"] = "si"  # gli anime passano da Sonarr
    if "lingue" in dati:
        lingue = lingue_valide(str(dati["lingue"]))
        if not lingue:
            raise DatiNonValidi("lingue", "Scrivi le lingue con codici di due lettere, separate da virgole (es. it,en).")
        scelte["LINGUE_SOTTOTITOLI"] = lingue
    if "ntfy" in dati:
        scelte["NTFY_TOPIC"] = pulisci_ntfy(str(dati["ntfy"]))
    if "cartella" in dati:
        cartella = percorso_valido(str(dati["cartella"]), windows(v))
        if not cartella:
            esempio = "D:\\Mediabox" if windows(v) else "/mnt/disco/mediabox"
            raise DatiNonValidi("cartella", f"Scrivi il percorso completo della cartella, es. {esempio}")
        scelte["MEDIA_DIR"] = cartella
    originali = {k: v.get(k, "") for k in scelte} | {k: v[k] for k in v if k.endswith("_API_KEY")}
    modifiche = modifiche_finali(originali, scelte)
    if windows(v):
        for chiave in VOCI_WINDOWS:
            if chiave in (dati.get("voci_windows") or {}):
                modifiche[chiave] = "si" if dati["voci_windows"][chiave] else "no"
    return modifiche


def salva_installazione(cartella: Path, dati: dict) -> None:
    """Prima installazione: salva tutto e dice all'installer di proseguire."""
    if configurato(cartella):
        raise DatiNonValidi("", "mediabox è già configurato: per cambiare le scelte apri le impostazioni.")
    v = valori(cartella)
    # la chiave TMDB è facoltativa
    chiave = re.sub(r"\s", "", str(dati.get("tmdb") or ""))
    password = valida_password(str(dati.get("password") or ""), str(dati.get("password2") or ""))
    modifiche = modifiche_da_scelte(v, dati)
    if chiave:
        modifiche["TMDB_API_KEY"] = chiave
    modifiche["ADMIN_PASSWORD"] = password
    if not v.get("IMPOSTAZIONI_SEGRETO"):
        modifiche["IMPOSTAZIONI_SEGRETO"] = secrets.token_hex(32)
    file_env = cartella / ".env"
    if not file_env.exists():
        file_env.write_text((cartella / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    aggiorna_env(file_env, modifiche)
    file_env.chmod(0o600)
    (cartella / FLAG_PRONTA).write_text(str(int(time.time())), encoding="utf-8")


def salva_impostazioni(cartella: Path, dati: dict) -> bool:
    """Dopo l'installazione. Restituisce False se non è cambiato nulla."""
    v = valori(cartella)
    modifiche = modifiche_da_scelte(v, dati)
    chiave = re.sub(r"\s", "", str(dati.get("tmdb") or ""))
    if dati.get("tmdb_togli") and v.get("TMDB_API_KEY"):
        modifiche["TMDB_API_KEY"] = ""
    elif chiave and chiave != v.get("TMDB_API_KEY"):
        modifiche["TMDB_API_KEY"] = chiave
    if not modifiche:
        return False
    aggiorna_env(cartella / ".env", modifiche)
    # dentro c'è la cartella di prima: se cambia, lo script sposta i file
    (cartella / FLAG_APPLICA).write_text(v.get("MEDIA_DIR", ""), encoding="utf-8")
    (cartella / STATO).write_text("attesa", encoding="utf-8")
    return True


# --- accesso ---------------------------------------------------------------------------
def segreto(cartella: Path) -> str:
    v = valori(cartella)
    if not v.get("IMPOSTAZIONI_SEGRETO"):
        nuovo = secrets.token_hex(32)
        aggiorna_env(cartella / ".env", {"IMPOSTAZIONI_SEGRETO": nuovo})
        return nuovo
    return v["IMPOSTAZIONI_SEGRETO"]


def gettone(cartella: Path, scadenza: int | None = None) -> str:
    """Cookie firmato: vale finché non cambiano la password o il segreto."""
    scadenza = scadenza or int(time.time()) + 30 * 86400
    v = valori(cartella)
    messaggio = f"{scadenza}:{hashlib.sha256(v.get('ADMIN_PASSWORD', '').encode()).hexdigest()}"
    firma = hmac.new(segreto(cartella).encode(), messaggio.encode(), hashlib.sha256).hexdigest()
    return f"{scadenza}.{firma}"


def gettone_valido(cartella: Path, valore: str | None) -> bool:
    if not valore or "." not in valore:
        return False
    scadenza, _ = valore.split(".", 1)
    if not scadenza.isdigit() or int(scadenza) < time.time():
        return False
    return hmac.compare_digest(valore, gettone(cartella, int(scadenza)))


def password_giusta(cartella: Path, password: str) -> bool:
    attesa = valori(cartella).get("ADMIN_PASSWORD", "")
    return bool(attesa) and hmac.compare_digest(password.encode(), attesa.encode())


# --- avanzamento -------------------------------------------------------------------------
@dataclass
class Avanzamento:
    stato: str = "nessuna"  # nessuna | attesa | in_corso | fatto | errore
    passi: list[dict] = field(default_factory=list)
    scarico: dict | None = None
    problemi: list[dict] = field(default_factory=list)
    da_fare: list[str] = field(default_factory=list)
    note: list[str] = field(default_factory=list)
    ultima_riga: str = ""


def nome_passo(titolo: str) -> str:
    return re.sub(r"\s*\(.*\)$", "", titolo)  # "Scarico i programmi (la prima volta…)" → "Scarico i programmi"


def leggi_log(testo: str) -> Avanzamento:
    """Dal log dell'installer: i passi (titoli "== … =="), i download, gli errori."""
    a = Avanzamento()
    nel_riepilogo = False
    problema: dict | None = None
    for grezza in testo.splitlines():
        riga = ANSI.sub("", grezza).rstrip()
        if not riga.strip():
            continue
        titolo = re.fullmatch(r"== (.+) ==", riga.strip())
        if titolo:
            t = titolo.group(1)
            nel_riepilogo = t in TITOLI_RIEPILOGO or nel_riepilogo
            if not nel_riepilogo:
                for p in a.passi:
                    if p["stato"] == "in_corso":
                        p["stato"] = "fatto"
                a.passi.append({"testo": nome_passo(t), "stato": "in_corso"})
            continue
        if nel_riepilogo:
            continue
        # solo i messaggi di mediabox ("· …", "✔ …", "! …"), non quelli tecnici di Docker
        nostra = re.match(r"^\s+([·✔!])\s+(.*)$", riga)
        if nostra:
            a.ultima_riga = nostra.group(2)
            scarico = re.search(r"Scarico (.+?) \((\d+) di (\d+)\)", riga)
            a.scarico = (
                {"nome": scarico.group(1), "n": int(scarico.group(2)), "di": int(scarico.group(3))} if scarico else None
            )
        if riga.strip().startswith("✘"):
            problema = {"nome": a.passi[-1]["testo"] if a.passi else "", "cosa": riga.strip()[1:].strip(), "cosa_fare": ""}
            a.problemi.append(problema)
            if a.passi:
                a.passi[-1]["stato"] = "errore"
        elif problema and "Cosa fare:" in riga:
            problema["cosa_fare"] = riga.split("Cosa fare:", 1)[1].strip()
            problema = None
    return a


def avanzamento(cartella: Path) -> Avanzamento:
    try:
        stato = (cartella / STATO).read_text(encoding="utf-8").strip()
    except OSError:
        stato = ""
    if (cartella / FLAG_APPLICA).exists() or (cartella / FLAG_PRONTA).exists():
        stato = stato if stato == "in_corso" else "attesa"
    try:
        testo = (cartella / LOG).read_text(encoding="utf-8", errors="replace")
    except OSError:
        testo = ""
    a = leggi_log(testo) if stato in ("in_corso", "fatto", "errore") else Avanzamento()
    a.stato = stato or "nessuna"
    if a.stato in ("fatto", "errore"):
        for p in a.passi:
            if p["stato"] == "in_corso":
                p["stato"] = "fatto" if a.stato == "fatto" else "errore"
        try:
            esito = json.loads((cartella / ESITO).read_text(encoding="utf-8"))
            a.problemi = esito.get("falliti", a.problemi)
            a.da_fare = esito.get("da_fare", [])
            a.note = esito.get("note", [])
        except (OSError, ValueError):
            pass
    return a
