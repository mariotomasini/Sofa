#!/usr/bin/env bash
# Funzioni comuni a install.sh e mediabox (Linux).
# Questo file viene incluso con "source": non va eseguito da solo.

# Colori solo se stiamo scrivendo su un terminale
if [[ -t 1 ]]; then
  C_TITOLO=$'\033[1;36m'; C_OK=$'\033[32m'; C_AVVISO=$'\033[33m'; C_ERRORE=$'\033[31m'; C_FINE=$'\033[0m'
else
  C_TITOLO=""; C_OK=""; C_AVVISO=""; C_ERRORE=""; C_FINE=""
fi

titolo() { printf '\n%s== %s ==%s\n' "$C_TITOLO" "$1" "$C_FINE"; }
info()   { printf '  · %s\n' "$1"; }
ok()     { printf '%s  ✔ %s%s\n' "$C_OK" "$1" "$C_FINE"; }
avviso() { printf '%s  ! %s%s\n' "$C_AVVISO" "$1" "$C_FINE"; }

# errore "cosa è successo" "cosa fare" → esce
errore() {
  printf '%s  ✘ %s%s\n' "$C_ERRORE" "$1" "$C_FINE" >&2
  if [[ -n "${2:-}" ]]; then
    printf '    Cosa fare: %s\n' "$2" >&2
  fi
  exit 1
}

# chiedi_si_no "domanda" [s|n predefinito]
chiedi_si_no() {
  local domanda="$1" predefinito="${2:-s}" risposta suggerimento
  if [[ "$predefinito" == "s" ]]; then suggerimento="[S/n]"; else suggerimento="[s/N]"; fi
  while true; do
    read -r -p "  ? $domanda $suggerimento " risposta || risposta=""
    risposta="${risposta:-$predefinito}"
    case "${risposta,,}" in
      s|si|sì|y|yes) return 0 ;;
      n|no) return 1 ;;
      *) echo "    Rispondi s (sì) oppure n (no)." ;;
    esac
  done
}

# Legge un valore dal file .env (vuoto se manca)
leggi_env() {
  local chiave="$1" file="$2"
  [[ -f "$file" ]] || return 0
  grep -E "^${chiave}=" "$file" | tail -n1 | cut -d= -f2- || true
}

# Scrive (o sostituisce) CHIAVE=valore nel file .env
scrivi_env() {
  local chiave="$1" valore="$2" file="$3" temporaneo
  temporaneo="$(mktemp)"
  if [[ -f "$file" ]]; then
    grep -vE "^${chiave}=" "$file" > "$temporaneo" || true
  fi
  printf '%s=%s\n' "$chiave" "$valore" >> "$temporaneo"
  cat "$temporaneo" > "$file"
  rm -f "$temporaneo"
}

# Indirizzo IP del PC nella rete di casa
ip_locale() {
  local ip
  ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}' | head -n1)"
  if [[ -z "$ip" ]]; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  fi
  printf '%s' "$ip"
}

chiave_casuale() {
  od -An -tx1 -N16 /dev/urandom | tr -d ' \n'
}
