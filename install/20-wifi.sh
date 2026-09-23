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
# powersave=2 => DISABLE WiFi power-save for this connection, persistently.
# (0=default, 1=don't-touch, 2=disable, 3=enable.) Without this, NM re-enables
# power-save on every (re)connect — including the watchdog's NM restarts — which
# makes the radio miss DHCP/multicast packets => recurring IPv4-loss => the
# ".local resolves only to fe80:: , no A record" symptom. The one-shot
# wifi-powersave-off.service only covers the first boot connection; this covers
# every reconnect.
powersave=2

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
# Three jobs in one daemon (script: config/wifi/beatbird-wifi-watchdog):
#  1. Pings the gateway every 30 s to keep the USB dongle warm (idle-disconnect
#     workaround) and detect link failure early.
#  2. After 5 consecutive failures, recovers in escalating stages:
#     reconnect → radio reset (USB re-authorize / driver reload) → reboot,
#     saving the journal tail to /boot/firmware before the reboot.
#  3. Telemetry — every iteration emits a one-line snapshot
#     (rssi/rate/bssid/ping) under journal tag "beatbird-wifi". This is the
#     post-mortem trail for "speaker vanished but the display showed no
#     error" — we can see if RSSI was already at -82 dBm or if the BSSID
#     flipped (AP roam — TCP/UDP sessions don't survive that).
# ⚠️ beatbird-update does NOT refresh /usr/local/sbin — rerun this role (or
# install the file into the overlayroot base) after changing the script.
log_step "WiFi keepalive + watchdog + telemetry"
ensure_pkg iw iproute2
install -m 755 -o root -g root "$REPO_DIR/config/wifi/beatbird-wifi-watchdog"   /usr/local/sbin/beatbird-wifi-watchdog

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

# ─── Hardware watchdog ───────────────────────────────────────────────────────
# The WiFi watchdog's reboot stage needs a running userspace; this covers a
# hung kernel / PID 1. Takes effect on the next boot (or daemon-reexec).
log_step "Hardware watchdog (bcm2835-wdt, 15 s)"
install -d /etc/systemd/system.conf.d
install -m 644 "$REPO_DIR/config/systemd/beatbird-hw-watchdog.conf"   /etc/systemd/system.conf.d/beatbird-hw-watchdog.conf
log_ok "RuntimeWatchdogSec=15 (active after reboot)"
