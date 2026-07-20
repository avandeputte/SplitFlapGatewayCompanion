#!/usr/bin/env bash
#
# SplitFlap Gateway Companion — one-line installer.
#
#   curl -fsSL https://raw.githubusercontent.com/avandeputte/SplitFlapGatewayCompanion/main/install.sh | bash
#
# Runs on Raspberry Pi OS (arm64) and x86-64 Linux (Debian/Ubuntu/Fedora/…). It:
#   1. installs Docker (docker-ce) if it isn't already present,
#   2. optionally deploys a Mosquitto MQTT broker (only needed for Home Assistant),
#   3. asks for your gateway URL + (optional) MQTT broker for Home Assistant,
#   4. writes a docker-compose project and starts the companion,
#   5. optionally adds a Watchtower container to auto-update the images.
#
# Everything is interactive but honors env-var overrides for unattended installs,
# e.g.:  GATEWAY_URL=http://192.168.1.50 DEPLOY_MQTT=no AUTO_UPDATE=no bash install.sh
#
set -euo pipefail

# --------------------------------------------------------------------------- #
# Presentation + prompt helpers (prompts read /dev/tty so they work under
# `curl … | bash`, where stdin is the script itself).
# --------------------------------------------------------------------------- #
if [ -t 1 ]; then
  B=$'\033[1m'; DIM=$'\033[2m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; C=$'\033[36m'; N=$'\033[0m'
else
  B=''; DIM=''; G=''; Y=''; R=''; C=''; N=''
fi
say()  { printf '%s\n' "$*"; }
info() { printf '%s\n' "${C}▸${N} $*"; }
ok()   { printf '%s\n' "${G}✓${N} $*"; }
warn() { printf '%s\n' "${Y}!${N} $*"; }
die()  { printf '%s\n' "${R}✗ $*${N}" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# Interactive only if we can actually open the controlling terminal for I/O
# (under `curl … | bash` stdin is the script, so we talk to /dev/tty directly).
if { : >/dev/tty; } 2>/dev/null; then TTY=/dev/tty; else TTY=""; fi

# ask <VAR> <prompt> [default]   — respects an existing env value of the same name
ask() {
  local var=$1 prompt=$2 def=${3:-} cur ans
  cur=$(eval "printf '%s' \"\${$var:-}\"")
  if [ -n "$cur" ]; then printf -v "$var" '%s' "$cur"; return; fi
  if [ -n "$TTY" ]; then
    if [ -n "$def" ]; then printf '%s %s[%s]%s ' "$prompt" "$DIM" "$def" "$N" >"$TTY"
    else printf '%s ' "$prompt" >"$TTY"; fi
    IFS= read -r ans <"$TTY" || ans=""
  fi
  [ -z "${ans:-}" ] && ans=$def
  printf -v "$var" '%s' "$ans"
}

# asksecret <VAR> <prompt>  — no echo
asksecret() {
  local var=$1 prompt=$2 cur ans
  cur=$(eval "printf '%s' \"\${$var:-}\"")
  if [ -n "$cur" ]; then printf -v "$var" '%s' "$cur"; return; fi
  if [ -n "$TTY" ]; then
    printf '%s ' "$prompt" >"$TTY"
    IFS= read -rs ans <"$TTY" || ans=""
    printf '\n' >"$TTY"
  fi
  printf -v "$var" '%s' "${ans:-}"
}

# askyn <VAR> <prompt> <default y|n>
askyn() {
  local var=$1 prompt=$2 def=$3 cur ans
  cur=$(eval "printf '%s' \"\${$var:-}\"")
  if [ -n "$cur" ]; then
    case ${cur,,} in y|yes|true|1) printf -v "$var" 'yes';; *) printf -v "$var" 'no';; esac
    return
  fi
  local hint='[y/N]'; [ "$def" = y ] && hint='[Y/n]'
  if [ -n "$TTY" ]; then
    printf '%s %s ' "$prompt" "$hint" >"$TTY"
    IFS= read -r ans <"$TTY" || ans=""
  fi
  [ -z "${ans:-}" ] && ans=$def
  case ${ans,,} in y|yes|true|1) printf -v "$var" 'yes';; *) printf -v "$var" 'no';; esac
}

fetch() { if have curl; then curl -fsSL "$1"; else wget -qO- "$1"; fi; }

# --------------------------------------------------------------------------- #
# Privilege + platform
# --------------------------------------------------------------------------- #
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  have sudo || die "Please run as root or install sudo."
  SUDO="sudo"
fi
have curl || have wget || die "Need curl or wget installed."

OS=$(uname -s); ARCH=$(uname -m)
[ "$OS" = "Linux" ] || die "This installer targets Linux (got $OS)."
case "$ARCH" in
  x86_64|amd64|aarch64|arm64|armv7l) ;;
  *) warn "Unrecognized architecture '$ARCH' — continuing, but the image may not exist for it." ;;
esac

say ""
say "${B}SplitFlap Gateway Companion — installer${N}"
say "${DIM}Linux ${ARCH} · Docker deployment${N}"
say ""

# --------------------------------------------------------------------------- #
# 1. Docker
# --------------------------------------------------------------------------- #
if have docker; then
  ok "Docker already installed ($(docker --version 2>/dev/null | awk '{print $3}' | tr -d ,))"
else
  info "Docker not found — installing docker-ce via get.docker.com …"
  tmp=$(mktemp)
  fetch https://get.docker.com >"$tmp" || die "Could not download the Docker install script."
  $SUDO sh "$tmp"
  rm -f "$tmp"
  $SUDO systemctl enable --now docker >/dev/null 2>&1 || true
  # Let the invoking user run docker without sudo after next login.
  if [ -n "$SUDO" ]; then
    $SUDO usermod -aG docker "$(id -un)" 2>/dev/null && \
      warn "Added $(id -un) to the 'docker' group — log out/in later to use docker without sudo."
  fi
  ok "Docker installed."
fi

# Compose v2 plugin
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif $SUDO docker compose version >/dev/null 2>&1; then
  DC="$SUDO docker compose"
else
  info "Installing the Docker Compose plugin …"
  if have apt-get; then $SUDO apt-get update -qq && $SUDO apt-get install -y docker-compose-plugin >/dev/null 2>&1 || true; fi
  docker compose version >/dev/null 2>&1 && DC="docker compose" || DC="$SUDO docker compose"
  $DC version >/dev/null 2>&1 || die "Docker Compose v2 is required but could not be installed."
fi
# Use sudo for docker if the current shell isn't in the docker group yet.
if [ -n "$SUDO" ] && ! docker info >/dev/null 2>&1; then DC="$SUDO docker compose"; DOCKER="$SUDO docker"; else DOCKER="docker"; fi
ok "Using: ${DC}"

# --------------------------------------------------------------------------- #
# 2. Configuration questions
# --------------------------------------------------------------------------- #
say ""
say "${B}Gateway${N}"
while :; do
  ask GATEWAY_URL "  Gateway base URL (e.g. http://192.168.1.50):"
  case "$GATEWAY_URL" in
    http://*|https://*) break ;;
    "") [ -z "$TTY" ] && die "GATEWAY_URL is required (set it as an env var for unattended installs)."; warn "Required." ;;
    *) GATEWAY_URL="http://$GATEWAY_URL"; break ;;
  esac
done
ok "Gateway: $GATEWAY_URL"

say ""
say "${B}MQTT broker (optional — only for Home Assistant)${N}"
say "${DIM}  The companion drives the display over REST; it does NOT need MQTT to work."
say "  MQTT is used solely to expose Home Assistant controls (App / Playlist / Stop"
say "  entities). Matrix Portal Gateway firmware 3.0 no longer supplies a broker, so"
say "  either deploy one here or point the companion at your existing broker.${N}"
askyn DEPLOY_MQTT "  Deploy a Mosquitto MQTT broker container here?" n

MQTT_USER="${MQTT_USER:-}"; MQTT_PASS="${MQTT_PASS:-}"; COMPANION_HA="auto"
MQTT_BROKER=""; MQTT_PORT=""
if [ "$DEPLOY_MQTT" = yes ]; then
  ask MQTT_USER "  Broker username (blank = allow anonymous):" ""
  if [ -n "$MQTT_USER" ]; then
    asksecret MQTT_PASS "  Broker password for '$MQTT_USER':"
    [ -z "$MQTT_PASS" ] && { warn "No password given — the broker will allow anonymous access."; MQTT_USER=""; }
  fi
  MQTT_BROKER="mosquitto"; MQTT_PORT="1883"; COMPANION_HA="true"
  ok "Will deploy Mosquitto (Home Assistant integration ON, companion points at it)."
else
  say "${DIM}  Point the companion at an existing broker (e.g. your Home Assistant Mosquitto,"
  say "  host 'core-mosquitto'). Leave the host blank to keep Home Assistant off.${N}"
  ask MQTT_BROKER "  Existing MQTT broker host (blank = no Home Assistant):" ""
  if [ -n "$MQTT_BROKER" ]; then
    ask MQTT_PORT "  Broker port:" "1883"
    ask MQTT_USER "  Broker username (blank = anonymous):" ""
    asksecret MQTT_PASS "  Broker password (blank = none):"
    COMPANION_HA="true"
    ok "Home Assistant integration ON (companion points at ${MQTT_BROKER}:${MQTT_PORT})."
  else
    COMPANION_HA="auto"
    say "${DIM}  No broker set — the Home Assistant integration stays off.${N}"
  fi
fi

say ""
say "${B}Data storage${N}"
say "${DIM}  Where app settings, playlists, triggers and uploaded apps live. A Docker"
say "  named volume is managed by Docker (simplest). A bind mount instead keeps the"
say "  data at a path you choose on this host, easier to back up or inspect directly.${N}"
askyn DATA_VOLUME "  Use a Docker named volume (recommended)?" y
DATA_BIND="${DATA_BIND:-}"
if [ "$DATA_VOLUME" = no ]; then
  ask DATA_BIND "  Host directory to bind-mount for data:" "/opt/sfgwcompanion"
fi

say ""
say "${B}Automatic image updates${N}"
say "${DIM}  Adds a Watchtower container that checks the companion (and broker) images"
say "  every 6h and automatically pulls + restarts to apply any newer version.${N}"
askyn AUTO_UPDATE "  Enable automatic updates with Watchtower?" n

say ""
say "${B}Developer mode${N}"
say "${DIM}  Primarily for app developers. Adds a \"Dev\" menu in the UI to run apps in"
say "  simulation mode (nothing is sent to the display), force a gateway resync, and"
say "  override the grid size while simulating. Leave off for normal use — you can turn"
say "  it on later by adding COMPANION_DEV_MODE=1 to the project's .env and re-running up.${N}"
askyn DEV_MODE "  Enable developer mode?" n

# --------------------------------------------------------------------------- #
# 3. Derive host facts
# --------------------------------------------------------------------------- #
HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -z "${HOST_IP:-}" ] && HOST_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '/src/ {print $7; exit}')
[ -z "${HOST_IP:-}" ] && HOST_IP="127.0.0.1"
PUBLIC_URL="http://${HOST_IP}:8000"
TZ_VAL=$(cat /etc/timezone 2>/dev/null || true)
[ -z "$TZ_VAL" ] && TZ_VAL=$( { timedatectl show -p Timezone --value 2>/dev/null; } || true)
[ -z "$TZ_VAL" ] && TZ_VAL="UTC"

# Install directory: /opt for root, ~ otherwise (so file writes need no sudo).
if [ "$(id -u)" -eq 0 ]; then DIR="/opt/splitflap-companion"; else DIR="$HOME/splitflap-companion"; fi
mkdir -p "$DIR"
info "Project directory: $DIR"

# Data location: a Docker named volume, or a host bind mount we create if missing.
if [ "$DATA_VOLUME" = no ]; then
  case "$DATA_BIND" in /*) ;; *) DATA_BIND="$DIR/$DATA_BIND" ;; esac   # make relative paths absolute
  mkdir -p "$DATA_BIND" 2>/dev/null || $SUDO mkdir -p "$DATA_BIND"
  DATA_MOUNT="${DATA_BIND}:/data"
  USE_NAMED_VOLUME=no
  ok "Data directory (bind mount): $DATA_BIND"
else
  DATA_MOUNT="companion-data:/data"
  USE_NAMED_VOLUME=yes
fi

# --------------------------------------------------------------------------- #
# 4. Write .env
# --------------------------------------------------------------------------- #
{
  echo "# Generated by install.sh on $(date 2>/dev/null || echo '?')"
  echo "GATEWAY_URL=$GATEWAY_URL"
  echo "COMPANION_PUBLIC_URL=$PUBLIC_URL"
  echo "COMPANION_HA=$COMPANION_HA"
  [ -n "$MQTT_BROKER" ] && echo "COMPANION_MQTT_BROKER=$MQTT_BROKER"
  [ -n "$MQTT_PORT" ]   && echo "COMPANION_MQTT_PORT=$MQTT_PORT"
  [ -n "$MQTT_USER" ]   && echo "COMPANION_MQTT_USER=$MQTT_USER"
  [ -n "$MQTT_PASS" ]   && echo "COMPANION_MQTT_PASSWORD=$MQTT_PASS"
  [ "$DEV_MODE" = yes ] && echo "COMPANION_DEV_MODE=1"
  echo "TZ=$TZ_VAL"
  echo "# Pin a specific release instead of latest, and set your GHCR owner if you forked:"
  echo "# GHCR_OWNER=avandeputte"
  echo "# COMPANION_TAG=latest"
} >"$DIR/.env"
chmod 600 "$DIR/.env"
ok "Wrote $DIR/.env"

# --------------------------------------------------------------------------- #
# 5. Mosquitto config (only if deploying the broker)
# --------------------------------------------------------------------------- #
if [ "$DEPLOY_MQTT" = yes ]; then
  mkdir -p "$DIR/mosquitto/config"
  {
    echo "listener 1883"
    echo "persistence true"
    echo "persistence_location /mosquitto/data/"
    echo "log_dest stdout"
    if [ -n "$MQTT_USER" ] && [ -n "$MQTT_PASS" ]; then
      echo "allow_anonymous false"
      echo "password_file /mosquitto/config/passwd"
    else
      echo "allow_anonymous true"
    fi
  } >"$DIR/mosquitto/config/mosquitto.conf"

  if [ -n "$MQTT_USER" ] && [ -n "$MQTT_PASS" ]; then
    info "Creating the broker password file …"
    $DOCKER run --rm -v "$DIR/mosquitto/config:/mosquitto/config" eclipse-mosquitto:2 \
      mosquitto_passwd -b -c /mosquitto/config/passwd "$MQTT_USER" "$MQTT_PASS" >/dev/null
    # mosquitto_passwd writes the file as root/0700; let the broker (uid 1883) read it.
    $SUDO chmod 0644 "$DIR/mosquitto/config/passwd" 2>/dev/null || true
  fi
  ok "Wrote Mosquitto config."
fi

# --------------------------------------------------------------------------- #
# 6. Write docker-compose.yml
# --------------------------------------------------------------------------- #
COMPOSE="$DIR/docker-compose.yml"
{
  echo "# Generated by install.sh — re-run the installer or edit .env to reconfigure."
  echo "services:"
  echo "  companion:"
  echo "    image: ghcr.io/\${GHCR_OWNER:-avandeputte}/splitflap-gateway-companion:\${COMPANION_TAG:-latest}"
  echo "    container_name: splitflap-companion"
  echo "    restart: unless-stopped"
  echo "    ports:"
  echo "      - \"8000:8000\""
  echo "    env_file:"
  echo "      - .env"
  echo "    volumes:"
  echo "      - ${DATA_MOUNT}"
  if [ "$DEPLOY_MQTT" = yes ]; then
    echo "    depends_on:"
    echo "      - mosquitto"
  fi
  if [ "$AUTO_UPDATE" = yes ]; then
    echo "    labels:"
    echo "      - com.centurylinklabs.watchtower.enable=true"
  fi

  if [ "$DEPLOY_MQTT" = yes ]; then
    echo ""
    echo "  mosquitto:"
    echo "    image: eclipse-mosquitto:2"
    echo "    container_name: splitflap-mosquitto"
    echo "    restart: unless-stopped"
    echo "    ports:"
    echo "      - \"1883:1883\""
    echo "    volumes:"
    echo "      - ./mosquitto/config:/mosquitto/config"
    echo "      - mosquitto-data:/mosquitto/data"
    echo "      - mosquitto-log:/mosquitto/log"
    if [ "$AUTO_UPDATE" = yes ]; then
      echo "    labels:"
      echo "      - com.centurylinklabs.watchtower.enable=true"
    fi
  fi

  if [ "$AUTO_UPDATE" = yes ]; then
    echo ""
    echo "  watchtower:"
    echo "    image: containrrr/watchtower:latest"
    echo "    container_name: splitflap-watchtower"
    echo "    restart: unless-stopped"
    echo "    environment:"
    echo "      - TZ=\${TZ:-UTC}"
    echo "      - WATCHTOWER_CLEANUP=true"          # remove the old image after updating
    echo "      - WATCHTOWER_LABEL_ENABLE=true"     # only touch containers we opt in
    echo "      - WATCHTOWER_SCHEDULE=0 0 */6 * * *"  # every 6h (6-field cron: sec min hour …)
    echo "    volumes:"
    echo "      - /var/run/docker.sock:/var/run/docker.sock"
    echo "    labels:"
    echo "      - com.centurylinklabs.watchtower.enable=true"
  fi

  # Only emit a `volumes:` section if a named volume is actually used (Watchtower
  # needs none; a bind mount declares no named volume).
  if [ "$USE_NAMED_VOLUME" = yes ] || [ "$DEPLOY_MQTT" = yes ]; then
    echo ""
    echo "volumes:"
    [ "$USE_NAMED_VOLUME" = yes ] && echo "  companion-data:"
    if [ "$DEPLOY_MQTT" = yes ]; then
      echo "  mosquitto-data:"
      echo "  mosquitto-log:"
    fi
  fi
} >"$COMPOSE"
ok "Wrote $COMPOSE"

# --------------------------------------------------------------------------- #
# 7. Launch
# --------------------------------------------------------------------------- #
say ""
info "Pulling images and starting containers …"
( cd "$DIR" && $DC pull && $DC up -d )

say ""
ok  "${B}Done.${N}"
say ""
say "  Companion UI : ${C}${PUBLIC_URL}${N}"
say "  Project dir  : ${DIR}   ${DIM}(docker-compose.yml + .env)${N}"
if [ "$USE_NAMED_VOLUME" = yes ]; then
  say "  Data         : ${DIM}Docker named volume 'companion-data'${N}"
else
  say "  Data         : ${DIM}bind mount at ${DATA_BIND}${N}"
fi
if [ "$DEPLOY_MQTT" = yes ]; then
  say "  MQTT broker  : ${HOST_IP}:1883  ${DIM}$([ -n "$MQTT_USER" ] && echo "user '$MQTT_USER' (password set)" || echo 'anonymous')${N}"
  say "                 ${DIM}Point Home Assistant's MQTT integration at this broker.${N}"
fi
if [ "$AUTO_UPDATE" = yes ]; then
  say "  Updates      : ${DIM}Watchtower auto-applies new images every 6h (pull + restart)"
  say "                 for the containers it manages. '$DC logs watchtower' to watch it.${N}"
fi
if [ "$DEV_MODE" = yes ]; then
  say "  Developer    : ${DIM}ON — a Dev menu is available in the UI (simulation, resync,"
  say "                 grid override). Remove COMPANION_DEV_MODE from .env to disable.${N}"
fi
say ""
say "  ${DIM}Manage:  cd $DIR"
say "           $DC logs -f companion     # follow logs"
say "           $DC pull && $DC up -d      # update to the latest image"
say "           $DC down                   # stop${N}"
say ""
