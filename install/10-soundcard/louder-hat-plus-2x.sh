#!/usr/bin/env bash
# install/10-soundcard/louder-hat-plus-2x.sh
# Sonocotta Louder Hat Plus 2X — dual TAS5825M (stereo @ primary + sub @ secondary).
#
# Known-good setup for Beat #1. Reproduces the Makefile logic from the legacy
# repo but reads addresses from the profile and stays idempotent.

source "$(dirname "$0")/../_lib.sh"

PRIMARY="$(pq_or soundcard.primary_i2c 0x4c)"
SECONDARY="$(pq_or soundcard.secondary_i2c 0x4d)"
SUB_EN="$(pq_bool soundcard.sub_enabled)"
SUB_XO="$(pq_or soundcard.sub_crossover_hz 150)"
SUB_DVOL="$(pq_or soundcard.sub_digital_volume 110)"

# ─── /boot/firmware/config.txt ───────────────────────────────────────────────
log_step "config.txt overlay"
ensure_line_in_config_txt "dtparam=i2c_arm=on"
ensure_line_in_config_txt "dtparam=i2s=on"
# NOTE: The overlay name is the Sonocotta-shipped 'tas58xx-dual' — NEVER edit
# the DTS directly; use the overlay parameters instead.
ensure_line_in_config_txt "dtoverlay=tas58xx-dual,i2creg_primary=$PRIMARY,i2creg_secondary=$SECONDARY"
# Do NOT enable ti,fault-monitor in dual-DAC mode — polling disrupts the
# shared I2S clock and causes intermittent glitching.

# ALSA Loopback — used as the single virtual capture device that CamillaDSP
# and go-librespot/BlueALSA/etc all share.
# Uses ensure_module_loaded for Trixie compatibility (dtoverlay alone
# doesn't reliably load snd-aloop on Debian 13+).
ensure_module_loaded snd-aloop

# ─── Driver package ──────────────────────────────────────────────────────────
# The upstream Sonocotta driver ships as an apt package on their repo; if
# unavailable, the user has already flashed an image with it present.
if ! modinfo snd-soc-tas58xx >/dev/null 2>&1; then
  log_warn "snd-soc-tas58xx kernel module not found."
  log_warn "Install per Sonocotta's instructions, then re-run this script."
fi

# ─── amixer-init script (boot-time retry loop) ───────────────────────────────
AMIXER_PATH=/usr/local/sbin/beatbird-louder-hat-init
cat > "$AMIXER_PATH" <<'AMIXER_EOF'
#!/bin/bash
# beatbird-louder-hat-init — retry-on-boot amixer settings for Louder Hat Plus 2X
# Pi Zero 2W boots slow; the ALSA card may take 3-8s to appear.

CARD=LouderRaspberry
MAX_TRIES=60  # ~30s @ 0.5s each

for i in $(seq 1 $MAX_TRIES); do
  amixer -c "$CARD" scontents >/dev/null 2>&1 && break
  sleep 0.5
done

if ! amixer -c "$CARD" scontents >/dev/null 2>&1; then
  echo "louder-hat-init: $CARD not found after $MAX_TRIES tries" >&2
  exit 1
fi

# Stereo amp
amixer -c "$CARD" cset numid=1 103   # Digital volume ~-6 dB
amixer -c "$CARD" cset numid=2 25    # Analog gain (~-3 dB from max, safe at 24V PVDD)
amixer -c "$CARD" cset numid=4 0     # L channel 0 dB
amixer -c "$CARD" cset numid=5 0     # R channel 0 dB
amixer -c "$CARD" cset numid=3 1     # Built-in EQ OFF (CamillaDSP handles it)

# Sub amp  (values injected at install time below)
amixer -c "$CARD" cset numid=21 __SUB_DVOL__
amixer -c "$CARD" cset numid=22 25
amixer -c "$CARD" cset numid=25 __SUB_XO_ITEM__
amixer -c "$CARD" cset numid=24 0
amixer -c "$CARD" cset numid=23 1    # Built-in EQ OFF

echo "louder-hat-init: $CARD configured (sub DV=__SUB_DVOL__, XO item=__SUB_XO_ITEM__)"
AMIXER_EOF

# Crossover frequency → ALSA enum item:
#   item 0 = off, 1..N = frequencies. '9' = 140 Hz on the current driver.
# Map a few well-known values; fall back to 9 (140 Hz) if unknown.
case "$SUB_XO" in
  off|OFF|0) SUB_XO_ITEM=0 ;;
  60)  SUB_XO_ITEM=1 ;;
  80)  SUB_XO_ITEM=3 ;;
  100) SUB_XO_ITEM=5 ;;
  120) SUB_XO_ITEM=7 ;;
  140) SUB_XO_ITEM=9 ;;
  150) SUB_XO_ITEM=9 ;;  # closest available
  160) SUB_XO_ITEM=10 ;;
  180) SUB_XO_ITEM=12 ;;
  *)   SUB_XO_ITEM=9 ;;
esac

sed -i "s/__SUB_DVOL__/$SUB_DVOL/g; s/__SUB_XO_ITEM__/$SUB_XO_ITEM/g" "$AMIXER_PATH"
chmod 755 "$AMIXER_PATH"
log_ok "wrote $AMIXER_PATH (sub XO=${SUB_XO} Hz → item $SUB_XO_ITEM)"

# ─── systemd service ─────────────────────────────────────────────────────────
cat > /etc/systemd/system/louder-hat-init.service <<EOF
[Unit]
Description=Louder Hat amplifier level init (Plus 2X)
After=sound.target
Wants=sound.target

[Service]
Type=oneshot
ExecStart=$AMIXER_PATH
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

enable_service_at_boot louder-hat-init.service
