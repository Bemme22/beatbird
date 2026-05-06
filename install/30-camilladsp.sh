#!/usr/bin/env bash
# install/30-camilladsp.sh — CamillaDSP 4.x + GUI install and config.
#
#   - Download the CamillaDSP binary if not present
#   - Install the GUI (optional, at <hostname>.local:5000)
#   - Copy the profile-selected DSP config file to /etc/camilladsp/config.yml
#   - Install the systemd unit

source "$(dirname "$0")/_lib.sh"

CDSP_VERSION="${CAMILLADSP_VERSION:-4.1.2}"
CDSP_BIN=/usr/local/bin/camilladsp
CDSP_CONF_NAME="$(pq audio.camilladsp_config)"
CDSP_CONF_SRC="$REPO_DIR/config/camilladsp/${CDSP_CONF_NAME}.yml"
CDSP_CONF_DST=/etc/camilladsp/config.yml

# ─── Binary ──────────────────────────────────────────────────────────────────
if [[ ! -x "$CDSP_BIN" ]] || ! "$CDSP_BIN" --version 2>/dev/null | grep -q "$CDSP_VERSION"; then
  log_step "Installing CamillaDSP $CDSP_VERSION"
  ARCH=$(dpkg --print-architecture)
  case "$ARCH" in
    arm64) CDSP_ARCH=aarch64 ;;
    armhf) CDSP_ARCH=armv7-unknown-linux-gnueabihf ;;
    amd64) CDSP_ARCH=x86_64-unknown-linux-gnu ;;
    *)     log_err "Unsupported architecture: $ARCH"; exit 1 ;;
  esac
  URL="https://github.com/HEnquist/camilladsp/releases/download/v${CDSP_VERSION}/camilladsp-linux-${CDSP_ARCH}.tar.gz"
  TMPDIR=$(mktemp -d)
  curl -fL -o "$TMPDIR/cdsp.tar.gz" "$URL"
  tar xzf "$TMPDIR/cdsp.tar.gz" -C "$TMPDIR"
  install -m 755 "$TMPDIR/camilladsp" "$CDSP_BIN"
  rm -rf "$TMPDIR"
  log_ok "$($CDSP_BIN --version)"
fi

# ─── Config ──────────────────────────────────────────────────────────────────
install -d -m 755 /etc/camilladsp
install -d -m 775 -g "$BEATBIRD_GROUP" /var/lib/camilladsp 2>/dev/null || true

if [[ ! -f "$CDSP_CONF_SRC" ]]; then
  log_warn "$CDSP_CONF_SRC not found — using _stub.yml as fallback"
  CDSP_CONF_SRC="$REPO_DIR/config/camilladsp/_stub.yml"
fi

install -m 644 "$CDSP_CONF_SRC" "$CDSP_CONF_DST"
log_ok "DSP config: $CDSP_CONF_NAME → $CDSP_CONF_DST"

# Volume state file — CamillaDSP persists the master volume here between runs
touch /var/lib/camilladsp/camilladsp-state.yml
chown "$BEATBIRD_USER":"$BEATBIRD_GROUP" /var/lib/camilladsp/camilladsp-state.yml 2>/dev/null || true

# ─── systemd unit ────────────────────────────────────────────────────────────
render_template \
  "$REPO_DIR/config/systemd/camilladsp.service.tpl" \
  /etc/systemd/system/camilladsp.service \
  "BEATBIRD_USER=$BEATBIRD_USER" \
  "BEATBIRD_GROUP=$BEATBIRD_GROUP"

enable_service camilladsp.service
