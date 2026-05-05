#!/usr/bin/env bash
# install/60-bluetooth.sh — Bluetooth A2DP sink via bluealsa.
#
# Skipped entirely if profile has bluetooth disabled or wifi.disable_bluetooth=true.

source "$(dirname "$0")/_lib.sh"

BT_ENABLED="$(pq_bool sources.bluetooth.enabled)"
BT_DISABLED_IN_KERNEL="$(pq_bool wifi.disable_bluetooth)"

if [[ "$BT_ENABLED" != "true" ]]; then
  log_step "Bluetooth disabled in profile — skipping"
  exit 0
fi

if [[ "$BT_DISABLED_IN_KERNEL" == "true" ]]; then
  log_warn "sources.bluetooth.enabled=true but wifi.disable_bluetooth=true — inconsistent, skipping"
  exit 0
fi

ensure_pkg bluez bluealsa

# Make the Pi discoverable and auto-accept pairings — simple approach for now.
# For production: pair explicitly in UI and persist with bluetoothctl.
cat > /etc/bluetooth/main.conf.d/beatbird.conf <<EOF
[General]
Class = 0x200414
DiscoverableTimeout = 0
PairableTimeout = 0
FastConnectable = true

[Policy]
AutoEnable=true
EOF

# Enable A2DP sink
cat > /etc/systemd/system/bluealsa.service.d/override.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/bluealsa -p a2dp-sink --xrun-boost=1
EOF

systemctl daemon-reload
systemctl enable --now bluetooth bluealsa 2>/dev/null || true

log_ok "Bluetooth A2DP sink enabled"
log_warn "First pairing requires 'bluetoothctl' interactive setup."
