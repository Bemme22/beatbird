#!/usr/bin/env bash
# install/40-go-librespot.sh — Spotify Connect via go-librespot.
#
# IMPORTANT: raspotify must not be installed alongside. The installer removes
# it if found to avoid mDNS name collisions.

source "$(dirname "$0")/_lib.sh"

SP_ENABLED="$(pq_bool sources.spotify.enabled)"
if [[ "$SP_ENABLED" != "true" ]]; then
  log_step "Spotify disabled in profile — skipping"
  exit 0
fi

GLSP_VERSION="${GOLIBRESPOT_VERSION:-0.8.0}"
GLSP_BIN=/usr/local/bin/go-librespot
GLSP_USER_HOME=$(getent passwd "$BEATBIRD_USER" | cut -d: -f6)
GLSP_CONF_DIR="$GLSP_USER_HOME/.config/go-librespot"
GLSP_CONF_DST="$GLSP_CONF_DIR/config.yml"

# ─── Remove raspotify if present ─────────────────────────────────────────────
if dpkg -s raspotify >/dev/null 2>&1; then
  log_step "Removing raspotify (collides with go-librespot)"
  systemctl disable --now raspotify 2>/dev/null || true
  apt-get purge -y raspotify
fi

# ─── Dependencies ────────────────────────────────────────────────────────────
ensure_pkg libogg-dev libvorbis-dev libasound2-dev avahi-daemon

# ─── Binary ──────────────────────────────────────────────────────────────────
if [[ ! -x "$GLSP_BIN" ]] || ! "$GLSP_BIN" --version 2>/dev/null | grep -q "$GLSP_VERSION"; then
  log_step "Installing go-librespot $GLSP_VERSION"
  ARCH=$(dpkg --print-architecture)
  case "$ARCH" in
    arm64) GLSP_ARCH=arm64 ;;
    armhf) GLSP_ARCH=armv6 ;;
    amd64) GLSP_ARCH=amd64 ;;
    *)     log_err "Unsupported architecture: $ARCH"; exit 1 ;;
  esac
  URL="https://github.com/devgianlu/go-librespot/releases/download/v${GLSP_VERSION}/go-librespot_linux_${GLSP_ARCH}.tar.gz"
  TMPDIR=$(mktemp -d)
  curl -fL -o "$TMPDIR/glsp.tar.gz" "$URL"
  tar xzf "$TMPDIR/glsp.tar.gz" -C "$TMPDIR"
  install -m 755 "$TMPDIR/go-librespot" "$GLSP_BIN"
  rm -rf "$TMPDIR"
fi

# ─── Config ──────────────────────────────────────────────────────────────────
DEVICE_NAME="$(pq_or sources.spotify.device_name "$(pq identity.friendly_name)")"
BITRATE="$(pq_or sources.spotify.bitrate 320)"
NORMALISATION="$(pq_bool sources.spotify.normalisation)"

install -d -m 755 -o "$BEATBIRD_USER" -g "$BEATBIRD_GROUP" "$GLSP_CONF_DIR"
render_template \
  "$REPO_DIR/config/go-librespot/config.yml.tpl" \
  "$GLSP_CONF_DST" \
  "DEVICE_NAME=$DEVICE_NAME" \
  "BITRATE=$BITRATE" \
  "NORMALISATION=$NORMALISATION"
chown "$BEATBIRD_USER":"$BEATBIRD_GROUP" "$GLSP_CONF_DST"
chmod 644 "$GLSP_CONF_DST"
log_ok "go-librespot config written (device: $DEVICE_NAME)"

# ─── systemd unit ────────────────────────────────────────────────────────────
render_template \
  "$REPO_DIR/config/systemd/go-librespot.service.tpl" \
  /etc/systemd/system/go-librespot.service \
  "BEATBIRD_USER=$BEATBIRD_USER" \
  "GLSP_BIN=$GLSP_BIN" \
  "GLSP_CONF=$GLSP_CONF_DST"

enable_service go-librespot.service
