#!/usr/bin/env bash
# Comandi Docker usati da install.sh e mediabox (Linux).
# Richiede: MEDIABOX_DIR impostata e lib/comune.sh già incluso.

# Docker si usa con sudo finché l'utente non è nel gruppo "docker"
# (dopo l'installazione serve uscire e rientrare perché valga).
DOCKER=(docker)
prepara_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    errore "Docker non è installato." "Esegui di nuovo install.sh: lo installa da solo."
  fi
  if docker info >/dev/null 2>&1; then
    DOCKER=(docker)
  elif sudo -n true 2>/dev/null || [[ -t 0 ]]; then
    DOCKER=(sudo docker)
    if ! "${DOCKER[@]}" info >/dev/null 2>&1; then
      sudo systemctl start docker 2>/dev/null || true
      "${DOCKER[@]}" info >/dev/null 2>&1 || errore "Docker non risponde." \
        "Prova a riavviare il PC. Se il problema resta: sudo systemctl status docker"
    fi
  else
    errore "Non ho i permessi per usare Docker." "Esci e rientra nel PC (o riavvialo), poi riprova."
  fi
}

compose() {
  "${DOCKER[@]}" compose --project-directory "$MEDIABOX_DIR/generato" \
    -f "$MEDIABOX_DIR/generato/docker-compose.yml" "$@"
}

configuratore_genera() {
  local immagine_base
  immagine_base="$(leggi_env IMG_PYTHON "$MEDIABOX_DIR/.env")"
  immagine_base="${immagine_base:-docker.io/library/python:3.13-slim}"
  info "Preparo il configuratore…"
  "${DOCKER[@]}" build -q --build-arg "BASE_IMAGE=$immagine_base" \
    -t mediabox/configuratore:locale "$MEDIABOX_DIR/configuratore" >/dev/null \
    || errore "Non riesco a preparare il configuratore." "Controlla la connessione a Internet e riprova."
  "${DOCKER[@]}" run --rm --user "$(id -u):$(id -g)" -v "$MEDIABOX_DIR:/mediabox" \
    mediabox/configuratore:locale genera || errore "Non riesco a generare i file di configurazione." \
    "Leggi il messaggio qui sopra, correggi il file .env e riprova."
}

# Riavvia i servizi che il configuratore ha segnato (es. Caddyfile cambiato)
riavvia_segnati() {
  local file="$MEDIABOX_DIR/generato/.da-riavviare" servizio
  [[ -f "$file" ]] || return 0
  while read -r servizio; do
    [[ -n "$servizio" ]] || continue
    info "Riavvio $servizio per applicare le modifiche…"
    compose restart "$servizio" >/dev/null 2>&1 || true
  done < "$file"
  rm -f "$file"
}

# Tutto il ciclo: genera → prepara → avvia → configura via API
applica() {
  configuratore_genera
  titolo "Scarico e avvio i servizi (la prima volta può richiedere diversi minuti)"
  compose build -q cerca || errore "Non riesco a preparare Cerca." "Controlla la connessione a Internet e riprova."
  compose pull -q --ignore-buildable 2>/dev/null || compose pull --ignore-pull-failures -q || true
  compose run --rm -T configuratore prepara
  compose up -d --remove-orphans || errore "Alcuni servizi non sono partiti." \
    "Guarda i messaggi qui sopra; spesso basta riprovare con \"mediabox configura\"."
  riavvia_segnati
  ok "Servizi avviati"
  # configura restituisce errore se qualcosa non è andato: il riepilogo lo spiega
  compose run --rm -T configuratore configura || return 1
}
