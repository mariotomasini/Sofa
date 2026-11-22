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

configuratore_costruisci() {
  local immagine_base
  immagine_base="$(leggi_env IMG_PYTHON "$MEDIABOX_DIR/.env")"
  immagine_base="${immagine_base:-docker.io/library/python:3.13-slim}"
  info "Preparo il configuratore…"
  "${DOCKER[@]}" build -q --build-arg "BASE_IMAGE=$immagine_base" \
    -t mediabox/configuratore:locale "$MEDIABOX_DIR/configuratore" >/dev/null \
    || errore "Non riesco a preparare il configuratore." "Controlla la connessione a Internet e riprova."
}

# Stampa l'IP del primo HDHomeRun trovato in rete (o niente)
cerca_hdhomerun() {
  "${DOCKER[@]}" run --rm --network host mediabox/configuratore:locale hdhomerun 2>/dev/null || true
}

# Menu "Cosa guardi?" (0 = scelte salvate, 3 = nessuna modifica)
menu_impostazioni() {
  local opzioni=(-i)
  [[ -t 0 && -t 1 ]] && opzioni=(-it)
  local esito=0
  "${DOCKER[@]}" run --rm "${opzioni[@]}" --user "$(id -u):$(id -g)" -v "$MEDIABOX_DIR:/mediabox" \
    mediabox/configuratore:locale impostazioni "$@" || esito=$?
  return "$esito"
}

configuratore_genera() {
  configuratore_costruisci
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

# Scarica le immagini una alla volta: la pagina nel browser mostra "3 di 12"
scarica_immagini() {
  local immagini=() i=0 immagine
  mapfile -t immagini < <(compose config --images 2>/dev/null | grep -v '^mediabox/' | sort -u)
  for immagine in "${immagini[@]}"; do
    i=$((i + 1))
    info "Scarico ${immagine##*/} ($i di ${#immagini[@]})"
    "${DOCKER[@]}" pull -q "$immagine" >/dev/null 2>&1 \
      || avviso "Non riesco a scaricare ${immagine##*/}: riprovo all'avvio."
  done
}

# Tutto il ciclo: genera → prepara → avvia → configura via API
applica() {
  # si rifà tutto: un piano rimasto da un cambio di impostazioni non serve più
  rm -f "$MEDIABOX_DIR/generato/impostazioni.piano"
  configuratore_genera
  titolo "Scarico i programmi (la prima volta può richiedere diversi minuti)"
  scarica_immagini
  info "Preparo Cerca"
  compose build -q cerca || errore "Non riesco a preparare Cerca." "Controlla la connessione a Internet e riprova."
  titolo "Avvio i servizi"
  compose run --rm --remove-orphans -T configuratore prepara
  # la pagina della configurazione passa a Caddy (/impostazioni): libera la porta
  ferma_procedura_web
  compose up -d --remove-orphans || errore "Alcuni servizi non sono partiti." \
    "Guarda i messaggi qui sopra; spesso basta riprovare con \"mediabox configura\"."
  riavvia_segnati
  ok "Servizi avviati"
  # configura restituisce errore se qualcosa non è andato: il riepilogo lo spiega
  compose run --rm -T configuratore configura || return 1
}

# Dopo un cambio di impostazioni dal browser: solo quello che serve (generato/impostazioni.piano,
# scritto dalla pagina). "compose up" ricrea solo i servizi con impostazioni diverse; i programmi
# si riconfigurano solo se usano le voci cambiate. Senza piano si rifà tutto, come prima.
voce_piano() {
  sed -n "s/^$1=//p" "$MEDIABOX_DIR/generato/impostazioni.piano" 2>/dev/null | head -n1
}

applica_necessario() {
  local piano="$MEDIABOX_DIR/generato/impostazioni.piano" servizi prepara
  if [[ ! -f "$piano" ]]; then
    applica
    return
  fi
  servizi="$(voce_piano configura)"
  prepara="$(voce_piano prepara)"
  configuratore_genera
  titolo "Applico le modifiche"
  scarica_mancanti
  if [[ "$prepara" == "si" ]]; then
    compose run --rm --remove-orphans -T configuratore prepara
  fi
  compose up -d --remove-orphans || errore "Alcuni servizi non sono partiti." \
    "Guarda i messaggi qui sopra; spesso basta riprovare con \"mediabox configura\"."
  riavvia_segnati
  rm -f "$piano"
  case "$servizi" in
    "") ok "Fatto: nessun programma da riconfigurare" ;;
    tutto) compose run --rm -T configuratore configura || return 1 ;;
    *) compose run --rm -T configuratore configura --solo "$servizi" || return 1 ;;
  esac
  ok "Modifiche applicate"
}

# --- Configurazione dal browser -----------------------------------------------------------
NOME_PROCEDURA="mediabox-procedura"

indirizzo_principale() {
  local porta ip
  porta="$(leggi_env HTTP_PORT "$ENV_FILE")"
  ip="$(leggi_env HOST_IP "$ENV_FILE")"
  [[ "${porta:-80}" == "80" ]] && porta="" || porta=":$porta"
  printf 'http://%s%s' "${ip:-mediabox.local}" "$porta"
}

stato_installazione() {
  printf '%s\n' "$1" > "$MEDIABOX_DIR/installazione.stato"
}

# Esegue un comando scrivendo tutto anche nel registro che la pagina legge
con_registro() {
  local registro="$MEDIABOX_DIR/installazione.log" esito=0
  : > "$registro"
  stato_installazione in_corso
  ( "$@" ) 2>&1 | tee -a "$registro" || esito="${PIPESTATUS[0]}"
  if [[ "$esito" -eq 0 ]]; then stato_installazione fatto; else stato_installazione errore; fi
  return "$esito"
}

# I dischi da proporre per i media: quello di sistema e quelli molto capienti
scrivi_dischi() {
  local file="$MEDIABOX_DIR/dischi.json" libero_root totale_root
  read -r totale_root libero_root < <(df -P -B1G / | awk 'NR==2 {print $2, $4}')
  {
    printf '[{"percorso": "/srv/mediabox/dati", "nome": "Disco del sistema", "libero_gb": %s, "totale_gb": %s}' \
      "$libero_root" "$totale_root"
    df -P -B1G -x tmpfs -x devtmpfs -x overlay -x squashfs -x efivarfs 2>/dev/null \
      | awk -v root="$libero_root" 'NR>1 && $6 != "/" && $6 !~ "^/(boot|snap|run|dev|sys|proc|var/lib/docker)" && $4 >= 20 \
          {gsub(/"/, "", $6); printf ", {\"percorso\": \"%s/mediabox\", \"nome\": \"Disco %s\", \"libero_gb\": %s, \"totale_gb\": %s, \"consigliato\": %s}", \
           $6, $6, $4, $2, ($4 > 2 * root ? "true" : "false")}'
    printf ']\n'
  } > "$file"
}

avvia_procedura_web() {
  local porta
  porta="$(leggi_env HTTP_PORT "$ENV_FILE")"
  ferma_procedura_web
  # se c'è un mediabox vecchio acceso, la porta è sua: lo spengo
  [[ -f "$MEDIABOX_DIR/generato/docker-compose.yml" ]] && compose stop caddy >/dev/null 2>&1 || true
  "${DOCKER[@]}" run -d --name "$NOME_PROCEDURA" -p "${porta:-80}:8080" \
    --user "$(id -u):$(id -g)" -v "$MEDIABOX_DIR:/mediabox" \
    mediabox/configuratore:locale web >/dev/null \
    || errore "Non riesco ad aprire la pagina di configurazione sulla porta ${porta:-80}." \
      "Controlla che nessun altro programma usi quella porta e rilancia l'installer."
}

ferma_procedura_web() {
  "${DOCKER[@]}" rm -f "$NOME_PROCEDURA" >/dev/null 2>&1 || true
}

# Aspetta che la configurazione nel browser sia finita
attendi_procedura_web() {
  local flag="$MEDIABOX_DIR/.configurazione-pronta"
  info "Aspetto che tu finisca la configurazione nel browser… (per interrompere: Ctrl+C)"
  until [[ -f "$flag" ]]; do
    if ! "${DOCKER[@]}" ps -q --filter "name=^${NOME_PROCEDURA}\$" | grep -q .; then
      avviso "La pagina di configurazione si è chiusa: la riapro."
      avvia_procedura_web
    fi
    sleep 2
  done
  rm -f "$flag"
  ok "Configurazione ricevuta"
}

# Crea la cartella dei media; sudo solo se serve davvero
assicura_cartella() {
  local cartella="$1"
  if mkdir -p "$cartella" 2>/dev/null && [[ -w "$cartella" ]]; then
    return 0
  fi
  sudo mkdir -p "$cartella"
  sudo chown "$(id -u):$(id -g)" "$cartella"
}

# Sposta film, musica e download quando cambia MEDIA_DIR (servizi spenti)
sposta_media() {
  local vecchia="$1" nuova="$2"
  [[ -n "$vecchia" && -n "$nuova" && "$vecchia" != "$nuova" ]] || return 0
  titolo "Sposto film, musica e download"
  info "Da $vecchia a $nuova (con tanti film può richiedere molto tempo: non spegnere il PC)"
  compose stop >/dev/null 2>&1 || true
  assicura_cartella "$nuova"
  if [[ -d "$vecchia" ]]; then
    if [[ "$(stat -c %d "$vecchia")" == "$(stat -c %d "$nuova")" ]]; then
      # stesso disco: spostamento istantaneo
      find "$vecchia" -mindepth 1 -maxdepth 1 -exec mv -t "$nuova" {} +
    else
      cp -a "$vecchia/." "$nuova/" || errore "La copia in $nuova non è riuscita (spazio finito?)." \
        "I file originali sono ancora in $vecchia. Libera spazio e riprova con: mediabox impostazioni"
      find "$vecchia" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    fi
  fi
  ok "File spostati in $nuova"
}
