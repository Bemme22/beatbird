#!/usr/bin/env bash
# install/25-static-ip.sh — optional per-Pi static IP fallback.
#
# Without this, the Pi's IP is whatever the Fritzbox hands out via DHCP.
# A Fritzbox restart, a long lease expiry, or the "always assign the same
# IP" checkbox getting unticked all silently rotate the Pi to a new IP,
# breaking every hardcoded reference (SSH bookmarks, mDNS caches on Win
# clients, MQTT topic links, …).
#
# This script lays down a dhcpcd/NetworkManager static-IP fallback. It's
# the *second* line of defence — the Fritzbox-side pin remains the first.
# Both together = the IP stays put even if one mechanism slips.
#
# Driven by an optional secrets/static-ip.conf with shell-style content:
#
#     IPV4=192.168.178.113/24
#     GATEWAY=192.168.178.1
#     DNS="192.168.178.1 1.1.1.1"   # QUOTE it — sourced as shell, space-separated
#
# If the file doesn't exist (or has no IPV4/GATEWAY), this script is a no-op.

source "$(dirname "$0")/_lib.sh"

CONF="$SECRETS_DIR/static-ip.conf"

if [[ ! -s "$CONF" ]]; then
  log_step "no $CONF — skipping static-ip reservation (DHCP-only)"
  exit 0
fi

# shellcheck source=/dev/null
source "$CONF"

# Optional feature: a present-but-incomplete conf (e.g. the commented-out
# template from `make secrets`) must NOT abort the whole install. Skip
# gracefully unless both required values are actually set.
if [[ -z "${IPV4:-}" || -z "${GATEWAY:-}" ]]; then
  log_step "$CONF present but IPV4/GATEWAY unset — skipping static-ip (DHCP-only)"
  exit 0
fi
DNS="${DNS:-$GATEWAY 1.1.1.1}"

log_step "Static-IP reservation on wlan0"

if systemctl list-unit-files 2>/dev/null | grep -q '^NetworkManager'; then
  # Trixie / NetworkManager. Modify the existing "beatbird" connection
  # (written by 20-wifi.sh) to set ipv4.method=manual + addresses + gateway.
  log_step "  NetworkManager path"
  nmcli connection modify beatbird \
    ipv4.method manual \
    ipv4.addresses "$IPV4" \
    ipv4.gateway "$GATEWAY" \
    ipv4.dns "$DNS" \
    >/dev/null
  nmcli connection up beatbird >/dev/null 2>&1 || true
  log_ok "NetworkManager: wlan0 → $IPV4"
else
  # Bookworm / dhcpcd. Drop a /etc/dhcpcd.conf.d/ snippet so the main
  # config stays clean. dhcpcd reads any .conf in this dir on startup.
  log_step "  dhcpcd path"
  mkdir -p /etc/dhcpcd.conf.d
  cat > /etc/dhcpcd.conf.d/beatbird-static.conf <<EOF
# Beatbird static-IP fallback for wlan0. Rendered by
# install/25-static-ip.sh from secrets/static-ip.conf. Acts as a
# second line of defence against the Fritzbox losing its IP pin.
interface wlan0
static ip_address=$IPV4
static routers=$GATEWAY
static domain_name_servers=$DNS
EOF
  systemctl restart dhcpcd 2>/dev/null || true
  log_ok "dhcpcd: wlan0 → $IPV4"
fi
