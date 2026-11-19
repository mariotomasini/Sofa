"""Configurazione di Bazarr (sottotitoli automatici).

Bazarr non legge impostazioni scritte prima del primo avvio: tutto passa
dalla sua API. La chiave API la genera Bazarr al primo avvio e la leggiamo
dal suo file di configurazione nel volume.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx

from ..impostazioni import Impostazioni
from ..messaggi import ErroreMediabox, info, ok

INDIRIZZO = "http://bazarr:6767"
BASE_URL = "/bazarr"
FILE_CONFIG = Path("/volumi/bazarr_config/config/config.yaml")

# Fonti di sottotitoli che non chiedono un account
FONTI = ["embeddedsubtitles", "yifysubtitles", "subtitlecat", "subf2m", "gestdown", "animetosho"]

NOMI_LINGUE = {"it": "italiano", "en": "inglese", "es": "spagnolo", "fr": "francese", "de": "tedesco", "ja": "giapponese"}


def leggi_chiave(testo: str) -> str:
    """La chiave API sta nella sezione "auth:" del config.yaml di Bazarr."""
    sezione = re.search(r"^auth:\n((?:[ \t]+.*\n?)*)", testo, re.M)
    if sezione:
        trovata = re.search(r"apikey:\s*['\"]?([0-9a-f]{32})", sezione.group(1))
        if trovata:
            return trovata.group(1)
    return ""


def attendi_chiave(timeout: float = 180) -> str:
    scadenza = time.monotonic() + timeout
    while time.monotonic() < scadenza:
        if FILE_CONFIG.exists():
            chiave = leggi_chiave(FILE_CONFIG.read_text(encoding="utf-8", errors="replace"))
            if chiave:
                return chiave
        time.sleep(3)
    raise ErroreMediabox(
        "Bazarr non ha ancora creato la sua configurazione.",
        "Aspetta un minuto e riesegui \"mediabox configura\".",
    )


def trova_indirizzo(chiave: str, timeout: float = 180) -> str:
    """Restituisce l'indirizzo dell'API, con o senza /bazarr davanti."""
    scadenza = time.monotonic() + timeout
    while time.monotonic() < scadenza:
        for prefisso in (BASE_URL, ""):
            try:
                r = httpx.get(f"{INDIRIZZO}{prefisso}/api/system/status", headers={"X-API-KEY": chiave}, timeout=10)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                    return f"{INDIRIZZO}{prefisso}"
            except httpx.HTTPError:
                pass
        time.sleep(3)
    raise ErroreMediabox("Bazarr non risponde.", "Guarda \"mediabox logs bazarr\" e poi riprova con \"mediabox configura\".")


def profilo_lingue(lingue: list[str]) -> list[dict]:
    return [{
        "profileId": 1,
        "name": "mediabox",
        "cutoff": None,
        "items": [
            {
                "id": i + 1, "language": lingua, "hi": "False", "forced": "False",
                "audio_exclude": "False", "audio_only_include": "False",
            }
            for i, lingua in enumerate(lingue)
        ],
        "mustContain": [],
        "mustNotContain": [],
        "originalFormat": 0,
        "tag": None,
    }]


def modulo_impostazioni(imp: Impostazioni) -> dict:
    lingue = imp.lingue_sottotitoli
    modulo: dict = {
        "settings-general-base_url": BASE_URL,
        "settings-general-use_radarr": "true",
        "settings-radarr-ip": "radarr",
        "settings-radarr-port": "7878",
        "settings-radarr-base_url": "/radarr",
        "settings-radarr-ssl": "false",
        "settings-radarr-apikey": imp.obbligatorio("RADARR_API_KEY"),
        "settings-radarr-only_monitored": "false",
        "settings-general-enabled_providers": FONTI,
        "languages-enabled": lingue,
        "languages-profiles": json.dumps(profilo_lingue(lingue)),
        "settings-general-serie_default_enabled": "true",
        "settings-general-serie_default_profile": "1",
        "settings-general-movie_default_enabled": "true",
        "settings-general-movie_default_profile": "1",
        "settings-general-use_embedded_subs": "true",
        "settings-general-upgrade_subs": "true",
        "settings-general-utf8_encode": "true",
        "settings-general-subfolder": "current",
        "settings-analytics-enabled": "false",
        "settings-backup-frequency": "Weekly",
    }
    if imp.sonarr:
        modulo.update({
            "settings-general-use_sonarr": "true",
            "settings-sonarr-ip": "sonarr",
            "settings-sonarr-port": "8989",
            "settings-sonarr-base_url": "/sonarr",
            "settings-sonarr-ssl": "false",
            "settings-sonarr-apikey": imp.obbligatorio("SONARR_API_KEY"),
            "settings-sonarr-only_monitored": "false",
        })
    return modulo


def configura(imp: Impostazioni, esito) -> None:
    info("Attendo che Bazarr sia pronto…")
    chiave = attendi_chiave()
    indirizzo = trova_indirizzo(chiave)
    r = httpx.post(
        f"{indirizzo}/api/system/settings",
        data=modulo_impostazioni(imp),
        headers={"X-API-KEY": chiave},
        timeout=60,
    )
    if not r.is_success:
        raise ErroreMediabox(
            f"Bazarr non ha accettato le impostazioni (errore {r.status_code}).",
            "Riprova con \"mediabox configura\".",
        )
    nomi = ", ".join(NOMI_LINGUE.get(x, x) for x in imp.lingue_sottotitoli)
    ok(f"Sottotitoli automatici in: {nomi}")
    ok("Collegato a Radarr" + (" e Sonarr" if imp.sonarr else ""))
    if not indirizzo.endswith(BASE_URL):
        # Il nuovo indirizzo /bazarr vale dopo un riavvio
        info("Riavvio Bazarr per attivare l'indirizzo /bazarr…")
        try:
            httpx.post(f"{indirizzo}/api/system", data={"action": "restart"}, headers={"X-API-KEY": chiave}, timeout=10)
        except httpx.HTTPError:
            pass
        time.sleep(10)
        if not trova_indirizzo(chiave).endswith(BASE_URL):
            esito.da_fare("Bazarr: esegui \"mediabox restart bazarr\" per attivare l'indirizzo /bazarr.")
    ok("Bazarr risponde su /bazarr")
    esito.nota(
        "Sottotitoli: per trovarne di più in italiano crea un account gratuito su opensubtitles.com "
        "e aggiungilo in Bazarr → Impostazioni → Fornitori."
    )
