"""Riepilogo finale in italiano semplice."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .impostazioni import Impostazioni
from .messaggi import CROCE, SPUNTA, ErroreMediabox, titolo


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

    def salva(self, file: Path) -> None:
        """Per la pagina di configurazione nel browser."""
        dati = {
            "riusciti": self.riusciti,
            "falliti": [{"nome": n, "cosa": e.cosa, "cosa_fare": e.cosa_fare} for n, e in self.falliti],
            "da_fare": self.cose_da_fare + ([] if any("indexer" in d for d in self.cose_da_fare) else [
                "Aggiungi i tuoi indexer in Prowlarr (pagina /prowlarr → Indexer): "
                "servono per trovare i film da scaricare."]),
            "note": self.note,
        }
        try:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass


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
        ("Cerca", "/", False),
        ("Jellyfin", "/jellyfin/", False),
        ("qBittorrent", "/qbit/", True),
        ("Radarr", "/radarr/", True),
        ("Prowlarr", "/prowlarr/", True),
    ]
    if imp.sonarr:
        elenco.append(("Sonarr", "/sonarr/", True))
    if imp.lidarr:
        elenco.append(("Lidarr", "/lidarr/", True))
    if imp.navidrome:
        elenco.append(("Navidrome", "/navidrome/", False))
    if imp.bazarr:
        elenco.append(("Bazarr", "/bazarr/", True))
    if imp.beszel:
        elenco.append(("Beszel", "/beszel/", False))
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
    # il Pannello (tessere dei servizi e stato del PC) ha la sua porta
    senza_porta = principale.rsplit(":", 1)[0] if principale.count(":") > 1 else principale
    print(f"    {'Pannello':<16} {senza_porta}:{imp.get('PANNELLO_PORT', '8081') or '8081'}/")
    print(f"\n  Utente: {imp.utente}   Password: quella scelta durante l'installazione")
    if imp.beszel:
        print(f"  Per Beszel l'utente è l'email {imp.utente}@mediabox.local (stessa password)")
    print("  Nell'app Jellyfin della TV scrivi come server:")
    print(f"    {principale}/jellyfin")
    if imp.navidrome:
        print("  Nell'app della musica (Amperfy, Symfonium…) scrivi come server:")
        print(f"    {principale}/navidrome")
    if imp.immich:
        from .servizi.immich import indirizzo_telefono

        print("  Nell'app Immich (foto) scrivi come server:")
        print(f"    {indirizzo_telefono(imp)}")


def stampa_riepilogo(imp: Impostazioni, esito: Esito) -> None:
    stampa_indirizzi(imp)

    titolo("Cosa è attivo")
    attivi = ["Jellyfin", "Cerca", "qBittorrent (upload ridotto al minimo)", "Radarr", "Prowlarr", "Homepage"]
    if imp.sonarr:
        attivi.append("Sonarr (serie" + (" e anime)" if imp.anime else ")"))
    if imp.lidarr:
        attivi.append("Lidarr (musica)")
    if imp.navidrome:
        attivi.append("Navidrome (musica nelle app: Amperfy, Symfonium, Feishin)")
    if imp.immich:
        attivi.append("Foto (Immich: backup dei telefoni e volti, ognuno vede solo le sue"
                      + (", copia notturna su un altro disco" if imp.foto_copia else "")
                      + (", copia cifrata nel cloud)" if imp.foto_cloud else ")"))
    if imp.bazarr:
        attivi.append("Bazarr (sottotitoli: " + ", ".join(imp.lingue_sottotitoli) + ")")
    if imp.flaresolverr:
        attivi.append("FlareSolverr (indexer protetti da Cloudflare)")
    if imp.tgarr:
        attivi.append(f"tgarr (Telegram come fonte, pagina sulla porta {imp.get('TGARR_PORT', '8765')})")
    if imp.beszel:
        attivi.append("Beszel (grafici e avvisi del PC)")
    if imp.dvb:
        attivi.append("TVHeadend (tuner USB)")
    if imp.hdhomerun:
        attivi.append(f"HDHomeRun ({imp.hdhomerun})")
    for a in attivi:
        print(f"  {SPUNTA} {a}")
    if not imp.dvb and not imp.hdhomerun:
        print("  – TV in diretta: nessun tuner trovato, saltata. Se ne colleghi uno, rilancia l'installer.")
    print(f"  {SPUNTA if imp.vaapi else '–'} Transcodifica hardware {'attiva' if imp.vaapi else 'non disponibile'}")
    print(f"  {SPUNTA if imp.get('NTFY_TOPIC') else '–'} Notifiche sul telefono (ntfy)"
          + (f": argomento \"{imp.get('NTFY_TOPIC')}\"" if imp.get("NTFY_TOPIC") else ": non impostate"))

    for n in esito.note:
        print(f"  · {n}")

    if esito.falliti:
        titolo("Cosa non è andato")
        for nome, errore in esito.falliti:
            print(f"  {CROCE} {nome}: {errore.cosa}")
            if errore.cosa_fare:
                print(f"      Cosa fare: {errore.cosa_fare}")

    da_fare = list(esito.cose_da_fare)
    if not any("indexer" in d for d in da_fare):
        da_fare.append("Se non l'hai già fatto, aggiungi i tuoi indexer in Prowlarr (/prowlarr → Indexer).")
    titolo("Cosa resta da fare a mano")
    for i, d in enumerate(da_fare, 1):
        print(f"  {i}. {d}")
    print()
