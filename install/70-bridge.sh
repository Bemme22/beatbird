#!/usr/bin/env bash
# install/70-bridge.sh — Install the BeatBird Python package and services.
#
# Creates a venv at /opt/beatbird/venv and installs the package in editable mode
# (so `git pull` is enough to pick up changes).

source "$(dirname "$0")/_lib.sh"

VENV=/opt/beatbird/venv
BRIDGE_USER_HOME=$(getent passwd "$BEATBIRD_USER" | cut -d: -f6)

log_step "Creating venv at $VENV"
install -d -m 755 /opt/beatbird
python3 -m venv --system-site-packages "$VENV"

log_step "Installing Python dependencies"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet \
  pyserial \
  paho-mqtt \
  websocket-client \
  requests \
  pyyaml \
  pydantic \
  fastapi \
  'uvicorn[standard]'

# Spectrum FFT capture is currently disabled by default (spectrum_bands=0 in
# profiles) because PortAudio can't share the ALSA Loopback sub with CamillaDSP.
# To re-enable: set spectrum_bands>0 in the profile AND uncomment the deps below
# AND add a dsnoop alias in /etc/asound.conf (TODO documented in _template.yml).
# ensure_pkg python3-numpy python3-sounddevice libportaudio2 || true

log_step "Installing BeatBird package (editable)"
"$VENV/bin/pip" install --quiet -e "$REPO_DIR"

# ─── Runtime state directory ─────────────────────────────────────────────────
# The bridge service has ReadWritePaths=/var/lib/beatbird — must exist.
log_step "Creating /var/lib/beatbird"
install -d -m 755 -o "$BEATBIRD_USER" -g "$BEATBIRD_GROUP" /var/lib/beatbird

# ─── Bridge service ──────────────────────────────────────────────────────────
render_template \
  "$REPO_DIR/config/systemd/beatbird-bridge.service.tpl" \
  /etc/systemd/system/beatbird-bridge.service \
  "BEATBIRD_USER=$BEATBIRD_USER" \
  "VENV=$VENV" \
  "REPO_DIR=$REPO_DIR"

# ─── Webserver service (optional per profile) ────────────────────────────────
WEB_ENABLED="$(pq_bool web.enabled)"
WEB_PORT="$(pq_or web.port 8080)"
if [[ "$WEB_ENABLED" == "true" ]]; then
  render_template \
    "$REPO_DIR/config/systemd/beatbird-web.service.tpl" \
    /etc/systemd/system/beatbird-web.service \
    "BEATBIRD_USER=$BEATBIRD_USER" \
    "VENV=$VENV" \
    "REPO_DIR=$REPO_DIR" \
    "WEB_PORT=$WEB_PORT"
  enable_service beatbird-web.service
fi

enable_service beatbird-bridge.service
