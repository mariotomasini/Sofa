"""Cosa rifare quando si cambiano le impostazioni: solo il necessario.

La pagina delle impostazioni sa quali righe del .env sono cambiate. Da lì:
- "genera" e "docker compose up" si fanno sempre: Docker ricrea solo i servizi con
  impostazioni diverse (es. Cerca se cambia la chiave TMDB), gli altri non si toccano;
- "prepara" (cartelle e permessi) solo se cambiano la cartella dei media o i servizi accesi;
- "configura" (le impostazioni dentro i programmi, via API) solo per i programmi
  che usano quella voce: le lingue dell'audio rifanno i profili di Radarr e Sonarr,
  i sottotitoli solo Bazarr, la chiave TMDB nessuno.

Il piano finisce in generato/impostazioni.piano, che lo script sul PC legge:
    prepara=si
    configura=radarr,sonarr        (vuoto = nessuno, "tutto" = tutti)
Una voce che qui non c'è rifà tutto: meglio lento che sbagliato.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

FILE = "generato/impostazioni.piano"
TUTTO = "tutto"

# voce del .env → programmi da riconfigurare
SERVIZI: dict[str, tuple[str, ...]] = {
    "WITH_SONARR": ("sonarr", "prowlarr", "bazarr", "jellyfin"),
    "WITH_ANIME": ("sonarr", "jellyfin"),
    "WITH_LIDARR": ("lidarr", "prowlarr", "jellyfin"),
    "WITH_BAZARR": ("bazarr",),
    "WITH_TGARR": ("tgarr", "prowlarr"),
    "WITH_FLARESOLVERR": ("prowlarr",),
    "WITH_BESZEL": ("beszel",),
    "LINGUE_AUDIO": ("radarr", "sonarr"),
    "DIMENSIONE_MAX_GB": ("radarr", "sonarr"),
    "LINGUE_SOTTOTITOLI": ("bazarr",),
    "TG_API_ID": ("tgarr",),
    "TG_API_HASH": ("tgarr",),
    "TG_PHONE": ("tgarr",),
    "NTFY_TOPIC": ("beszel",),
    "UTENTI_CASA": ("jellyfin",),
    # solo nelle variabili dei servizi (Cerca e altri): basta ricrearli, lo fa Docker
    "TMDB_API_KEY": (),
    "PULIZIA_VISTI": (),
    "MEDIA_DIR": (),
    # le legge lo script sul PC o l'installer di Windows
    "AGGIORNAMENTI_AUTOMATICI": (),
    "WIN_RINOMINA_PC": (),
    "WIN_RETE_PRIVATA": (),
    "WIN_NO_SOSPENSIONE": (),
}
PREPARA = {"MEDIA_DIR"} | {k for k in SERVIZI if k.startswith("WITH_")}
NOMI = {"qbittorrent": "qBittorrent", "radarr": "Radarr", "sonarr": "Sonarr", "lidarr": "Lidarr",
        "prowlarr": "Prowlarr", "tgarr": "Telegram (tgarr)", "bazarr": "Bazarr", "beszel": "Beszel",
        "jellyfin": "Jellyfin", "tvheadend": "TVHeadend"}


@dataclass
class Piano:
    prepara: bool = False
    configura: list[str] | None = field(default_factory=list)  # None = tutti

    def righe(self) -> str:
        servizi = TUTTO if self.configura is None else ",".join(self.configura)
        return f"prepara={'si' if self.prepara else 'no'}\nconfigura={servizi}\n"

    def descrizione(self) -> str:
        """In parole semplici, per la pagina."""
        if self.configura is None:
            return "Riconfiguro tutti i programmi."
        parti = []
        if self.configura:
            parti.append("aggiorno " + ", ".join(NOMI.get(s, s) for s in self.configura))
        if self.prepara:
            parti.append("preparo le cartelle")
        if not parti:
            return "Riavvio solo i programmi che usano queste impostazioni: ci vuole poco."
        testo = " e ".join(parti) + "; gli altri programmi non si toccano."
        return testo[0].upper() + testo[1:]


def piano(cambiate: set[str] | list[str]) -> Piano:
    cambiate = set(cambiate)
    risultato = Piano(prepara=bool(cambiate & PREPARA))
    servizi: list[str] = []
    for chiave in sorted(cambiate):
        if chiave.endswith("_API_KEY") and chiave != "TMDB_API_KEY":
            continue  # le chiavi dei programmi nuovi arrivano con la loro voce WITH_
        if chiave not in SERVIZI:
            return Piano(prepara=True, configura=None)
        servizi += [s for s in SERVIZI[chiave] if s not in servizi]
    risultato.configura = servizi
    return risultato


def leggi(cartella: Path) -> Piano | None:
    try:
        righe = dict(r.split("=", 1) for r in (cartella / FILE).read_text(encoding="utf-8").splitlines() if "=" in r)
    except OSError:
        return None
    servizi = righe.get("configura", "")
    return Piano(prepara=righe.get("prepara") == "si",
                 configura=None if servizi == TUTTO else [x for x in servizi.split(",") if x])


def unisci(a: Piano, b: Piano) -> Piano:
    configura = None if a.configura is None or b.configura is None else \
        a.configura + [s for s in b.configura if s not in a.configura]
    return Piano(prepara=a.prepara or b.prepara, configura=configura)


def scrivi(cartella: Path, p: Piano) -> Piano:
    """Salva il piano. Se il PC non ha ancora applicato quello di prima, li somma."""
    precedente = leggi(cartella)
    if precedente is not None:
        p = unisci(precedente, p)
    file = cartella / FILE
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(p.righe(), encoding="utf-8")
    return p
