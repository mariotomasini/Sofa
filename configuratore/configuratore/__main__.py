"""Punto di ingresso del configuratore.

Uso (dentro il container):
    python -m configuratore genera      # scrive i file in generato/
    python -m configuratore prepara     # cartelle, permessi, qBittorrent
    python -m configuratore configura   # configura i servizi via API
    python -m configuratore riepilogo   # stampa indirizzi e stato
    python -m configuratore backup
    python -m configuratore ripristina <file>
    python -m configuratore web         # configurazione dal browser (porta 8080)
    python -m configuratore leggero     # sui PC con poca memoria spegne i programmi pesanti
"""

from __future__ import annotations

import argparse
import sys

from .impostazioni import CARTELLA_MEDIABOX, Impostazioni
from .messaggi import ErroreMediabox, avviso, titolo


def errore_imprevisto(nome: str, exc: Exception) -> ErroreMediabox:
    """Trasforma un errore tecnico (es. httpx.ReadTimeout) in un messaggio comprensibile."""
    import httpx

    if isinstance(exc, httpx.TimeoutException):
        return ErroreMediabox(
            f"{nome} risponde troppo lentamente.",
            "Il PC è molto carico: aspetta qualche minuto e riprova con \"mediabox configura\". "
            "Se succede spesso, spegni dalle impostazioni i servizi che non usi (FlareSolverr, Beszel, Lidarr).",
        )
    if isinstance(exc, httpx.HTTPError):
        return ErroreMediabox(
            f"{nome} non risponde ({type(exc).__name__}).",
            f"Guarda i messaggi con \"mediabox logs {nome.lower()}\", poi riprova con \"mediabox configura\".",
        )
    return ErroreMediabox(
        f"Errore imprevisto con {nome}: {type(exc).__name__}: {exc}",
        "Riprova con \"mediabox configura\". Se si ripete, manda questo messaggio a chi ti aiuta con mediabox.",
    )


def _configura(imp: Impostazioni, solo: set[str] | None = None) -> int:
    """Configura i servizi via API. Con `solo` (es. {"radarr", "sonarr"}) solo quelli."""
    from .riepilogo import Esito, stampa_riepilogo
    from .servizi import (
        bazarr,
        beszel,
        immich,
        jellyfin,
        lidarr,
        navidrome,
        prowlarr,
        qbittorrent,
        radarr,
        sonarr,
        tgarr,
        tvheadend,
    )

    esito = Esito()
    passi = [
        ("qBittorrent", qbittorrent.configura),
        ("Radarr", radarr.configura),
    ]
    if imp.sonarr:
        passi.append(("Sonarr", sonarr.configura))
    if imp.lidarr:
        passi.append(("Lidarr", lidarr.configura))
    passi.append(("Prowlarr", prowlarr.configura))
    if imp.tgarr:
        passi.append(("Telegram (tgarr)", tgarr.configura))
    if imp.bazarr:
        passi.append(("Bazarr", bazarr.configura))
    if imp.dvb:
        passi.append(("TVHeadend", tvheadend.configura))
    if imp.beszel:
        passi.append(("Beszel", beszel.configura))
    passi.append(("Jellyfin", jellyfin.configura))
    if imp.navidrome:
        passi.append(("Navidrome", navidrome.configura))
    if imp.immich:
        passi.append(("Foto (Immich)", immich.configura))
    if solo is not None:
        codici = {"Telegram (tgarr)": "tgarr", "Foto (Immich)": "immich"}
        passi = [(nome, f) for nome, f in passi if codici.get(nome, nome.lower()) in solo]
        if not passi:
            return 0

    for numero, (nome, funzione) in enumerate(passi, 1):
        titolo(f"Configuro {nome} ({numero} di {len(passi)})")
        try:
            funzione(imp, esito)
            esito.ok(nome)
        except ErroreMediabox as exc:
            exc.mostra()
            esito.fallito(nome, exc)
        except Exception as exc:  # noqa: BLE001 — un servizio che non va non deve fermare gli altri
            problema = errore_imprevisto(nome, exc)
            problema.mostra()
            esito.fallito(nome, problema)
    stampa_riepilogo(imp, esito)
    esito.salva(CARTELLA_MEDIABOX / "generato" / "esito.json")
    return 1 if esito.falliti else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="configuratore", description="Configuratore di mediabox")
    sotto = parser.add_subparsers(dest="comando", required=True)
    sotto.add_parser("genera")
    sotto.add_parser("prepara")
    conf = sotto.add_parser("configura")
    conf.add_argument("--solo", default="", help="solo questi servizi, separati da virgola (es. radarr,sonarr)")
    sotto.add_parser("riepilogo")
    sotto.add_parser("indirizzi")
    sotto.add_parser("backup")
    sotto.add_parser("hdhomerun")
    imp_menu = sotto.add_parser("impostazioni")
    imp_menu.add_argument("--prima-volta", action="store_true")
    sotto.add_parser("leggero")
    agg = sotto.add_parser("aggiornamento")
    agg.add_argument("azione", choices=["controlla", "installa"])
    web = sotto.add_parser("web")
    web.add_argument("--porta", type=int, default=8080)
    rip = sotto.add_parser("ripristina")
    rip.add_argument("file")
    args = parser.parse_args(argv)

    if args.comando == "impostazioni":
        from .menu import menu

        return menu(CARTELLA_MEDIABOX, prima_volta=args.prima_volta)

    if args.comando == "leggero":
        from .leggero import main as leggero

        return leggero(CARTELLA_MEDIABOX)

    if args.comando == "aggiornamento":
        from .aggiornamenti import main as aggiornamento

        return aggiornamento(CARTELLA_MEDIABOX, args.azione)

    if args.comando == "web":
        import threading

        import uvicorn

        from .aggiornamenti import sorveglia
        from .web.app import crea_app

        # ogni minuto: c'è una versione nuova su GitHub? Se sì, l'aggiornamento parte da solo
        threading.Thread(target=sorveglia, args=(CARTELLA_MEDIABOX,), daemon=True, name="aggiornamenti").start()
        uvicorn.run(crea_app(CARTELLA_MEDIABOX), host="0.0.0.0", port=args.porta, log_level="warning")  # noqa: S104
        return 0

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
            solo = {x.strip() for x in args.solo.split(",") if x.strip()} if args.solo else None
            return _configura(imp, solo)
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
