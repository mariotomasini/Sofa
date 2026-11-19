"""Riepilogo finale in italiano semplice."""

from __future__ import annotations

from dataclasses import dataclass, field

from .impostazioni import Impostazioni
from .messaggi import ErroreMediabox, titolo


@dataclass
class Esito:
    riusciti: list[str] = field(default_factory=list)
    falliti: list[tuple[str, ErroreMediabox]] = field(default_factory=list)
    cose_da_fare: list[str] = field(default_factory=list)
    note: list[str] = field(default_factory=list)

    def ok(self, nome: str) -> None:
        self.riusciti.append(nome)

    def fallito(self, nome: str, errore: ErroreMediabox) -> None:
        self.falliti.append((nome, errore))

    def da_fare(self, testo: str) -> None:
        if testo not in self.cose_da_fare:
            self.cose_da_fare.append(testo)

    def nota(self, testo: str) -> None:
        if testo not in self.note:
            self.note.append(testo)


def indirizzi_base(imp: Impostazioni) -> list[str]:
    porta = imp.get("HTTP_PORT", "80")
    suffisso = "" if porta == "80" else f":{porta}"
    base = [f"http://{imp.get('NOME_HOST', 'mediabox.local')}{suffisso}"]
    if imp.get("HOST_IP"):
        base.append(f"http://{imp.get('HOST_IP')}{suffisso}")
    if imp.get("TAILSCALE_IP"):
        base.append(f"http://{imp.get('TAILSCALE_IP')}{suffisso}  (da fuori casa, con Tailscale)")
    return base


def pagine(imp: Impostazioni) -> list[tuple[str, str, bool]]:
    """(nome, percorso, protetto da password)"""
    elenco = [
        ("Pagina iniziale", "/", False),
        ("Cerca un film", "/cerca/", False),
        ("Jellyfin", "/jellyfin/", False),
        ("qBittorrent", "/qbit/", True),
        ("Radarr", "/radarr/", True),
        ("Prowlarr", "/prowlarr/", True),
    ]
    if imp.sonarr:
        elenco.append(("Sonarr", "/sonarr/", True))
    if imp.dvb:
        elenco.append(("TVHeadend", "/tv/", True))
    return elenco


def stampa_indirizzi(imp: Impostazioni) -> None:
    titolo("Indirizzi di mediabox")
    basi = indirizzi_base(imp)
    print("  Apri uno di questi indirizzi dal browser (telefono, PC o TV):")
    for b in basi:
        print(f"    {b}")
    principale = basi[-1] if len(basi) > 1 and "Tailscale" not in basi[-1] else basi[0]
    principale = principale.split()[0]
    print("\n  Pagine:")
    for nome, percorso, protetto in pagine(imp):
        lucchetto = "  (utente e password di amministrazione)" if protetto else ""
        print(f"    {nome:<16} {principale}{percorso}{lucchetto}")
    print(f"\n  Utente: {imp.utente}   Password: quella scelta durante l'installazione")
    print("  Nell'app Jellyfin della TV scrivi come server:")
    print(f"    {principale}/jellyfin")


def stampa_riepilogo(imp: Impostazioni, esito: Esito) -> None:
    stampa_indirizzi(imp)

    titolo("Cosa è attivo")
    attivi = ["Jellyfin", "Cerca", "qBittorrent (upload ridotto al minimo)", "Radarr", "Prowlarr", "Homepage"]
    if imp.sonarr:
        attivi.append("Sonarr")
    if imp.dvb:
        attivi.append("TVHeadend (tuner USB)")
    if imp.hdhomerun:
        attivi.append(f"HDHomeRun ({imp.hdhomerun})")
    for a in attivi:
        print(f"  ✔ {a}")
    if not imp.dvb and not imp.hdhomerun:
        print("  – TV in diretta: nessun tuner trovato, saltata. Se ne colleghi uno, rilancia l'installer.")
    print(f"  {'✔' if imp.vaapi else '–'} Transcodifica hardware {'attiva' if imp.vaapi else 'non disponibile'}")
    print(f"  {'✔' if imp.get('NTFY_TOPIC') else '–'} Notifiche sul telefono (ntfy)"
          + (f": argomento \"{imp.get('NTFY_TOPIC')}\"" if imp.get("NTFY_TOPIC") else ": non impostate"))

    for n in esito.note:
        print(f"  · {n}")

    if esito.falliti:
        titolo("Cosa non è andato")
        for nome, errore in esito.falliti:
            print(f"  ✘ {nome}: {errore.cosa}")
            if errore.cosa_fare:
                print(f"      Cosa fare: {errore.cosa_fare}")

    da_fare = list(esito.cose_da_fare)
    if not any("indexer" in d for d in da_fare):
        da_fare.append("Se non l'hai già fatto, aggiungi i tuoi indexer in Prowlarr (/prowlarr → Indexer).")
    titolo("Cosa resta da fare a mano")
    for i, d in enumerate(da_fare, 1):
        print(f"  {i}. {d}")
    print()
