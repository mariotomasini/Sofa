#!/usr/bin/env bash
# Aggiornamento di mediabox da GitHub (Linux).
# Richiede: MEDIABOX_DIR, ENV_FILE e lib/comune.sh, lib/docker.sh già inclusi.

versione_installata() {
  tr -d '[:space:]' < "$MEDIABOX_DIR/VERSION" 2>/dev/null || echo "0.0.0"
}

notifica_ntfy() {
  local topic server
  topic="$(leggi_env NTFY_TOPIC "$ENV_FILE")"
  [[ -n "$topic" ]] || return 0
  server="$(leggi_env NTFY_SERVER "$ENV_FILE")"
  curl -fsS -m 20 -H "Title: mediabox" -d "$1" "${server:-https://ntfy.sh}/$topic" >/dev/null 2>&1 || true
}

# Lo stato che la pagina delle impostazioni mostra: fase e messaggio
# fasi: controllo | scarico | installo | fatto | aggiornato | errore | in_attesa
stato_aggiornamento() {
  printf '{"fase": "%s", "messaggio": "%s", "quando": "%s"}\n' "$1" "${2:-}" "$(date +%Y-%m-%dT%H:%M:%S)" \
    > "$MEDIABOX_DIR/aggiornamento.stato"
}

# Il controllo e il download li fa il configuratore (la stessa logica su Linux e Windows).
# Codici: 0 = c'è una versione nuova / installata, 3 = niente di nuovo, altro = errore
configuratore_aggiornamento() {
  # MEDIABOX_GITHUB_API serve solo per le prove (un GitHub finto)
  "${DOCKER[@]}" run --rm --user "$(id -u):$(id -g)" -v "$MEDIABOX_DIR:/mediabox" -e MEDIABOX_GITHUB_API \
    mediabox/configuratore:locale aggiornamento "$1"
}

# Vero se nel piano dell'aggiornamento c'è da rifare questa parte (cerca, configuratore, configura)
nel_piano() {
  grep -q "\"$1\": true" "$MEDIABOX_DIR/generato/aggiornamento.json" 2>/dev/null
}

# Scarica solo i programmi che mancano (quelli con una versione nuova)
scarica_mancanti() {
  local immagine
  while read -r immagine; do
    [[ -n "$immagine" ]] || continue
    "${DOCKER[@]}" image inspect "$immagine" >/dev/null 2>&1 && continue
    info "Scarico la versione nuova di ${immagine##*/}"
    "${DOCKER[@]}" pull -q "$immagine" >/dev/null 2>&1 \
      || avviso "Non riesco a scaricare ${immagine##*/}: riprovo all'avvio."
  done < <(compose config --images 2>/dev/null | grep -v '^mediabox/' | sort -u)
}

# Dopo un aggiornamento: via le versioni vecchie dei programmi di mediabox e gli avanzi delle
# costruzioni di Cerca e del configuratore. Su un PC con un disco piccolo sono diversi GB.
# Si toglie solo un'immagine dello stesso programma con una versione diversa da quella in uso.
pulisci_versioni_vecchie() {
  local in_uso nomi immagine
  in_uso="$(compose config --images 2>/dev/null | sort -u)"
  [[ -n "$in_uso" ]] || return 0
  nomi="$(sed 's/:[^:/]*$//' <<<"$in_uso" | sort -u)"
  while read -r immagine; do
    [[ -n "$immagine" && "$immagine" != *"<none>"* ]] || continue
    grep -qxF "$immagine" <<<"$in_uso" && continue
    grep -qxF "${immagine%:*}" <<<"$nomi" || continue
    # se un contenitore la usa ancora, Docker si rifiuta: va bene così
    "${DOCKER[@]}" rmi "$immagine" >/dev/null 2>&1 && info "Tolta la versione vecchia: ${immagine##*/}"
  done < <("${DOCKER[@]}" images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null)
  "${DOCKER[@]}" image prune -f >/dev/null 2>&1 || true
  "${DOCKER[@]}" builder prune -f --filter until=168h >/dev/null 2>&1 || true
}

# Prima parte: controlla, fa il backup e installa i file nuovi. Poi passa al nuovo script.
aggiorna_mediabox() {
  local esito=0
  titolo "Controllo gli aggiornamenti"
  stato_aggiornamento controllo "Controllo se c'è una versione nuova…"
  configuratore_costruisci
  configuratore_aggiornamento controlla >/dev/null || esito=$?
  if [[ "$esito" -eq 3 ]]; then
    stato_aggiornamento aggiornato "mediabox è già all'ultima versione."
    ok "mediabox è già all'ultima versione ($(versione_installata))"
    return 0
  elif [[ "$esito" -ne 0 ]]; then
    stato_aggiornamento errore "Non riesco a controllare gli aggiornamenti: manca Internet?"
    errore "Non riesco a controllare gli aggiornamenti." "Controlla la connessione a Internet e riprova."
  fi
  backup
  esito=0
  configuratore_aggiornamento installa || esito=$?
  [[ "$esito" -eq 3 ]] && return 0
  if [[ "$esito" -ne 0 ]]; then
    errore "L'aggiornamento non è riuscito." "Riprova più tardi: mediabox continua a funzionare con la versione di prima."
  fi
  chmod +x "$MEDIABOX_DIR/mediabox" "$MEDIABOX_DIR/install.sh" "$MEDIABOX_DIR"/lib/*.sh 2>/dev/null || true
  # il nuovo codice va eseguito dal nuovo script
  exec "$MEDIABOX_DIR/mediabox" aggiorna-servizi
}

# Seconda parte (con il nuovo script): rifà solo quello che è cambiato
aggiorna_servizi() {
  stato_aggiornamento installo "Aggiorno i programmi cambiati…"
  titolo "Aggiorno solo quello che è cambiato"
  # indirizzi e Tailscale (se c'è già sul PC mediabox non avvia il suo)
  aggiorna_ip
  configuratore_genera
  scarica_mancanti
  if nel_piano cerca; then
    info "Preparo la nuova versione di Cerca"
    compose build -q cerca || {
      stato_aggiornamento errore "Non riesco a preparare la nuova versione di Cerca."
      errore "Non riesco a preparare Cerca." "Controlla la connessione a Internet e riprova: mediabox update"
    }
  fi
  compose up -d --remove-orphans || {
    stato_aggiornamento errore "Alcuni servizi non sono ripartiti."
    errore "Alcuni servizi non sono ripartiti." "Riprova con: mediabox configura"
  }
  riavvia_segnati
  if nel_piano configura; then
    titolo "Riconfiguro i servizi"
    stato_aggiornamento installo "Riconfiguro i servizi…"
    compose run --rm -T configuratore configura || {
      stato_aggiornamento errore "La configurazione dei servizi non è finita: guarda il registro."
      return 1
    }
  fi
  pulisci_versioni_vecchie
  rm -f "$MEDIABOX_DIR/generato/aggiornamento.json"
  stato_aggiornamento fatto "mediabox aggiornato alla versione $(versione_installata)."
  ok "mediabox aggiornato alla versione $(versione_installata)"
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
