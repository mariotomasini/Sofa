#!/usr/bin/env bash
# Aggiornamento di mediabox da GitHub (Linux).
# Richiede: MEDIABOX_DIR, ENV_FILE e lib/comune.sh, lib/docker.sh già inclusi.

versione_installata() {
  tr -d '[:space:]' < "$MEDIABOX_DIR/VERSION" 2>/dev/null || echo "0.0.0"
}

# Stampa "tag url_archivio" dell'ultima release pubblicata (niente se non ce ne sono)
ultima_release() {
  local repo
  repo="$(leggi_env MEDIABOX_REPO "$ENV_FILE")"
  repo="${repo:-mariotomasini/Sofa}"
  local json tag archivio
  json="$(curl -fsSL -m 30 -H "Accept: application/vnd.github+json" \
    "${MEDIABOX_GITHUB_API:-https://api.github.com}/repos/$repo/releases/latest" 2>/dev/null)" || return 0
  # niente python o jq: bastano due righe del JSON
  tag="$(printf '%s' "$json" | grep -o '"tag_name": *"[^"]*"' | head -n1 | sed 's/.*"\([^"]*\)"$/\1/')"
  archivio="$(printf '%s' "$json" | grep -o '"tarball_url": *"[^"]*"' | head -n1 | sed 's/.*"\([^"]*\)"$/\1/')"
  [[ -n "$tag" && -n "$archivio" ]] && printf '%s %s\n' "$tag" "$archivio"
  return 0
}

# Vero se $1 è una versione più nuova di $2 (es. v1.3.0 > 1.2.0)
piu_nuova() {
  local a="${1#v}" b="${2#v}"
  [[ "$a" != "$b" && "$(printf '%s\n%s\n' "$a" "$b" | sort -V | tail -n1)" == "$a" ]]
}

notifica_ntfy() {
  local topic server
  topic="$(leggi_env NTFY_TOPIC "$ENV_FILE")"
  [[ -n "$topic" ]] || return 0
  server="$(leggi_env NTFY_SERVER "$ENV_FILE")"
  curl -fsS -m 20 -H "Title: mediabox" -d "$1" "${server:-https://ntfy.sh}/$topic" >/dev/null 2>&1 || true
}

# Le versioni delle immagini seguono quelle nuove, salvo quelle cambiate a mano nel .env
aggiorna_versioni_immagini() {
  local vecchio_esempio="$1" chiave nuovo_valore attuale vecchio_valore
  while IFS='=' read -r chiave nuovo_valore; do
    attuale="$(leggi_env "$chiave" "$ENV_FILE")"
    vecchio_valore="$(leggi_env "$chiave" "$vecchio_esempio")"
    if [[ -z "$attuale" || "$attuale" == "$vecchio_valore" ]]; then
      scrivi_env "$chiave" "$nuovo_valore" "$ENV_FILE"
    fi
  done < <(grep -E '^IMG_[A-Z_]+=' "$MEDIABOX_DIR/.env.example")
}

# Scarica e installa la nuova versione del codice. Restituisce 1 se non c'è niente di nuovo.
aggiorna_codice() {
  local riga tag archivio temporanea vecchio_esempio
  riga="$(ultima_release)"
  if [[ -z "$riga" ]]; then
    info "Nessuna versione pubblicata su GitHub (o GitHub non risponde)."
    return 1
  fi
  tag="${riga%% *}"
  archivio="${riga#* }"
  if ! piu_nuova "$tag" "$(versione_installata)"; then
    ok "mediabox è già all'ultima versione ($(versione_installata))"
    return 1
  fi
  titolo "Nuova versione di mediabox: $tag (installata: $(versione_installata))"
  temporanea="$(mktemp -d)"
  curl -fsSL -m 300 "$archivio" -o "$temporanea/mediabox.tar.gz" \
    || { rm -rf "$temporanea"; errore "Non riesco a scaricare la nuova versione." "Riprova più tardi con: mediabox update"; }
  mkdir "$temporanea/nuova"
  tar -xzf "$temporanea/mediabox.tar.gz" -C "$temporanea/nuova" --strip-components=1
  [[ -f "$temporanea/nuova/mediabox" && -f "$temporanea/nuova/VERSION" ]] \
    || { rm -rf "$temporanea"; errore "L'archivio scaricato non sembra mediabox." "Controlla MEDIABOX_REPO nel file .env."; }
  vecchio_esempio="$temporanea/vecchio.env.example"
  cp "$MEDIABOX_DIR/.env.example" "$vecchio_esempio"
  # il file .env, i file generati e i backup restano dove sono
  tar -C "$temporanea/nuova" --exclude=.git --exclude=.env --exclude=generato --exclude=backup -cf - . \
    | tar -C "$MEDIABOX_DIR" -xf -
  chmod +x "$MEDIABOX_DIR/mediabox" "$MEDIABOX_DIR/install.sh" "$MEDIABOX_DIR"/lib/*.sh
  aggiorna_versioni_immagini "$vecchio_esempio"
  rm -rf "$temporanea"
  ok "Codice aggiornato a $tag"
  return 0
}

# Timer giornaliero (systemd) che controlla se è uscita una nuova versione
installa_aggiornamenti_automatici() {
  if [[ ! -d /run/systemd/system ]]; then
    avviso "Questo sistema non usa systemd: aggiornamenti automatici non attivati (usa \"mediabox update\")."
    return 0
  fi
  # Il timer c'è sempre: se gli aggiornamenti sono spenti nelle impostazioni, non fa nulla
  sudo tee /etc/systemd/system/mediabox-aggiorna.service >/dev/null <<UNIT
[Unit]
Description=mediabox: controlla e installa gli aggiornamenti
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
User=$(id -un)
ExecStart=$MEDIABOX_DIR/mediabox aggiorna-automatico
UNIT
  sudo tee /etc/systemd/system/mediabox-aggiorna.timer >/dev/null <<'UNIT'
[Unit]
Description=mediabox: aggiornamento automatico ogni notte

[Timer]
OnCalendar=*-*-* 04:20
RandomizedDelaySec=40min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
  sudo systemctl daemon-reload
  sudo systemctl enable --now mediabox-aggiorna.timer >/dev/null 2>&1
  if [[ "$(leggi_env AGGIORNAMENTI_AUTOMATICI "$ENV_FILE")" == "no" ]]; then
    info "Aggiornamenti automatici spenti (si accendono dalle impostazioni)"
  else
    ok "Aggiornamenti automatici attivi (ogni notte verso le 4:30)"
  fi
}

# Le impostazioni salvate dal browser si applicano da sole: systemd guarda il file .da-applicare
installa_controllo_impostazioni() {
  if [[ ! -d /run/systemd/system ]]; then
    avviso "Senza systemd le modifiche fatte nel browser si applicano con: mediabox applica-impostazioni"
    return 0
  fi
  sudo tee /etc/systemd/system/mediabox-impostazioni.path >/dev/null <<UNIT
[Unit]
Description=mediabox: guarda se nel browser sono cambiate le impostazioni

[Path]
PathExists=$MEDIABOX_DIR/.da-applicare

[Install]
WantedBy=multi-user.target
UNIT
  sudo tee /etc/systemd/system/mediabox-impostazioni.service >/dev/null <<UNIT
[Unit]
Description=mediabox: applica le impostazioni cambiate nel browser
After=docker.service

[Service]
Type=oneshot
User=$(id -un)
SupplementaryGroups=docker
# la nuova cartella dei media la crea l'amministratore, il resto gira con il tuo utente
ExecStartPre=+$MEDIABOX_DIR/mediabox prepara-cartella
ExecStart=$MEDIABOX_DIR/mediabox applica-impostazioni
UNIT
  sudo systemctl daemon-reload
  sudo systemctl enable --now mediabox-impostazioni.path >/dev/null 2>&1
  ok "Impostazioni dal browser pronte: $(indirizzo_principale)/impostazioni/"
}
