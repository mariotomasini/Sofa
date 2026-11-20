"""Modalità leggera: sui PC con poca memoria si accende solo quello che ci sta.

L'installer scrive nel file .env la memoria del PC (MEMORIA_GB) e poi lancia
"python -m configuratore leggero". Sotto i 6 GB spegniamo i programmi più
pesanti che non servono per cercare, scaricare e guardare film. Lo facciamo una
volta sola (MODALITA_LEGGERA=si): se poi qualcuno li riaccende dalle
impostazioni, la sua scelta resta.
"""

from __future__ import annotations

from pathlib import Path

from .impostazioni import leggi_env
from .menu import PREDEFINITI, aggiorna_env, attivo
from .messaggi import avviso, info

SOGLIA_GB = 6
# Memoria indicativa usata da ogni programma facoltativo, in MB
MEMORIA_MB = {
    "WITH_FLARESOLVERR": 400,
    "WITH_LIDARR": 250,
    "WITH_SONARR": 250,
    "WITH_BAZARR": 200,
    "WITH_BESZEL": 100,
    "WITH_TGARR": 600,
}
# Quelli che la modalità leggera spegne (Serie TV e Anime restano come li hai scelti)
DA_SPEGNERE = {
    "WITH_FLARESOLVERR": "Aiuto per i siti protetti (FlareSolverr)",
    "WITH_LIDARR": "Musica da scaricare (Lidarr)",
    "WITH_BAZARR": "Sottotitoli automatici (Bazarr)",
    "WITH_BESZEL": "Grafici del PC (Beszel)",
    "WITH_TGARR": "Telegram come fonte (tgarr)",
}


def memoria_gb(valori: dict[str, str]) -> float:
    try:
        return float(valori.get("MEMORIA_GB", "") or 0)
    except ValueError:
        return 0


def poca_memoria(valori: dict[str, str]) -> bool:
    gb = memoria_gb(valori)
    return 0 < gb < SOGLIA_GB


def applica(cartella: Path) -> list[str]:
    """Spegne i programmi pesanti se serve. Restituisce i nomi di quelli spenti."""
    file_env = cartella / ".env"
    valori = leggi_env(cartella / ".env.example")
    valori.update(leggi_env(file_env))
    if not poca_memoria(valori) or valori.get("MODALITA_LEGGERA", "").strip().lower() in ("si", "no"):
        return []
    spenti = [nome for chiave, nome in DA_SPEGNERE.items() if attivo(valori, chiave)]
    modifiche = {chiave: "no" for chiave in DA_SPEGNERE if valori.get(chiave, PREDEFINITI.get(chiave)) != "no"}
    modifiche["MODALITA_LEGGERA"] = "si"
    aggiorna_env(file_env, modifiche)
    return spenti


def main(cartella: Path) -> int:
    spenti = applica(cartella)
    if spenti:
        valori = leggi_env(cartella / ".env")
        gb = f"{memoria_gb(valori):g}".replace(".", ",")
        avviso(f"Questo PC ha {gb} GB di memoria: accendo solo i programmi che ci stanno.")
        for nome in spenti:
            info(f"Spento: {nome}")
        info("Puoi riaccenderli dalle impostazioni (/impostazioni), ma il PC potrebbe rallentare molto.")
    return 0
