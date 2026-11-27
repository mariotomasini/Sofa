"""Preparazione prima dell'avvio dei servizi.

- crea le cartelle di film, serie, registrazioni e download;
- sistema i proprietari dei volumi (Jellyfin e Cerca girano come PUID:PGID);
- scrive le impostazioni iniziali di qBittorrent, che servono prima del primo
  avvio (altrimenti qBittorrent genera una password casuale).
"""

from __future__ import annotations

import base64
import hashlib
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from .impostazioni import Impostazioni
from .messaggi import avviso, ok

CARTELLE_DATI = [
    "downloads/radarr",
    "downloads/cerca",
    "downloads/manuale",
    "downloads/sonarr",
    "downloads/lidarr",
    "media/film",
    "media/serie",
    "media/anime",
    "media/musica",
    "media/registrazioni",
    "foto",  # Immich: le foto dei telefoni (mai toccate dalla pulizia)
]

VOLUMI_UTENTE = ["jellyfin_config", "cerca_dati"]


def _chown(percorso: Path, uid: int, gid: int, ricorsivo: bool = False) -> None:
    try:
        os.chown(percorso, uid, gid)
        if ricorsivo:
            for radice, cartelle, file in os.walk(percorso):
                for nome in cartelle + file:
                    os.lchown(os.path.join(radice, nome), uid, gid)
    except OSError:
        # Su Windows (cartelle NTFS) i proprietari non esistono: va bene così.
        pass


def crea_cartelle(imp: Impostazioni, radice_dati: Path = Path("/data")) -> None:
    uid, gid = int(imp.get("PUID", "1000")), int(imp.get("PGID", "1000"))
    for relativo in CARTELLE_DATI:
        percorso = radice_dati / relativo
        nuovo = not percorso.exists()
        percorso.mkdir(parents=True, exist_ok=True)
        if nuovo:
            _chown(percorso, uid, gid)
    for intermedia in ("downloads", "media"):
        _chown(radice_dati / intermedia, uid, gid)
    ok(f"Cartelle pronte in {imp.get('MEDIA_DIR')}")


def sistema_volumi(imp: Impostazioni, radice_volumi: Path = Path("/volumi")) -> None:
    uid, gid = int(imp.get("PUID", "1000")), int(imp.get("PGID", "1000"))
    for nome in VOLUMI_UTENTE:
        percorso = radice_volumi / nome
        if percorso.exists() and (percorso.stat().st_uid != uid or percorso.stat().st_gid != gid):
            _chown(percorso, uid, gid, ricorsivo=True)


# --- qBittorrent ---------------------------------------------------------------

def hash_qbittorrent(password: str, sale: bytes | None = None) -> str:
    """Hash della password nel formato di qBittorrent (PBKDF2-SHA512)."""
    sale = sale or os.urandom(16)
    chiave = hashlib.pbkdf2_hmac("sha512", password.encode(), sale, 100_000, dklen=64)
    return f"@ByteArray({base64.b64encode(sale).decode()}:{base64.b64encode(chiave).decode()})"


def verifica_hash_qbittorrent(password: str, valore: str) -> bool:
    try:
        interno = valore.strip('"')[len("@ByteArray("):-1]
        sale_b64, chiave_b64 = interno.split(":")
        sale = base64.b64decode(sale_b64)
        return hash_qbittorrent(password, sale) == valore.strip('"')
    except (ValueError, IndexError):
        return False


def leggi_ini(testo: str) -> dict[str, dict[str, str]]:
    sezioni: dict[str, dict[str, str]] = {}
    corrente = sezioni.setdefault("", {})
    for riga in testo.splitlines():
        r = riga.strip()
        if not r or r.startswith(";") or r.startswith("#"):
            continue
        if r.startswith("[") and r.endswith("]"):
            corrente = sezioni.setdefault(r[1:-1], {})
        elif "=" in r:
            k, v = r.split("=", 1)
            corrente[k] = v
    return sezioni


def scrivi_ini(sezioni: dict[str, dict[str, str]]) -> str:
    righe = []
    for nome, valori in sezioni.items():
        if not valori:
            continue
        if nome:
            righe.append(f"[{nome}]")
        righe.extend(f"{k}={v}" for k, v in valori.items())
        righe.append("")
    return "\n".join(righe)


def impostazioni_qbittorrent(imp: Impostazioni, esistente: str = "") -> str:
    sezioni = leggi_ini(esistente)
    subnet = imp.get("SUBNET", "172.28.64.0/24")
    pref = sezioni.setdefault("Preferences", {})
    sezioni.setdefault("LegalNotice", {})["Accepted"] = "true"
    attuale = pref.get("WebUI\\Password_PBKDF2", "")
    if not verifica_hash_qbittorrent(imp.password, attuale):
        pref["WebUI\\Password_PBKDF2"] = f'"{hash_qbittorrent(imp.password)}"'
    pref.update({
        "WebUI\\Username": imp.utente,
        "WebUI\\Port": "8080",
        "WebUI\\LocalHostAuth": "false",
        # Le richieste dalla rete interna (Caddy, Cerca, Radarr) non chiedono
        # login: la protezione la fa la password di Caddy.
        "WebUI\\AuthSubnetWhitelistEnabled": "true",
        "WebUI\\AuthSubnetWhitelist": subnet,
        "WebUI\\ReverseProxySupportEnabled": "true",
        "WebUI\\TrustedReverseProxiesList": subnet,
        "WebUI\\HostHeaderValidation": "false",
        "General\\Locale": "it",
        "Connection\\UPnP": "false",
    })
    return scrivi_ini(sezioni)


def prepara_qbittorrent(imp: Impostazioni, radice_volumi: Path = Path("/volumi")) -> None:
    percorso = radice_volumi / "qbittorrent_config" / "qBittorrent" / "qBittorrent.conf"
    if not percorso.parent.parent.exists():
        avviso("Volume di qBittorrent non trovato: salto la preparazione.")
        return
    esistente = percorso.read_text(encoding="utf-8") if percorso.exists() else ""
    nuovo = impostazioni_qbittorrent(imp, esistente)
    if nuovo != esistente:
        percorso.parent.mkdir(parents=True, exist_ok=True)
        percorso.write_text(nuovo, encoding="utf-8")
        uid, gid = int(imp.get("PUID", "1000")), int(imp.get("PGID", "1000"))
        _chown(percorso.parent, uid, gid, ricorsivo=True)
    ok("Impostazioni iniziali di qBittorrent pronte")


# --- Jellyfin ------------------------------------------------------------------

RETE_JELLYFIN_MINIMA = """<?xml version="1.0" encoding="utf-8"?>
<NetworkConfiguration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" \
xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <BaseUrl>/jellyfin</BaseUrl>
  <EnableUPnP>false</EnableUPnP>
</NetworkConfiguration>
"""


def prepara_jellyfin(imp: Impostazioni, radice_volumi: Path = Path("/volumi")) -> bool:
    """Imposta il base URL /jellyfin prima dell'avvio: Jellyfin lo applica solo
    ripartendo. Restituisce True se Jellyfin va riavviato."""
    volume = radice_volumi / "jellyfin_config"
    if not volume.exists():
        return False
    percorso = volume / "config" / "network.xml"
    uid, gid = int(imp.get("PUID", "1000")), int(imp.get("PGID", "1000"))
    if not percorso.exists():
        percorso.parent.mkdir(parents=True, exist_ok=True)
        percorso.write_text(RETE_JELLYFIN_MINIMA, encoding="utf-8")
        _chown(percorso.parent, uid, gid, ricorsivo=True)
        return False
    albero = ET.parse(percorso)
    base = albero.getroot().find("BaseUrl")
    if base is not None and base.text == "/jellyfin":
        return False
    if base is None:
        base = ET.SubElement(albero.getroot(), "BaseUrl")
    base.text = "/jellyfin"
    albero.write(percorso, encoding="utf-8", xml_declaration=True)
    _chown(percorso, uid, gid)
    return True


def segna_da_riavviare(cartella_mediabox: Path, servizio: str) -> None:
    """Annota un servizio da riavviare: lo fa lo script mediabox dopo l'avvio."""
    file = cartella_mediabox / "generato" / ".da-riavviare"
    file.parent.mkdir(parents=True, exist_ok=True)
    attuali = set(file.read_text().split()) if file.exists() else set()
    attuali.add(servizio)
    file.write_text("\n".join(sorted(attuali)) + "\n")


def prepara(imp: Impostazioni, cartella_mediabox: Path) -> None:
    crea_cartelle(imp)
    sistema_volumi(imp)
    prepara_qbittorrent(imp)
    if prepara_jellyfin(imp):
        segna_da_riavviare(cartella_mediabox, "jellyfin")
    ok("Indirizzo /jellyfin impostato")
