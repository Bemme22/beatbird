#!/usr/bin/env bash
# install/20-wifi.sh — WiFi configuration per profile.
#
#   - Write wpa_supplicant conf (or NM keyfile on Bookworm w/ NM)
#   - Disable onboard radio if USB dongle is preferred (Pi Zero 2W in metal)
#   - Kill WiFi power-save (causes audio dropouts on Raspotify/Snapcast)
#   - Disable onboard Bluetooth if profile says so

source "$(dirname "$0")/_lib.sh"

SSID="$(pq wifi.ssid)"
COUNTRY="$(pq_or wifi.country DE)"
USE_USB="$(pq_bool wifi.use_usb_dongle)"
DISABLE_ONBOARD_WIFI="$(pq_bool wifi.disable_onboard_radio)"
DISABLE_BT="$(pq_bool wifi.disable_bluetooth)"
PSK_FILE="$ETC_DIR/wifi.pass"

# ─── config.txt overlays ─────────────────────────────────────────────────────
log_step "WiFi: config.txt overlays"
if [[ "$DISABLE_ONBOARD_WIFI" == "true" ]]; then
  ensure_line_in_config_txt "dtoverlay=disable-wifi"
fi
if [[ "$DISABLE_BT" == "true" ]]; then
  ensure_line_in_config_txt "dtoverlay=disable-bt"
fi

# ─── WiFi credentials ────────────────────────────────────────────────────────
if [[ -z "$SSID" || "$SSID" == "your-ssid" ]]; then
  log_warn "wifi.ssid is not set in profile — skipping WiFi config"
else
  PSK=""
  [[ -f "$PSK_FILE" ]] && PSK="$(cat "$PSK_FILE")"

  # Detect which network stack is in use
  if systemctl list-unit-files 2>/dev/null | grep -q '^NetworkManager'; then
    log_step "WiFi: writing NetworkManager connection"
    NM_FILE="/etc/NetworkManager/system-connections/beatbird.nmconnection"
    cat > "$NM_FILE" <<EOF
[connection]
id=beatbird
type=wifi
autoconnect=true

[wifi]
mode=infrastructure
ssid=$SSID

[wifi-security]
key-mgmt=wpa-psk
psk=$PSK

[ipv4]
method=auto

[ipv6]
method=auto
EOF
    chmod 600 "$NM_FILE"
    nmcli connection reload 2>/dev/null || true
    log_ok "NetworkManager profile 'beatbird' configured"
  else
    log_step "WiFi: writing wpa_supplicant conf"
    WPA_CONF=/etc/wpa_supplicant/wpa_supplicant.conf
    cat > "$WPA_CONF" <<EOF
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=$COUNTRY

network={
    ssid="$SSID"
    psk="$PSK"
    key_mgmt=WPA-PSK
}
EOF
    chmod 600 "$WPA_CONF"
    log_ok "wpa_supplicant.conf written"
  fi
fi

# ─── Kill WiFi power-save (critical for audio dropouts) ──────────────────────
log_step "Disable WiFi powersave"
mkdir -p /etc/systemd/system
cat > /etc/systemd/system/wifi-powersave-off.service <<'EOF'
[Unit]
Description=Disable WiFi power management
After=network.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'for i in wlan0 wlan1; do /sbin/iw dev $i set power_save off 2>/dev/null || true; done'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
enable_service wifi-powersave-off.service

# ─── WiFi keepalive (prevents suspend/idle drops) ────────────────────────────
log_step "WiFi keepalive"
cat > /etc/systemd/system/wifi-keepalive.service <<'EOF'
[Unit]
Description=Periodic ping to keep WiFi active (prevents idle disconnects)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/bin/sh -c 'while true; do ping -c1 -W2 1.1.1.1 >/dev/null 2>&1 || ping -c1 -W2 8.8.8.8 >/dev/null 2>&1 || true; sleep 30; done'
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
EOF
enable_service wifi-keepalive.service
