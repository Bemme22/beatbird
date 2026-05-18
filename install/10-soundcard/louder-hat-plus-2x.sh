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
# Uses Control-NAMEN, not numids — numids drift between Sonocotta driver
# versions and produced silent "Operation not permitted" failures.
# Verified names on Beat #1 (2026-05-18 dump):
#   primary 0x4C → prefix '2.0' (stereo)
#   secondary 0x4D → prefix '0.1' (PBTL mono sub, NOT '2.1' as one would guess)
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

set_q() { amixer -c "$CARD" -q sset "$1" "$2" 2>/dev/null || true; }

# Stereo amp — primary 0x4C, prefix '2.0'
set_q '2.0 Digital'             103     # ~-6 dB
set_q '2.0 Analog Gain'          25     # ~-3 dB from max (safe @ 24V PVDD)
set_q '2.0 Channel Left Gain'     0     # 0 dB
set_q '2.0 Channel Right Gain'    0     # 0 dB
set_q '2.0 Equalizer'           Off     # CamillaDSP handles EQ

# Sub amp — secondary 0x4D, PBTL mono, prefix '0.1'
set_q '0.1 Digital'             __SUB_DVOL__
set_q '0.1 Analog Gain'          25
set_q '0.1 Mono Channel Gain'     0
set_q '0.1 Crossover Frequency' '__SUB_XO_VAL__'
set_q '0.1 Equalizer'           Off

echo "louder-hat-init: $CARD configured (sub DV=__SUB_DVOL__, XO=__SUB_XO_VAL__)"
AMIXER_EOF

# Crossover enum on the driver accepts string values directly: 'OFF', '60 Hz'
# through '150 Hz' in 10 Hz steps. Avoid numeric item indices — they shifted
# between driver versions (was the original bug with numid=25).
case "$SUB_XO" in
  off|OFF|0) SUB_XO_VAL="OFF" ;;
  60|70|80|90|100|110|120|130|140|150) SUB_XO_VAL="$SUB_XO Hz" ;;
  *)
    log_warn "sub_crossover_hz=$SUB_XO not in driver enum (60–150 Hz, 10 Hz steps); defaulting to 150 Hz"
    SUB_XO_VAL="150 Hz"
    ;;
esac

# Use | as sed separator since SUB_XO_VAL contains a space
sed -i "s/__SUB_DVOL__/$SUB_DVOL/g; s|__SUB_XO_VAL__|$SUB_XO_VAL|g" "$AMIXER_PATH"
chmod 755 "$AMIXER_PATH"
log_ok "wrote $AMIXER_PATH (sub XO=$SUB_XO_VAL, DV=$SUB_DVOL)"

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
