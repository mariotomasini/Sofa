"""Generazione dei file di mediabox (compose, Caddyfile, Homepage).

I file generati vanno in <cartella mediabox>/generato e si possono rifare in
qualsiasi momento: i dati veri stanno nei volumi Docker e in MEDIA_DIR.
"""

from __future__ import annotations

import re
from pathlib import Path

import bcrypt
from jinja2 import Environment, PackageLoader, StrictUndefined

from .impostazioni import Impostazioni
from .messaggi import ErroreMediabox, ok

FILE_DA_GENERARE = {
    "docker-compose.yml.j2": "docker-compose.yml",
    "Caddyfile.j2": "Caddyfile",
    "homepage/services.yaml.j2": "homepage/services.yaml",
    "homepage/settings.yaml.j2": "homepage/settings.yaml",
    "homepage/widgets.yaml.j2": "homepage/widgets.yaml",
    "homepage/bookmarks.yaml.j2": "homepage/bookmarks.yaml",
    "homepage/custom.css.j2": "homepage/custom.css",
}


def _ambiente_jinja() -> Environment:
    return Environment(
        loader=PackageLoader("configuratore", "templates"),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )


def _hash_password(password: str, caddyfile_esistente: Path) -> str:
    """Hash bcrypt per Caddy. Riusa quello vecchio se la password non è cambiata,
    così il Caddyfile non cambia a ogni esecuzione."""
    if caddyfile_esistente.exists():
        trovato = re.search(r"\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}", caddyfile_esistente.read_text())
        if trovato and bcrypt.checkpw(password.encode(), trovato.group(0).encode()):
            return trovato.group(0)
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def gateway(subnet: str) -> str:
    """Primo indirizzo della rete interna: da lì i container raggiungono il PC."""
    import ipaddress

    return str(next(ipaddress.ip_network(subnet, strict=False).hosts()))


def normalizza_percorso(percorso: str) -> str:
    """Percorso adatto a Docker: barre "/" anche su Windows, niente barra finale."""
    percorso = percorso.strip().replace("\\", "/")
    return percorso.rstrip("/") if len(percorso) > 1 else percorso


def contesto(imp: Impostazioni, cartella_generato: Path) -> dict:
    valori = dict(imp.valori)
    # la chiave TMDB è facoltativa: senza, Cerca usa fonti gratuite
    for chiave in ("ADMIN_PASSWORD", "RADARR_API_KEY", "PROWLARR_API_KEY"):
        imp.obbligatorio(chiave)
    if imp.sonarr:
        imp.obbligatorio("SONARR_API_KEY")
    if imp.lidarr:
        imp.obbligatorio("LIDARR_API_KEY")
    if imp.vaapi and not imp.get("RENDER_GID"):
        raise ErroreMediabox(
            "La transcodifica hardware è attiva ma manca RENDER_GID nel file .env.",
            "Esegui di nuovo l'installer, che lo rileva da solo.",
        )
    if '"' in imp.password or "\\" in imp.password:
        raise ErroreMediabox(
            "La password di amministrazione contiene virgolette doppie o la barra \\.",
            "Scegli una password senza questi due caratteri e rilancia l'installer.",
        )
    valori["MEDIA_DIR"] = normalizza_percorso(imp.obbligatorio("MEDIA_DIR"))
    valori.update(
        vaapi=imp.vaapi,
        dvb=imp.dvb,
        sonarr=imp.sonarr,
        anime=imp.anime,
        lidarr=imp.lidarr,
        bazarr=imp.bazarr,
        flaresolverr=imp.flaresolverr,
        beszel=imp.beszel,
        gateway=gateway(imp.get("SUBNET", "172.28.64.0/24")),
        tv=imp.dvb or bool(imp.hdhomerun),
        hash_password=_hash_password(imp.password, cartella_generato / "Caddyfile"),
    )
    return valori


def genera(imp: Impostazioni, cartella_mediabox: Path) -> list[str]:
    """Scrive i file generati. Restituisce l'elenco dei file cambiati."""
    cartella = cartella_mediabox / "generato"
    ctx = contesto(imp, cartella)
    env = _ambiente_jinja()
    cambiati = []
    for modello, destinazione in FILE_DA_GENERARE.items():
        valori = ctx
        if destinazione == "docker-compose.yml":
            # Docker Compose interpreta "$": va raddoppiato (es. password con "$")
            valori = {k: v.replace("$", "$$") if isinstance(v, str) else v for k, v in ctx.items()}
        testo = env.get_template(modello).render(**valori)
        percorso = cartella / destinazione
        percorso.parent.mkdir(parents=True, exist_ok=True)
        if not percorso.exists() or percorso.read_text(encoding="utf-8") != testo:
            percorso.write_text(testo, encoding="utf-8", newline="\n")
            cambiati.append(destinazione)
    if "Caddyfile" in cambiati:
        from .prepara import segna_da_riavviare

        segna_da_riavviare(cartella_mediabox, "caddy")
    if cambiati:
        ok("File generati: " + ", ".join(cambiati))
    else:
        ok("File generati già aggiornati")
    return cambiati
