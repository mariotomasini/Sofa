"""Lettura delle impostazioni dal file .env di mediabox.

Il configuratore legge sempre il file .env direttamente (montato in /mediabox),
così funziona allo stesso modo su Linux e su Windows.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .messaggi import ErroreMediabox

CARTELLA_MEDIABOX = Path(os.environ.get("MEDIABOX_DIR", "/mediabox"))


def scrivi_atomico(file: Path, testo: str, modo: int | None = None) -> None:
    """Scrive il file tutto o niente: prima una copia accanto, poi la si mette al suo posto.

    Con write_text uno spegnimento (o il disco pieno) a metà lasciava il .env troncato,
    cioè mediabox senza password e chiavi. I permessi restano quelli del file di prima
    (il .env è 600), oppure `modo` se indicato.
    """
    file.parent.mkdir(parents=True, exist_ok=True)
    if modo is None:
        try:
            modo = file.stat().st_mode & 0o777
        except OSError:
            modo = 0o644
    fd, temporaneo = tempfile.mkstemp(dir=file.parent, prefix=f".{file.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(testo)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temporaneo, modo)
        os.replace(temporaneo, file)
    except BaseException:
        Path(temporaneo).unlink(missing_ok=True)
        raise


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
    def utenti_casa(self) -> list[str]:
        """Le persone di casa (UTENTI_CASA=Mario,Anna,Bimbi): un utente di Jellyfin ciascuna."""
        nomi = [n.strip() for n in self.get("UTENTI_CASA").split(",") if n.strip()]
        visti: set[str] = set()
        risultato = []
        for nome in nomi:
            if nome.lower() not in visti and nome.lower() != self.utente.lower():
                visti.add(nome.lower())
                risultato.append(nome)
        return risultato

    @property
    def vaapi(self) -> bool:
        return _si(self.get("VAAPI", "no"))

    @property
    def dvb(self) -> bool:
        return _si(self.get("DVB", "no"))

    @property
    def anime(self) -> bool:
        return _si(self.get("WITH_ANIME", "no"))

    @property
    def sonarr(self) -> bool:
        """Sonarr serve sia per le serie sia per gli anime."""
        return _si(self.get("WITH_SONARR", "no")) or self.anime

    @property
    def lidarr(self) -> bool:
        return _si(self.get("WITH_LIDARR", "no"))

    @property
    def navidrome(self) -> bool:
        """Navidrome: la musica nelle app Subsonic (Amperfy, Symfonium…). Leggero, acceso di base."""
        return _si(self.get("WITH_NAVIDROME", "si"))

    @property
    def immich(self) -> bool:
        """Immich: le foto dei telefoni di casa, con i volti. Pesante, spento di base."""
        return _si(self.get("WITH_IMMICH", "no"))

    @property
    def persone_foto(self) -> list[str]:
        """Chi ha le foto (FOTO_PERSONE, se no le persone di UTENTI_CASA): un account Immich
        ciascuno, separato dagli altri."""
        scritte = [n.strip() for n in self.get("FOTO_PERSONE").split(",") if n.strip()]
        if not scritte:
            return self.utenti_casa
        visti: set[str] = set()
        risultato = []
        for nome in scritte:
            if nome.lower() not in visti:
                visti.add(nome.lower())
                risultato.append(nome)
        return risultato

    @property
    def foto_copia(self) -> str:
        """Cartella su un altro disco per la copia notturna delle foto ("" = nessuna)."""
        return self.get("FOTO_COPIA_DIR").strip() if self.immich else ""

    @property
    def bazarr(self) -> bool:
        return _si(self.get("WITH_BAZARR", "si"))

    @property
    def flaresolverr(self) -> bool:
        return _si(self.get("WITH_FLARESOLVERR", "si"))

    @property
    def lingue_audio(self) -> list[str]:
        """Lingue dell'audio in ordine di preferenza (LINGUE_AUDIO=it,es,en)."""
        from .servizi.formati import LINGUE, LINGUE_PREDEFINITE

        scelte = [x.strip().lower() for x in self.get("LINGUE_AUDIO", "it,es,en").split(",")]
        valide = list(dict.fromkeys(c for c in scelte if c in LINGUE))
        return valide or list(LINGUE_PREDEFINITE)

    @property
    def dimensione_max_gb(self) -> float:
        """Peso massimo di un film 1080p di 2 ore (le serie in proporzione alla durata)."""
        try:
            return max(1.0, float(self.get("DIMENSIONE_MAX_GB", "10").replace(",", ".")))
        except ValueError:
            return 10.0

    @property
    def tgarr(self) -> bool:
        """Telegram come fonte di download (tgarr)."""
        return _si(self.get("WITH_TGARR", "no"))

    @property
    def beszel(self) -> bool:
        return _si(self.get("WITH_BESZEL", "no"))

    @property
    def lingue_sottotitoli(self) -> list[str]:
        lingue = [x.strip().lower() for x in self.get("LINGUE_SOTTOTITOLI", "it,en").split(",") if x.strip()]
        return list(dict.fromkeys(lingue)) or ["it", "en"]

    @property
    def hdhomerun(self) -> str:
        return self.get("HDHOMERUN_IP")

    @property
    def tailscale_docker(self) -> bool:
        """Tailscale come servizio di mediabox (Linux). Non se è già installato sul PC
        (TAILSCALE_HOST=si, lo scrive lo script) o se è spento (WITH_TAILSCALE=no)."""
        return (not self.windows and _si(self.get("WITH_TAILSCALE", "si"))
                and not _si(self.get("TAILSCALE_HOST", "no")))

    @property
    def windows(self) -> bool:
        return self.get("PIATTAFORMA", "linux").lower() == "windows"

    def flag(self, chiave: str) -> bool:
        return _si(self.get(chiave, "no"))
