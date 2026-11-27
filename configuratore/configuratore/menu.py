"""Menu "Cosa guardi?": sceglie i servizi facoltativi, all'installazione e dopo.

Si apre con "mediabox impostazioni" (e la prima volta dall'installer).
Scrive le scelte nel file .env; poi lo script mediabox riapplica tutto.
Uscita: 0 = scelte salvate, 3 = nessuna modifica.
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path

from .impostazioni import leggi_env, scrivi_atomico
from .messaggi import SPUNTA

NESSUNA_MODIFICA = 3

# chiave, descrizione, chiave API che serve quando è attivo
VOCI = [
    ("WITH_SONARR", "Serie TV (Sonarr le scarica e le ordina per stagioni)", "SONARR_API_KEY"),
    ("WITH_ANIME", "Anime (libreria separata in Jellyfin, gestiti da Sonarr)", "SONARR_API_KEY"),
    ("WITH_LIDARR", "Musica con Lidarr (scarica e ordina per artista e album)", "LIDARR_API_KEY"),
    ("WITH_BAZARR", "Sottotitoli automatici (Bazarr)", ""),
    ("WITH_TGARR", "Telegram come fonte di download (tgarr: canali e gruppi del tuo account)", ""),
    ("WITH_FLARESOLVERR", "FlareSolverr (aiuta gli indexer protetti da Cloudflare)", ""),
    ("PULIZIA_VISTI", "Pulizia automatica dei film ed episodi già visti quando il disco è quasi pieno", ""),
    ("WITH_BESZEL", "Beszel (grafici di processore, memoria, dischi e temperatura, con avvisi)", ""),
    ("AGGIORNAMENTI_AUTOMATICI", "Aggiornamenti automatici di mediabox quando esce una nuova versione", ""),
    ("WITH_NAVIDROME", "Musica nelle app per telefono (Navidrome: Amperfy, Symfonium, Feishin)", ""),
    ("WITH_IMMICH", "Foto dei telefoni con i volti (Immich: ognuno vede solo le sue)", ""),
]
PREDEFINITI = {
    "WITH_SONARR": "no", "WITH_ANIME": "no", "WITH_LIDARR": "no", "WITH_NAVIDROME": "si", "WITH_IMMICH": "no",
    "WITH_BAZARR": "si",
    "WITH_TGARR": "si",
    "WITH_FLARESOLVERR": "si", "WITH_BESZEL": "no", "AGGIORNAMENTI_AUTOMATICI": "si", "PULIZIA_VISTI": "no",
}


def attivo(valori: dict[str, str], chiave: str) -> bool:
    return valori.get(chiave, PREDEFINITI.get(chiave, "no")).strip().lower() in {"si", "sì", "yes", "true", "1"}


def aggiorna_env(file: Path, modifiche: dict[str, str]) -> None:
    """Cambia solo le righe indicate, lasciando commenti e ordine come sono."""
    for chiave, valore in modifiche.items():
        # un a capo in un valore aggiungerebbe righe (e impostazioni) che nessuno ha scelto
        if not re.fullmatch(r"[A-Z0-9_]+", chiave) or any(c in str(valore) for c in "\r\n\x00"):
            raise ValueError(f"Valore non valido per {chiave!r} nel file .env")
    righe = file.read_text(encoding="utf-8").splitlines() if file.exists() else []
    restanti = dict(modifiche)
    nuove = []
    for riga in righe:
        trovata = re.match(r"^([A-Z0-9_]+)=", riga)
        if trovata and trovata.group(1) in restanti:
            nuove.append(f"{trovata.group(1)}={restanti.pop(trovata.group(1))}")
        elif trovata and trovata.group(1) in modifiche:
            continue  # chiave ripetuta: teniamo solo la prima
        else:
            nuove.append(riga)
    nuove.extend(f"{k}={v}" for k, v in restanti.items())
    scrivi_atomico(file, "\n".join(nuove) + "\n")


def lingue_valide(testo: str) -> str | None:
    codici = [c.strip().lower() for c in testo.replace(" ", ",").split(",") if c.strip()]
    if not codici or not all(re.fullmatch(r"[a-z]{2}", c) for c in codici):
        return None
    return ",".join(dict.fromkeys(codici))


def modifiche_finali(originali: dict[str, str], scelte: dict[str, str]) -> dict[str, str]:
    """Le righe da scrivere: solo quelle cambiate, più le chiavi API che mancano."""
    risultato = {k: v for k, v in scelte.items() if originali.get(k, PREDEFINITI.get(k, "")) != v}
    for chiave, _, chiave_api in VOCI:
        if chiave_api and attivo(scelte, chiave) and not originali.get(chiave_api) and chiave_api not in risultato:
            risultato[chiave_api] = secrets.token_hex(16)
    return risultato


def percorso_valido(testo: str, windows: bool) -> str | None:
    """Percorso assoluto della cartella dei media (su Windows con la lettera del disco)."""
    percorso = testo.strip().strip('"').replace("\\", "/").rstrip("/")
    if windows:
        return percorso if re.fullmatch(r"[A-Za-z]:(/[^<>:\"|?*\x00-\x1f]*)?", percorso) and len(percorso) > 2 else None
    return percorso if percorso.startswith("/") and len(percorso) > 1 and "\n" not in percorso else None


def _stampa(scelte: dict[str, str], prima_volta: bool) -> None:
    print()
    if prima_volta:
        print("  Cosa vuoi fare con mediabox? I film ci sono sempre. Puoi cambiare idea quando vuoi")
        print("  con il comando: mediabox impostazioni")
    else:
        print("  Impostazioni di mediabox")
    print()
    for i, (chiave, descrizione, _) in enumerate(VOCI, 1):
        segno = SPUNTA if attivo(scelte, chiave) else " "
        print(f"   [{segno}] {i}. {descrizione}")
    print()
    print(f"       l. Lingue dei sottotitoli: {scelte.get('LINGUE_SOTTOTITOLI', 'it,en')}")
    print(f"       n. Notifiche sul telefono (ntfy): {scelte.get('NTFY_TOPIC', '') or 'non impostate'}")
    print(f"       c. Cartella di film, musica e download: {scelte.get('MEDIA_DIR', '')}")
    print()
    print("   Scrivi un numero per attivare/disattivare, oppure una lettera per cambiare, poi Invio.")
    print("   s = salva e applica    q = esci senza cambiare nulla")


def menu(cartella: Path, prima_volta: bool = False) -> int:
    file_env = cartella / ".env"
    originali = leggi_env(cartella / ".env.example")
    originali.update(leggi_env(file_env))
    windows = originali.get("PIATTAFORMA", "linux").lower() == "windows"
    chiavi = [v[0] for v in VOCI] + ["LINGUE_SOTTOTITOLI", "NTFY_TOPIC", "MEDIA_DIR"]
    scelte = {k: originali.get(k, PREDEFINITI.get(k, "")) for k in chiavi}
    while True:
        _stampa(scelte, prima_volta)
        try:
            risposta = input("  ? Scelta: ").strip().lower()
        except EOFError:
            risposta = "s" if prima_volta else "q"
        if risposta in {"q", "esci"}:
            return NESSUNA_MODIFICA
        if risposta in {"s", "salva", ""} and (risposta or prima_volta):
            break
        if risposta.isdigit() and 1 <= int(risposta) <= len(VOCI):
            chiave = VOCI[int(risposta) - 1][0]
            scelte[chiave] = "no" if attivo(scelte, chiave) else "si"
            if chiave == "WITH_ANIME" and attivo(scelte, "WITH_ANIME"):
                print("    (gli anime usano Sonarr: verrà installato anche quello)")
        elif risposta == "l":
            nuove = lingue_valide(input("  ? Lingue, separate da virgola (es. it,en,ja): "))
            if nuove:
                scelte["LINGUE_SOTTOTITOLI"] = nuove
            else:
                print("    Usa codici di due lettere: it = italiano, en = inglese, ja = giapponese…")
        elif risposta == "n":
            testo = input("  ? Argomento ntfy (vuoto = nessuna notifica): ")
            scelte["NTFY_TOPIC"] = re.sub(r"[^A-Za-z0-9_-]", "", testo)
        elif risposta == "c":
            esempio = "D:\\Mediabox" if windows else "/mnt/disco/mediabox"
            nuovo = percorso_valido(input(f"  ? Nuova cartella (es. {esempio}): "), windows)
            if nuovo:
                scelte["MEDIA_DIR"] = nuovo
                if not prima_volta and originali.get("MEDIA_DIR") and nuovo != originali.get("MEDIA_DIR"):
                    print("    Film, musica e download già presenti verranno spostati lì al salvataggio.")
            else:
                print(f"    Scrivi il percorso completo della cartella, es. {esempio}")
        else:
            print(f"    Non ho capito: scrivi un numero da 1 a {len(VOCI)}, una lettera (l, n, c), s oppure q.")
    modifiche = modifiche_finali(originali, scelte)
    if not modifiche:
        print("  Nessuna modifica.")
        return NESSUNA_MODIFICA
    aggiorna_env(file_env, modifiche)
    spenti = [d for k, d, _ in VOCI if attivo(originali, k) and not attivo(scelte, k)]
    if spenti:
        print("  I servizi disattivati vengono spenti, ma le loro impostazioni restano salvate:")
        print("  se li riattivi ritrovi tutto com'era.")
    print(f"  {SPUNTA} Scelte salvate")
    return 0
