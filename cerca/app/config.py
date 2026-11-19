"""Impostazioni di Cerca, lette dalle variabili d'ambiente (vengono dal .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

VERO = {"si", "sì", "yes", "true", "1"}


def _env(nome: str, predefinito: str = "") -> str:
    return os.environ.get(nome, predefinito).strip()


@dataclass(frozen=True)
class Config:
    tmdb_chiave: str = field(default_factory=lambda: _env("TMDB_API_KEY"))

    @property
    def con_tmdb(self) -> bool:
        """La chiave TMDB è facoltativa: senza, Cerca usa fonti che non chiedono chiavi."""
        return bool(self.tmdb_chiave) and self.tmdb_chiave != "da-impostare"
    tmdb_api: str = field(default_factory=lambda: _env("TMDB_API_URL", "https://api.themoviedb.org/3").rstrip("/"))
    paese: str = field(default_factory=lambda: _env("PAESE", "IT"))
    lingua: str = field(default_factory=lambda: _env("LINGUA", "it"))
    prowlarr_url: str = field(default_factory=lambda: _env("PROWLARR_URL", "http://prowlarr:9696/prowlarr"))
    prowlarr_chiave: str = field(default_factory=lambda: _env("PROWLARR_API_KEY"))
    radarr_url: str = field(default_factory=lambda: _env("RADARR_URL", "http://radarr:7878/radarr"))
    radarr_chiave: str = field(default_factory=lambda: _env("RADARR_API_KEY"))
    qbit_url: str = field(default_factory=lambda: _env("QBIT_URL", "http://qbittorrent:8080"))
    jellyfin_url: str = field(default_factory=lambda: _env("JELLYFIN_URL", "http://jellyfin:8096/jellyfin"))
    lidarr_url: str = field(default_factory=lambda: _env("LIDARR_URL"))
    lidarr_chiave: str = field(default_factory=lambda: _env("LIDARR_API_KEY"))
    sonarr_url: str = field(default_factory=lambda: _env("SONARR_URL"))
    sonarr_chiave: str = field(default_factory=lambda: _env("SONARR_API_KEY"))
    pulizia_visti: bool = field(default_factory=lambda: _env("PULIZIA_VISTI", "no").lower() in VERO)
    pulizia_giorni: int = field(default_factory=lambda: int(_env("PULIZIA_GIORNI", "30") or 30))
    soglia_disco: float = field(default_factory=lambda: float(_env("SOGLIA_DISCO_LIBERO", "10") or 10))
    utente: str = field(default_factory=lambda: _env("ADMIN_USER", "admin"))
    password: str = field(default_factory=lambda: _env("ADMIN_PASSWORD"))
    ntfy_topic: str = field(default_factory=lambda: _env("NTFY_TOPIC"))
    ntfy_server: str = field(default_factory=lambda: _env("NTFY_SERVER", "https://ntfy.sh").rstrip("/"))
    indirizzo_pubblico: str = field(default_factory=lambda: _env("INDIRIZZO_PUBBLICO", "http://mediabox.local"))
    cartella_dati: Path = field(default_factory=lambda: Path(_env("CARTELLA_DATI", "/dati")))
    cartella_download: Path = field(default_factory=lambda: Path(_env("CARTELLA_DOWNLOAD", "/data/downloads")))
    cartella_film_radarr: str = field(default_factory=lambda: _env("CARTELLA_FILM_RADARR", "/data/media/film"))
    root_path: str = field(default_factory=lambda: _env("ROOT_PATH", "/cerca"))

    @property
    def lingua_tmdb(self) -> str:
        """TMDB vuole lingua-PAESE, es. it-IT."""
        return f"{self.lingua}-{self.paese}" if "-" not in self.lingua else self.lingua
