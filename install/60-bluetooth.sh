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

# Package naming bit us once: on Debian Bookworm the BlueALSA utilities
# live in `bluez-alsa-utils`, not the historical `bluealsa` (which is
# what the Buster/Bullseye-era docs called it). The systemd unit is
# still called bluealsa.service, but the binary is /usr/bin/bluealsad.
ensure_pkg bluez bluez-alsa-utils

# Discoverable mode is now opt-in via /bluetooth in the web UI (which
# calls `discoverable on` with an explicit per-session timeout). The
# DiscoverableTimeout=60 here is just a safety net so a manual `bluetoothctl
# discoverable on` over SSH doesn't leave the adapter permanently visible
# on the network.
#
# Pairable always-on is fine: pairing only succeeds while discoverable
# is also on, so this isn't an attack surface on its own.
cat > /etc/bluetooth/main.conf.d/beatbird.conf <<EOF
[General]
Class = 0x200414
DiscoverableTimeout = 60
PairableTimeout = 0
FastConnectable = true

[Policy]
AutoEnable=true
EOF

# Enable A2DP sink. Bookworm's binary is `bluealsad` (daemon suffix), not
# the historical `bluealsa` — they kept the unit name but renamed the
# binary it ExecStart's. Without this override, the unit launches with
# default args (no a2dp-sink profile) and a connecting phone fails the
# transport negotiation with no useful error.
mkdir -p /etc/systemd/system/bluealsa.service.d
cat > /etc/systemd/system/bluealsa.service.d/override.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/bluealsad -p a2dp-sink --xrun-boost=1
EOF

systemctl daemon-reload
systemctl enable --now bluetooth bluealsa 2>/dev/null || true

log_ok "Bluetooth A2DP sink enabled"
log_ok "Pair new devices at http://<host>:8080/bluetooth"
