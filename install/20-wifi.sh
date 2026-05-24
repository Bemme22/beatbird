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

# ─── WiFi keepalive + self-healing watchdog + telemetry ─────────────────────
# Three jobs in one daemon:
#  1. Pings the gateway every 30 s to keep the USB dongle warm (idle-disconnect
#     workaround) and detect link failure early.
#  2. After 5 consecutive ping failures, bounces NetworkManager / wpa_supplicant
#     and as a last resort cycles the wlan interface.
#  3. Telemetry — every iteration emits a one-line snapshot
#     (rssi/rate/bssid/ping) under journal tag "beatbird-wifi". This is the
#     post-mortem trail for "speaker vanished but the display showed no
#     error" — we can see if RSSI was already at -82 dBm or if the BSSID
#     flipped (AP roam — TCP/UDP sessions don't survive that).
log_step "WiFi keepalive + watchdog + telemetry"
ensure_pkg iw iproute2
install -m 755 -o root -g root /dev/stdin /usr/local/sbin/beatbird-wifi-watchdog <<'EOF'
#!/usr/bin/env bash
# beatbird-wifi-watchdog — keepalive + recovery + RSSI telemetry.
# `journalctl -t beatbird-wifi --since "1 hour ago"` shows just the WiFi trail.
set -uo pipefail

FAIL_THRESHOLD=5
SLEEP_S=30
fails=0
last_bssid=""
last_rssi=""

pick_iface() {
  # Prefer a wlan* with carrier up. Falls back to whatever wlan* exists.
  local i iface
  for i in /sys/class/net/wlan*; do
    [[ -e "$i" ]] || continue
    iface=$(basename "$i")
    if [[ "$(cat "$i/operstate" 2>/dev/null)" == "up" ]]; then
      echo "$iface"; return
    fi
  done
  for i in /sys/class/net/wlan*; do
    [[ -e "$i" ]] && { basename "$i"; return; }
  done
  echo "wlan0"
}

pick_gw() {
  local gw
  gw="$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}')"
  [[ -z "$gw" ]] && gw="192.168.1.1"
  echo "$gw"
}

# Parse `iw dev <iface> link` → echo "rssi rate bssid"
wifi_link() {
  iw dev "$1" link 2>/dev/null | awk '
    /Connected to/ { bssid=$3 }
    /signal:/      { rssi=$2 }
    /tx bitrate:/  { rate=$3$4 }
    END { printf "%s %s %s\n", (rssi?rssi:"?"), (rate?rate:"?"), (bssid?bssid:"?") }
  '
}

GW="$(pick_gw)"
echo "wifi-watchdog: starting iface=$(pick_iface) gw=$GW"

while true; do
  IFACE="$(pick_iface)"
  read -r rssi rate bssid <<<"$(wifi_link "$IFACE")"

  if ping -c1 -W2 "$GW" >/dev/null 2>&1; then
    ping_status="ok"
    fails=0
  else
    fails=$((fails + 1))
    ping_status="fail($fails/$FAIL_THRESHOLD)"
  fi

  echo "wifi: iface=$IFACE rssi=${rssi}dBm rate=$rate bssid=$bssid gw=$GW ping=$ping_status"

  if [[ -n "$last_bssid" && "$bssid" != "$last_bssid" && "$bssid" != "?" && "$last_bssid" != "?" ]]; then
    echo "wifi: ROAM bssid $last_bssid -> $bssid (rssi was ${last_rssi}, now ${rssi})"
  fi
  last_bssid="$bssid"
  last_rssi="$rssi"

  if [[ "$fails" -ge "$FAIL_THRESHOLD" ]]; then
    echo "wifi-watchdog: threshold hit — dumping full state before recovery"
    iw dev "$IFACE" station dump 2>&1 || true
    ip -4 addr show "$IFACE" 2>&1 || true
    ip route 2>&1 || true
    echo "wifi-watchdog: attempting recovery"
    if systemctl is-active --quiet NetworkManager; then
      systemctl restart NetworkManager
    elif systemctl is-active --quiet wpa_supplicant; then
      systemctl restart wpa_supplicant
    else
      ip link set "$IFACE" down; sleep 2; ip link set "$IFACE" up
    fi
    sleep 30
    fails=0
    GW="$(pick_gw)"
  fi

  sleep "$SLEEP_S"
done
EOF

cat > /etc/systemd/system/wifi-keepalive.service <<'EOF'
[Unit]
Description=BeatBird WiFi keepalive + watchdog + telemetry
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/sbin/beatbird-wifi-watchdog
Restart=always
RestartSec=60
StandardOutput=journal
StandardError=journal
SyslogIdentifier=beatbird-wifi

[Install]
WantedBy=multi-user.target
EOF
enable_service wifi-keepalive.service
