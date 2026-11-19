"""Lettura delle impostazioni dal file .env di mediabox.

Il configuratore legge sempre il file .env direttamente (montato in /mediabox),
così funziona allo stesso modo su Linux e su Windows.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .messaggi import ErroreMediabox

CARTELLA_MEDIABOX = Path(os.environ.get("MEDIABOX_DIR", "/mediabox"))


def leggi_env(percorso: Path) -> dict[str, str]:
    """Legge un file .env semplice (CHIAVE=valore, commenti con #)."""
    valori: dict[str, str] = {}
    if not percorso.exists():
        return valori
    for riga in percorso.read_text(encoding="utf-8-sig").splitlines():
        riga = riga.strip()
        if not riga or riga.startswith("#") or "=" not in riga:
            continue
        chiave, valore = riga.split("=", 1)
        valore = valore.strip()
        # toglie eventuali virgolette intorno al valore
        if len(valore) >= 2 and valore[0] == valore[-1] and valore[0] in "\"'":
            valore = valore[1:-1]
        valori[chiave.strip()] = valore
    return valori


def _si(valore: str) -> bool:
    return valore.strip().lower() in {"si", "sì", "yes", "true", "1", "on"}


@dataclass
class Impostazioni:
    valori: dict[str, str] = field(default_factory=dict)

    @classmethod
    def carica(cls, cartella: Path | None = None) -> Impostazioni:
        cartella = cartella or CARTELLA_MEDIABOX
        file_env = cartella / ".env"
        if not file_env.exists():
            raise ErroreMediabox(
                f"Non trovo il file di configurazione {file_env}.",
                "Esegui di nuovo l'installer: crea lui questo file.",
            )
        valori = leggi_env(cartella / ".env.example")
        valori.update(leggi_env(file_env))
        return cls(valori)

    def get(self, chiave: str, predefinito: str = "") -> str:
        return self.valori.get(chiave, predefinito) or predefinito

    def obbligatorio(self, chiave: str) -> str:
        valore = self.get(chiave)
        if not valore:
            raise ErroreMediabox(
                f"Nel file .env manca il valore di {chiave}.",
                "Esegui di nuovo l'installer oppure aggiungi il valore a mano nel file .env.",
            )
        return valore

    # --- comodità ---------------------------------------------------------
    @property
    def utente(self) -> str:
        return self.get("ADMIN_USER", "admin")

    @property
    def password(self) -> str:
        return self.obbligatorio("ADMIN_PASSWORD")

    @property
    def vaapi(self) -> bool:
        return _si(self.get("VAAPI", "no"))

    @property
    def dvb(self) -> bool:
        return _si(self.get("DVB", "no"))

    @property
    def sonarr(self) -> bool:
        return _si(self.get("WITH_SONARR", "no"))

    @property
    def hdhomerun(self) -> str:
        return self.get("HDHOMERUN_IP")

    @property
    def windows(self) -> bool:
        return self.get("PIATTAFORMA", "linux").lower() == "windows"

    def flag(self, chiave: str) -> bool:
        return _si(self.get(chiave, "no"))
