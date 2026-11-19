#!/usr/bin/env bash
# shellcheck disable=SC2016  # le espressioni tra apici le valuta "verifica"
# Test delle funzioni di aggiornamento (eseguito da "make check").
set -euo pipefail
RADICE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../lib/comune.sh
source "$RADICE/lib/comune.sh"
# shellcheck source=../lib/aggiorna.sh
source "$RADICE/lib/aggiorna.sh"

fallimenti=0
verifica() { if eval "$2"; then echo "  ✔ $1"; else echo "  ✘ $1"; fallimenti=$((fallimenti + 1)); fi; }

verifica "v1.3.0 è più nuova di 1.2.0" 'piu_nuova v1.3.0 1.2.0'
verifica "1.10.0 è più nuova di 1.9.2" 'piu_nuova 1.10.0 1.9.2'
verifica "stessa versione non è più nuova" '! piu_nuova v1.2.0 1.2.0'
verifica "versione vecchia non è più nuova" '! piu_nuova 1.1.9 1.2.0'

cartella="$(mktemp -d)"
trap 'rm -rf "$cartella"' EXIT
MEDIABOX_DIR="$cartella"
ENV_FILE="$cartella/.env"
printf 'IMG_A=a:1\nIMG_B=b:1\n' > "$cartella/vecchio.example"
printf 'IMG_A=a:2\nIMG_B=b:2\nIMG_C=c:1\n' > "$cartella/.env.example"
printf 'IMG_A=a:1\nIMG_B=b:personale\nALTRO=x\n' > "$ENV_FILE"
aggiorna_versioni_immagini "$cartella/vecchio.example"
verifica "immagine non toccata segue la nuova versione" '[[ "$(leggi_env IMG_A "$ENV_FILE")" == "a:2" ]]'
verifica "immagine cambiata a mano resta com era" '[[ "$(leggi_env IMG_B "$ENV_FILE")" == "b:personale" ]]'
verifica "immagine nuova viene aggiunta" '[[ "$(leggi_env IMG_C "$ENV_FILE")" == "c:1" ]]'
verifica "le altre righe restano" '[[ "$(leggi_env ALTRO "$ENV_FILE")" == "x" ]]'

echo "1.2.0" > "$cartella/VERSION"
verifica "legge la versione installata" '[[ "$(versione_installata)" == "1.2.0" ]]'

if ((fallimenti)); then echo "✘ $fallimenti test falliti"; exit 1; fi
echo "✔ Test di aggiornamento"
