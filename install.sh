#!/usr/bin/env bash
# =============================================================================
#  mediabox — installer per Debian 12 / Ubuntu Server 24.04
#
#  Uso:   ./install.sh [--with-sonarr]
#
#  Si può rilanciare quando si vuole: non cancella nulla, completa quello che
#  manca e aggiorna la configurazione.
# =============================================================================
set -euo pipefail

SORGENTE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEDIABOX_DIR="${MEDIABOX_DIR:-/opt/mediabox}"
# shellcheck source=lib/comune.sh
source "$SORGENTE/lib/comune.sh"

CON_SONARR=""
CON_LIDARR=""
CON_ANIME=""
for argomento in "$@"; do
  case "$argomento" in
    --with-sonarr) CON_SONARR="si" ;;
    --with-lidarr) CON_LIDARR="si" ;;
    --with-anime) CON_ANIME="si" ;;
    -h|--help|--aiuto)
      echo "Uso: ./install.sh [--with-sonarr] [--with-anime] [--with-lidarr]"
      echo "  --with-sonarr   installa anche Sonarr per organizzare le serie TV"
      echo "  --with-anime    aggiunge la libreria Anime (usa Sonarr)"
      echo "  --with-lidarr   installa Lidarr per scaricare e ordinare la musica"
      echo "Tutte le scelte si fanno poi nel browser, e si cambiano da http://mediabox.local/impostazioni"
      exit 0 ;;
    *) errore "Opzione sconosciuta: $argomento" "Usa ./install.sh --aiuto per vedere le opzioni." ;;
  esac
done

# --- 1. Prerequisiti -----------------------------------------------------------
titolo "Controllo il sistema"

if [[ "$EUID" -eq 0 ]]; then
  errore "Non lanciare l'installer come root (o con sudo)." \
    "Entra con il tuo utente normale e scrivi: ./install.sh  — la password di sudo verrà chiesta quando serve."
fi
command -v sudo >/dev/null || errore "Manca il comando sudo." "Installa sudo e aggiungi il tuo utente al gruppo sudo."
info "Potrebbe esserti chiesta la password del tuo utente (per sudo)."
sudo -v || errore "Il tuo utente non può usare sudo." "Chiedi a chi ha installato il PC di aggiungerti al gruppo sudo."

# shellcheck disable=SC1091
source /etc/os-release
case "${ID:-}-${VERSION_ID:-}" in
  debian-12|ubuntu-24.04) ok "Sistema supportato: ${PRETTY_NAME}" ;;
  debian-*|ubuntu-*)
    avviso "Questo sistema (${PRETTY_NAME:-sconosciuto}) non è tra quelli provati (Debian 12, Ubuntu 24.04): provo lo stesso." ;;
  *) errore "Sistema non supportato: ${PRETTY_NAME:-sconosciuto}." "mediabox funziona su Debian 12 o Ubuntu Server 24.04." ;;
esac
[[ "$(uname -m)" == "x86_64" ]] || avviso "Processore $(uname -m): mediabox è provato solo su x86_64."

# --- 2. Copia dei file in /opt/mediabox ------------------------------------------------
if [[ "$SORGENTE" != "$MEDIABOX_DIR" ]]; then
  info "Copio mediabox in $MEDIABOX_DIR…"
  sudo mkdir -p "$MEDIABOX_DIR"
  sudo chown "$(id -u):$(id -g)" "$MEDIABOX_DIR"
  tar -C "$SORGENTE" --exclude=.git --exclude=.env --exclude=generato --exclude=backup \
    --exclude=__pycache__ --exclude=.pytest_cache --exclude=.ruff_cache -cf - . | tar -C "$MEDIABOX_DIR" -xf -
  ok "File copiati in $MEDIABOX_DIR"
fi
ENV_FILE="$MEDIABOX_DIR/.env"
# shellcheck source=lib/docker.sh
source "$MEDIABOX_DIR/lib/docker.sh"
# shellcheck source=lib/aggiorna.sh
source "$MEDIABOX_DIR/lib/aggiorna.sh"

# --- 3. Pacchetti di sistema e Docker ------------------------------------------------
titolo "Installo quello che serve"
pacchetti=(ca-certificates curl avahi-daemon avahi-utils iproute2)
mancanti=()
for p in "${pacchetti[@]}"; do
  dpkg -s "$p" >/dev/null 2>&1 || mancanti+=("$p")
done
if ((${#mancanti[@]})); then
  info "Installo: ${mancanti[*]}"
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${mancanti[@]}" >/dev/null
fi
ok "Pacchetti di base presenti"

installa_docker() {
  info "Installo Docker (qualche minuto)…"
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
  sudo systemctl enable --now docker >/dev/null
}

if ! command -v docker >/dev/null 2>&1; then
  installa_docker || errore "Non sono riuscito a installare Docker." "Controlla la connessione a Internet e rilancia l'installer."
elif ! docker compose version >/dev/null 2>&1 && ! sudo docker compose version >/dev/null 2>&1; then
  info "Docker c'è ma manca \"docker compose\": lo aggiungo."
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose-plugin >/dev/null 2>&1 || installa_docker \
    || errore "Non riesco a installare docker compose." "Disinstalla la versione di Docker presente e rilancia l'installer."
fi
if ! id -nG "$USER" | grep -qw docker; then
  sudo usermod -aG docker "$USER"
  info "Ti ho aggiunto al gruppo docker (vale dal prossimo accesso; per ora uso sudo)."
fi
prepara_docker
ok "Docker pronto ($("${DOCKER[@]}" --version | cut -d, -f1))"

# --- 4. File .env ----------------------------------------------------------------------
titolo "Preparo la configurazione"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$MEDIABOX_DIR/.env.example" "$ENV_FILE"
fi
# la pagina delle impostazioni gira con il tuo utente: il file deve essere tuo
sudo chown "$(id -u):$(id -g)" "$ENV_FILE"
chmod 600 "$ENV_FILE"

# Chiavi interne dei servizi
for chiave in RADARR_API_KEY PROWLARR_API_KEY SONARR_API_KEY LIDARR_API_KEY IMPOSTAZIONI_SEGRETO; do
  [[ -n "$(leggi_env "$chiave" "$ENV_FILE")" ]] || scrivi_env "$chiave" "$(chiave_casuale)" "$ENV_FILE"
done
scrivi_env PIATTAFORMA linux "$ENV_FILE"
scrivi_env PUID "$(id -u)" "$ENV_FILE"
scrivi_env PGID "$(id -g)" "$ENV_FILE"
scrivi_env HOST_IP "$(ip_locale)" "$ENV_FILE"
[[ -n "$CON_SONARR" ]] && scrivi_env WITH_SONARR si "$ENV_FILE"
[[ -n "$CON_LIDARR" ]] && scrivi_env WITH_LIDARR si "$ENV_FILE"
[[ -n "$CON_ANIME" ]] && scrivi_env WITH_ANIME si "$ENV_FILE"
ok "File di configurazione pronto"

# --- 5. Hardware --------------------------------------------------------------------
titolo "Rilevo l'hardware"
if [[ -e /dev/dri/renderD128 ]]; then
  scrivi_env VAAPI si "$ENV_FILE"
  scrivi_env RENDER_GID "$(stat -c %g /dev/dri/renderD128)" "$ENV_FILE"
  ok "Scheda grafica trovata: transcodifica hardware attiva"
else
  scrivi_env VAAPI no "$ENV_FILE"
  info "Nessuna scheda grafica (/dev/dri): la transcodifica userà il processore"
fi

if [[ -d /dev/dvb ]] && compgen -G "/dev/dvb/adapter*" >/dev/null; then
  scrivi_env DVB si "$ENV_FILE"
  ok "Tuner TV USB trovato: attivo TVHeadend"
else
  scrivi_env DVB no "$ENV_FILE"
  info "Nessun tuner USB (/dev/dvb)"
fi

configuratore_costruisci
hdhomerun="$(cerca_hdhomerun)"
if [[ -n "$hdhomerun" ]]; then
  scrivi_env HDHOMERUN_IP "$hdhomerun" "$ENV_FILE"
  ok "HDHomeRun trovato in rete: $hdhomerun"
elif [[ -n "$(leggi_env HDHOMERUN_IP "$ENV_FILE")" ]]; then
  info "HDHomeRun non risponde ora, tengo quello già configurato ($(leggi_env HDHOMERUN_IP "$ENV_FILE"))"
else
  info "Nessun HDHomeRun in rete"
fi

# Porta 80 occupata da qualcos'altro? Allora 8080, senza chiedere
porta="$(leggi_env HTTP_PORT "$ENV_FILE")"
if sudo ss -ltnp "sport = :${porta:-80}" 2>/dev/null | grep -v -q -e docker-proxy -e '^State' ; then
  avviso "La porta ${porta:-80} è già usata da un altro programma: uso la 8080."
  scrivi_env HTTP_PORT 8080 "$ENV_FILE"
fi

# --- 6. mediabox.local e comando mediabox ------------------------------------------
titolo "Indirizzo mediabox.local"
if [[ "$(hostname -s)" == "mediabox" ]]; then
  ok "Il PC si chiama già \"mediabox\": mediabox.local funziona da solo"
else
  sudo install -m 0644 "$MEDIABOX_DIR/lib/mediabox-avahi.service" /etc/systemd/system/mediabox-avahi.service
  sudo sed -i "s#/opt/mediabox#$MEDIABOX_DIR#g" /etc/systemd/system/mediabox-avahi.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now avahi-daemon >/dev/null 2>&1 || true
  sudo systemctl enable mediabox-avahi.service >/dev/null 2>&1
  sudo systemctl restart mediabox-avahi.service
  ok "mediabox.local annunciato nella rete di casa"
fi
sudo ln -sf "$MEDIABOX_DIR/mediabox" /usr/local/bin/mediabox
chmod +x "$MEDIABOX_DIR/mediabox" "$MEDIABOX_DIR/install.sh" "$MEDIABOX_DIR/lib/avahi-alias.sh"
ok "Comando \"mediabox\" installato"

# --- 7. Configurazione dal browser -------------------------------------------------------
# (la password la scrive solo la procedura: se manca, la procedura non è stata fatta)
if [[ -z "$(leggi_env ADMIN_PASSWORD "$ENV_FILE")" ]]; then
  scrivi_dischi
  avvia_procedura_web
  porta="$(leggi_env HTTP_PORT "$ENV_FILE")"
  [[ "${porta:-80}" == "80" ]] && porta="" || porta=":$porta"
  titolo "Continua dal browser"
  echo
  echo "  Apri questo indirizzo da un PC o dal telefono collegato alla stessa rete:"
  echo
  printf '      %shttp://mediabox.local%s%s\n' "$C_OK" "$porta" "$C_FINE"
  printf '      oppure  http://%s%s\n' "$(leggi_env HOST_IP "$ENV_FILE")" "$porta"
  echo
  echo "  Lì scegli la password, cosa guardare e dove salvare i film."
  echo "  Puoi seguire l'installazione dal browser: questa finestra puoi anche ignorarla."
  echo
  attendi_procedura_web
fi

MEDIA_DIR="$(leggi_env MEDIA_DIR "$ENV_FILE")"
assicura_cartella "$MEDIA_DIR"
ok "Film, serie e download in: $MEDIA_DIR"
installa_aggiornamenti_automatici
installa_controllo_impostazioni

# --- 8. Avvio e configurazione automatica ------------------------------------------
if con_registro applica; then
  titolo "Fatto!"
  echo "  mediabox è pronto: $(indirizzo_principale)"
  echo "  Gli indirizzi li rivedi quando vuoi con: mediabox indirizzi"
else
  titolo "Installazione completata con qualche problema"
  echo "  Leggi la sezione \"Cosa non è andato\" qui sopra (o nel browser). Spesso basta aspettare"
  echo "  un minuto e scrivere: mediabox configura"
  exit 1
fi
