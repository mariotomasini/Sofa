"""Punto di ingresso del configuratore.

Uso (dentro il container):
    python -m configuratore genera      # scrive i file in generato/
    python -m configuratore prepara     # cartelle, permessi, qBittorrent
    python -m configuratore configura   # configura i servizi via API
    python -m configuratore riepilogo   # stampa indirizzi e stato
    python -m configuratore backup
    python -m configuratore ripristina <file>
"""

from __future__ import annotations

import argparse
import sys

from .impostazioni import CARTELLA_MEDIABOX, Impostazioni
from .messaggi import ErroreMediabox, avviso, titolo


def _configura(imp: Impostazioni) -> int:
    from .riepilogo import Esito, stampa_riepilogo
    from .servizi import jellyfin, prowlarr, qbittorrent, radarr, sonarr, tvheadend

    esito = Esito()
    passi = [
        ("qBittorrent", qbittorrent.configura),
        ("Radarr", radarr.configura),
    ]
    if imp.sonarr:
        passi.append(("Sonarr", sonarr.configura))
    passi.append(("Prowlarr", prowlarr.configura))
    if imp.dvb:
        passi.append(("TVHeadend", tvheadend.configura))
    passi.append(("Jellyfin", jellyfin.configura))

    for nome, funzione in passi:
        titolo(f"Configuro {nome}")
        try:
            funzione(imp, esito)
            esito.ok(nome)
        except ErroreMediabox as exc:
            exc.mostra()
            esito.fallito(nome, exc)
    stampa_riepilogo(imp, esito)
    return 1 if esito.falliti else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="configuratore", description="Configuratore di mediabox")
    sotto = parser.add_subparsers(dest="comando", required=True)
    sotto.add_parser("genera")
    sotto.add_parser("prepara")
    sotto.add_parser("configura")
    sotto.add_parser("riepilogo")
    sotto.add_parser("indirizzi")
    sotto.add_parser("backup")
    sotto.add_parser("hdhomerun")
    rip = sotto.add_parser("ripristina")
    rip.add_argument("file")
    args = parser.parse_args(argv)

    if args.comando == "hdhomerun":
        from .hdhomerun import main as cerca_hdhomerun

        return cerca_hdhomerun()

    try:
        imp = Impostazioni.carica()
        if args.comando == "genera":
            from .genera import genera

            genera(imp, CARTELLA_MEDIABOX)
        elif args.comando == "prepara":
            from .prepara import prepara

            prepara(imp, CARTELLA_MEDIABOX)
        elif args.comando == "configura":
            return _configura(imp)
        elif args.comando in ("riepilogo", "indirizzi"):
            from .riepilogo import Esito, stampa_indirizzi, stampa_riepilogo

            if args.comando == "indirizzi":
                stampa_indirizzi(imp)
            else:
                stampa_riepilogo(imp, Esito())
        elif args.comando == "backup":
            from .backup import backup

            backup(imp, CARTELLA_MEDIABOX)
        elif args.comando == "ripristina":
            from .backup import ripristina

            ripristina(imp, CARTELLA_MEDIABOX, args.file)
    except ErroreMediabox as exc:
        exc.mostra()
        return 1
    except KeyboardInterrupt:
        avviso("Interrotto.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
