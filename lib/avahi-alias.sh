#!/usr/bin/env bash
# Annuncia "mediabox.local" nella rete di casa (mDNS) senza rinominare il PC.
# Avviato da systemd (mediabox-avahi.service). Se l'IP cambia, riannuncia.
set -euo pipefail

NOME="${1:-mediabox.local}"

ip_attuale() {
  ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}' | head -n1
}

pid=""
ip_annunciato=""
trap '[[ -n "$pid" ]] && kill "$pid" 2>/dev/null; exit 0' TERM INT
while true; do
  ip="$(ip_attuale || true)"
  if [[ -n "$ip" && "$ip" != "$ip_annunciato" ]]; then
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
    avahi-publish -a -R "$NOME" "$ip" &
    pid=$!
    ip_annunciato="$ip"
  fi
  # se avahi-publish si è chiuso, lo rilanciamo al prossimo giro
  if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
    ip_annunciato=""
  fi
  sleep 60
done
