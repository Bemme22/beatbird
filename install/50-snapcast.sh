#!/usr/bin/env bash
# install/50-snapcast.sh — Snapclient for multi-room audio (optional).

source "$(dirname "$0")/_lib.sh"

SN_ENABLED="$(pq_bool sources.snapcast.enabled)"
if [[ "$SN_ENABLED" != "true" ]]; then
  log_step "Snapcast disabled in profile — skipping"
  exit 0
fi

SERVER="$(pq sources.snapcast.server)"
LATENCY="$(pq_or sources.snapcast.latency_ms 30)"
HOSTNAME_VAL="$(pq identity.friendly_name)"

log_step "Installing snapclient"
ensure_pkg snapclient

render_template \
  "$REPO_DIR/config/snapcast/snapclient.conf.tpl" \
  /etc/default/snapclient \
  "SERVER=$SERVER" \
  "LATENCY=$LATENCY" \
  "HOSTNAME=$HOSTNAME_VAL"

systemctl enable --now snapclient.service
log_ok "snapclient connecting to $SERVER (latency ${LATENCY}ms)"
