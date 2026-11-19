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
      echo "Le scelte si cambiano anche dopo, con: mediabox impostazioni"
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
    avviso "Questo sistema (${PRETTY_NAME:-sconosciuto}) non è tra quelli provati (Debian 12, Ubuntu 24.04)."
    chiedi_si_no "Vuoi provare lo stesso?" n || exit 1 ;;
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

# --- 4. File .env e domande ---------------------------------------------------------
titolo "Configurazione"
NUOVA_INSTALLAZIONE=""
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$MEDIABOX_DIR/.env.example" "$ENV_FILE"
  NUOVA_INSTALLAZIONE="si"
fi
chmod 600 "$ENV_FILE"

valida_tmdb() {
  local chiave="$1" codice
  if ((${#chiave} > 40)); then
    codice="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $chiave" https://api.themoviedb.org/3/configuration || true)"
  else
    codice="$(curl -s -o /dev/null -w '%{http_code}' "https://api.themoviedb.org/3/configuration?api_key=$chiave" || true)"
  fi
  [[ "$codice" == "200" ]] && return 0
  [[ "$codice" == "000" ]] && return 2  # niente Internet: non possiamo verificarla
  return 1
}

if [[ -z "$(leggi_env TMDB_API_KEY "$ENV_FILE")" ]]; then
  echo
  echo "  Serve una chiave gratuita di TMDB (il catalogo dei film)."
  echo "  La trovi su themoviedb.org → il tuo account → Impostazioni → API."
  while true; do
    read -r -p "  ? Incolla la chiave TMDB: " chiave_tmdb
    chiave_tmdb="$(echo "$chiave_tmdb" | tr -d '[:space:]')"
    [[ -n "$chiave_tmdb" ]] || { echo "    La chiave non può essere vuota."; continue; }
    if valida_tmdb "$chiave_tmdb"; then
      ok "Chiave TMDB valida"
      break
    elif [[ $? -eq 2 ]]; then
      avviso "Non riesco a verificare la chiave (manca Internet?). La salvo comunque."
      break
    else
      echo "    TMDB non accetta questa chiave. Controlla di averla copiata tutta e riprova."
    fi
  done
  scrivi_env TMDB_API_KEY "$chiave_tmdb" "$ENV_FILE"
fi

if [[ -z "$(leggi_env ADMIN_PASSWORD "$ENV_FILE")" ]]; then
  echo
  echo "  Scegli una password di amministrazione: vale per Jellyfin e per i pannelli."
  echo "  Almeno 8 caratteri, senza virgolette doppie (\") e senza barra rovesciata (\\)."
  while true; do
    read -r -s -p "  ? Password: " pw1; echo
    read -r -s -p "  ? Scrivila di nuovo: " pw2; echo
    if [[ "$pw1" != "$pw2" ]]; then echo "    Le due password non coincidono, riprova."; continue; fi
    if ((${#pw1} < 8)); then echo "    Troppo corta: almeno 8 caratteri."; continue; fi
    if [[ "$pw1" == *'"'* || "$pw1" == *\\* ]]; then echo "    Togli le virgolette doppie e la barra rovesciata."; continue; fi
    if (($(printf '%s' "$pw1" | wc -c) > 72)); then echo "    Troppo lunga: al massimo 72 caratteri."; continue; fi
    break
  done
  scrivi_env ADMIN_PASSWORD "$pw1" "$ENV_FILE"
  unset pw1 pw2
fi

if [[ -n "$NUOVA_INSTALLAZIONE" ]]; then
  echo
  echo "  (Facoltativo) Notifiche sul telefono con l'app gratuita ntfy: scegli un nome"
  echo "  difficile da indovinare, es. mediabox-rossi-4821. Lascia vuoto per saltare."
  read -r -p "  ? Argomento ntfy: " ntfy
  scrivi_env NTFY_TOPIC "$(echo "$ntfy" | tr -cd '[:alnum:]_-')" "$ENV_FILE"
fi

# Chiavi interne dei servizi
for chiave in RADARR_API_KEY PROWLARR_API_KEY SONARR_API_KEY LIDARR_API_KEY; do
  [[ -n "$(leggi_env "$chiave" "$ENV_FILE")" ]] || scrivi_env "$chiave" "$(chiave_casuale)" "$ENV_FILE"
done

# Cosa guardi? (solo la prima volta; dopo si cambia con "mediabox impostazioni")
configuratore_costruisci
if [[ -n "$NUOVA_INSTALLAZIONE" && -t 0 ]]; then
  menu_impostazioni --prima-volta || true
fi

# Cartella dei film: se c'è un disco molto più capiente lo proponiamo
if [[ -n "$NUOVA_INSTALLAZIONE" ]]; then
  cartella_dati="/srv/mediabox/dati"
  libero_root="$(df -P -B1 / | awk 'NR==2 {print $4}')"
  migliore="$(df -P -B1 -x tmpfs -x devtmpfs -x overlay -x squashfs -x efivarfs 2>/dev/null \
    | awk -v root="$libero_root" 'NR>1 && $6 != "/" && $6 !~ "^/(boot|snap|run|dev|sys|proc)" && $4 > 2*root && $4 > 100*1024^3 {print $4, $6}' \
    | sort -nr | head -n1)"
  if [[ -n "$migliore" ]]; then
    spazio="$(( ${migliore%% *} / 1024 / 1024 / 1024 ))"
    disco="${migliore#* }"
    if chiedi_si_no "Ho trovato un disco più capiente in $disco ($spazio GB liberi). Metto lì i film?" s; then
      cartella_dati="$disco/mediabox"
    fi
  fi
  scrivi_env MEDIA_DIR "$cartella_dati" "$ENV_FILE"
fi
MEDIA_DIR="$(leggi_env MEDIA_DIR "$ENV_FILE")"
sudo mkdir -p "$MEDIA_DIR"
sudo chown "$(id -u):$(id -g)" "$MEDIA_DIR"
ok "Film, serie e download in: $MEDIA_DIR"

# --- 5. Hardware --------------------------------------------------------------------
titolo "Rilevo l'hardware"
scrivi_env PIATTAFORMA linux "$ENV_FILE"
scrivi_env PUID "$(id -u)" "$ENV_FILE"
scrivi_env PGID "$(id -g)" "$ENV_FILE"
scrivi_env HOST_IP "$(ip_locale)" "$ENV_FILE"
[[ -n "$CON_SONARR" ]] && scrivi_env WITH_SONARR si "$ENV_FILE"
[[ -n "$CON_LIDARR" ]] && scrivi_env WITH_LIDARR si "$ENV_FILE"
[[ -n "$CON_ANIME" ]] && scrivi_env WITH_ANIME si "$ENV_FILE"

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

hdhomerun="$(cerca_hdhomerun)"
if [[ -n "$hdhomerun" ]]; then
  scrivi_env HDHOMERUN_IP "$hdhomerun" "$ENV_FILE"
  ok "HDHomeRun trovato in rete: $hdhomerun"
elif [[ -n "$(leggi_env HDHOMERUN_IP "$ENV_FILE")" ]]; then
  info "HDHomeRun non risponde ora, tengo quello già configurato ($(leggi_env HDHOMERUN_IP "$ENV_FILE"))"
else
  info "Nessun HDHomeRun in rete"
fi

# Porta 80 occupata da qualcos'altro?
porta="$(leggi_env HTTP_PORT "$ENV_FILE")"
if sudo ss -ltnp "sport = :${porta:-80}" 2>/dev/null | grep -v -q -e docker-proxy -e '^State' ; then
  avviso "La porta ${porta:-80} è già usata da un altro programma."
  if chiedi_si_no "Uso la porta 8080? (l'indirizzo diventerà http://mediabox.local:8080)" s; then
    scrivi_env HTTP_PORT 8080 "$ENV_FILE"
  fi
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

# --- 7. Avvio e configurazione automatica ------------------------------------------
if applica; then
  titolo "Fatto!"
  echo "  mediabox è pronto. Questo riepilogo lo rivedi quando vuoi con: mediabox indirizzi"
else
  titolo "Installazione completata con qualche problema"
  echo "  Leggi la sezione \"Cosa non è andato\" qui sopra. Spesso basta aspettare un minuto e"
  echo "  scrivere: mediabox configura"
  exit 1
fi
