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

cartella="$(mktemp -d)"
trap 'rm -rf "$cartella"' EXIT
MEDIABOX_DIR="$cartella"
ENV_FILE="$cartella/.env"
mkdir -p "$cartella/generato"
printf '{\n  "cerca": false,\n  "configuratore": true,\n  "configura": true\n}\n' > "$cartella/generato/aggiornamento.json"
verifica "il piano dice cosa rifare" 'nel_piano configuratore && nel_piano configura'
verifica "il piano dice cosa lasciare" '! nel_piano cerca'
rm "$cartella/generato/aggiornamento.json"
verifica "senza piano non si rifà niente" '! nel_piano cerca'

stato_aggiornamento installo "Aggiorno i programmi cambiati…"
verifica "lo stato per la pagina è JSON valido" \
  'python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert d[\"fase\"]==\"installo\"" "$cartella/aggiornamento.stato"'

echo "1.2.0" > "$cartella/VERSION"
verifica "legge la versione installata" '[[ "$(versione_installata)" == "1.2.0" ]]'

if ((fallimenti)); then echo "✘ $fallimenti test falliti"; exit 1; fi
echo "✔ Test di aggiornamento"
