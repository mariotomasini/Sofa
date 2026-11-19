"""Menu "Cosa guardi?": sceglie i servizi facoltativi, all'installazione e dopo.

Si apre con "mediabox impostazioni" (e la prima volta dall'installer).
Scrive le scelte nel file .env; poi lo script mediabox riapplica tutto.
Uscita: 0 = scelte salvate, 3 = nessuna modifica.
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path

from .impostazioni import leggi_env

NESSUNA_MODIFICA = 3

# chiave, descrizione, chiave API che serve quando è attivo
VOCI = [
    ("WITH_SONARR", "Serie TV (Sonarr le scarica e le ordina per stagioni)", "SONARR_API_KEY"),
    ("WITH_ANIME", "Anime (libreria separata in Jellyfin, gestiti da Sonarr)", "SONARR_API_KEY"),
    ("WITH_LIDARR", "Musica con Lidarr (scarica e ordina per artista e album)", "LIDARR_API_KEY"),
    ("WITH_BAZARR", "Sottotitoli automatici (Bazarr)", ""),
    ("WITH_FLARESOLVERR", "FlareSolverr (aiuta gli indexer protetti da Cloudflare)", ""),
]
PREDEFINITI = {"WITH_SONARR": "no", "WITH_ANIME": "no", "WITH_LIDARR": "no", "WITH_BAZARR": "si", "WITH_FLARESOLVERR": "si"}


def attivo(valori: dict[str, str], chiave: str) -> bool:
    return valori.get(chiave, PREDEFINITI.get(chiave, "no")).strip().lower() in {"si", "sì", "yes", "true", "1"}


def aggiorna_env(file: Path, modifiche: dict[str, str]) -> None:
    """Cambia solo le righe indicate, lasciando commenti e ordine come sono."""
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
    file.write_text("\n".join(nuove) + "\n", encoding="utf-8")


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


def _stampa(scelte: dict[str, str], prima_volta: bool) -> None:
    print()
    if prima_volta:
        print("  Cosa vuoi fare con mediabox? I film ci sono sempre. Puoi cambiare idea quando vuoi")
        print("  con il comando: mediabox impostazioni")
    else:
        print("  Impostazioni di mediabox")
    print()
    for i, (chiave, descrizione, _) in enumerate(VOCI, 1):
        segno = "✔" if attivo(scelte, chiave) else " "
        print(f"   [{segno}] {i}. {descrizione}")
    print(f"       6. Lingue dei sottotitoli: {scelte.get('LINGUE_SOTTOTITOLI', 'it,en')}")
    ntfy = scelte.get("NTFY_TOPIC", "") or "non impostate"
    print(f"       7. Notifiche sul telefono (ntfy): {ntfy}")
    print()
    print("   Scrivi un numero per attivare/disattivare, poi Invio.")
    print("   s = salva e applica    q = esci senza cambiare nulla")


def menu(cartella: Path, prima_volta: bool = False) -> int:
    file_env = cartella / ".env"
    originali = leggi_env(cartella / ".env.example")
    originali.update(leggi_env(file_env))
    chiavi = [v[0] for v in VOCI] + ["LINGUE_SOTTOTITOLI", "NTFY_TOPIC"]
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
        elif risposta == "6":
            nuove = lingue_valide(input("  ? Lingue, separate da virgola (es. it,en,ja): "))
            if nuove:
                scelte["LINGUE_SOTTOTITOLI"] = nuove
            else:
                print("    Usa codici di due lettere: it = italiano, en = inglese, ja = giapponese…")
        elif risposta == "7":
            testo = input("  ? Argomento ntfy (vuoto = nessuna notifica): ")
            scelte["NTFY_TOPIC"] = re.sub(r"[^A-Za-z0-9_-]", "", testo)
        else:
            print("    Non ho capito: scrivi un numero da 1 a 7, oppure s o q.")
    modifiche = modifiche_finali(originali, scelte)
    if not modifiche:
        print("  Nessuna modifica.")
        return NESSUNA_MODIFICA
    aggiorna_env(file_env, modifiche)
    spenti = [d for k, d, _ in VOCI if attivo(originali, k) and not attivo(scelte, k)]
    if spenti:
        print("  I servizi disattivati vengono spenti, ma le loro impostazioni restano salvate:")
        print("  se li riattivi ritrovi tutto com'era.")
    print("  ✔ Scelte salvate")
    return 0
