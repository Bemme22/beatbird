#!/usr/bin/env bash
# install/50-snapcast.sh — Snapclient for multi-room audio (optional).

source "$(dirname "$0")/_lib.sh"

SN_ENABLED="$(pq_bool sources.snapcast.enabled)"
if [[ "$SN_ENABLED" != "true" ]]; then
  log_step "Snapcast disabled in profile — skipping"
  exit 0
fi

# Resolve the real server. The profile YAML carries a placeholder
# (192.168.1.10) because the repo is public; the actual host lives in
# secrets/snapcast.host and is written into /etc/beatbird/env as
# BEATBIRD_SNAPCAST_SERVER by 00-base.sh. Prefer the env var; fall
# back to the profile placeholder only if the secrets file was empty
# during install (in which case the user wanted snapcast disabled
# anyway and the placeholder won't resolve).
SECRETS_SERVER=""
if [[ -s "$SECRETS_DIR/snapcast.host" ]]; then
  SECRETS_SERVER="$(head -1 "$SECRETS_DIR/snapcast.host" | tr -d '[:space:]')"
fi
SERVER="${SECRETS_SERVER:-$(pq sources.snapcast.server)}"
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
# …and restart it, because `enable --now` is a no-op on an already-running
# service and would leave the config we just rendered unread. That is not
# hypothetical: `ensure_pkg snapclient` above makes apt install the package,
# Debian policy starts the unit immediately with the stock (empty) defaults,
# and only then does render_template write the real ones. RobinPi ran for
# three days that way — argv was a bare `snapclient --logsink=system`, so it
# ignored the configured host AND the `--soundcard beatbird_mix` setting,
# grabbed the raw ALSA Loopback that CamillaDSP already holds, and logged
# "Device or resource busy" followed by "No chunk received for 5000ms".
# Found 2026-09-16; the file's mtime was 12 s LATER than the unit's start.
restart_if_active snapclient.service
log_ok "snapclient connecting to $SERVER (latency ${LATENCY}ms)"
