#!/usr/bin/env bash
# install/10-soundcard/louder-hat-triple.sh
# Lounge — 1× Louder Hat Plus 2X (2× TAS5825M) + 1× Louder Hat 1X non-Plus
# (1× TAS5805M), three boards on ONE shared I2S/I2C line, driven as a
# 6-channel TDM stack.
#
# ARCHITECTURE 2 (all-CamillaDSP): the TAS chips run FLAT — no internal EQ,
# no internal crossover. Every crossover/EQ/delay/level is in CamillaDSP
# (config/camilladsp/lounge.yml, 8-channel TDM). This replaces the earlier
# Architecture-1 approach (TAS internal 15-band-EQ crossover) which dead-ended
# at voicing on the Pi 4 — that's why the Pi 5 + all-CDSP plan exists.
#
# Channel / address / slot map (verified on the bench 2026-05-29):
#   0x4C TAS5825M stereo  Mid L / Mid R   TDM slots 0,1  (offset 0)
#   0x4D TAS5825M PBTL    8" woofer       TDM slot  2    (offset 64)
#   0x2D TAS5805M stereo  Ribbon L / R    TDM slots 4,5  (offset 128)
#
# The 6-ch-TDM driver + overlay come from install/05-tas-driver.sh (TDM patch
# + tas58xx-triple overlay). This script does config.txt + gain staging only.

source "$(dirname "$0")/../_lib.sh"

log_step "config.txt (Triple TDM DAC Stack)"
ensure_line_in_config_txt "dtparam=i2c_arm=on"
ensure_line_in_config_txt "dtparam=i2s=on"

# Triple TDM overlay. Addresses pinned to the verified bus values
# (mid 0x4c, woofer 0x4d, ribbon 0x2d — the ribbon is 0x2d, NOT the 0x2e
# the old docs claimed). The .dtbo is built by 05-tas-driver.sh.
ensure_line_in_config_txt \
  "dtoverlay=tas58xx-triple,i2creg_mid=0x4c,i2creg_woofer=0x4d,i2creg_ribbon=0x2d"

# i2c-dev for /dev/i2c-1 (i2cdetect / debugging), persistent across reboots.
ensure_module_loaded i2c-dev
# ALSA loopback — go-librespot / snapcast / BT write here, CamillaDSP captures.
ensure_module_loaded snd-aloop

# WICHTIG: ti,fault-monitor bleibt im Multi-DAC-Modus AUS (im Overlay so
# gesetzt) — periodisches I2C-Polling stört den geteilten Bus.

if ! modinfo snd-soc-tas58xx >/dev/null 2>&1; then
  log_warn "snd-soc-tas58xx kernel module not found — run install/05-tas-driver.sh first."
fi

# ─── amixer-init: reines Gain-Staging (KEIN EQ/Crossover — der liegt in CDSP) ─
# Conservative levels; the ribbon (flat TAS5805M) starts low and the real
# protection is the CamillaDSP HP + limiter + the in-line series cap. The
# profile's analog_gain_db is the canonical safe-boot value; this mirrors it
# per chip. Control names use the overlay's sound-name-prefix (Mid/Woofer/
# Ribbon); guarded with || true since exact names can drift between driver
# versions.
AMIXER_PATH=/usr/local/sbin/beatbird-louder-hat-init
cat > "$AMIXER_PATH" <<'AMIXER_EOF'
#!/bin/bash
# beatbird-louder-hat-init — Triple TDM DAC Stack (Lounge), FLAT mode.
# Gain-staging only. All crossover/EQ is in CamillaDSP.

CARD=LouderRaspberry
MAX_TRIES=60
for i in $(seq 1 $MAX_TRIES); do
  amixer -c "$CARD" scontents >/dev/null 2>&1 && break
  sleep 0.5
done
amixer -c "$CARD" scontents >/dev/null 2>&1 || {
  echo "louder-hat-init: $CARD not found after $MAX_TRIES tries" >&2; exit 1; }

echo "louder-hat-init: Triple TDM stack — flat gain staging"

# Digital volume / analog gain per chip. Ribbon conservative (flat amp).
# Mid (0x4c):
amixer -c "$CARD" -q sset 'Mid Digital Volume' 70 2>/dev/null || true
amixer -c "$CARD" -q sset 'Mid Analog Gain'    25 2>/dev/null || true
# Woofer (0x4d, PBTL):
amixer -c "$CARD" -q sset 'Woofer Digital Volume' 70 2>/dev/null || true
amixer -c "$CARD" -q sset 'Woofer Analog Gain'    20 2>/dev/null || true
# Ribbon (0x2d) — start low, it's flat and fragile:
amixer -c "$CARD" -q sset 'Ribbon Digital Volume' 50 2>/dev/null || true
amixer -c "$CARD" -q sset 'Ribbon Analog Gain'    15 2>/dev/null || true

# Make sure internal EQ is OFF on all three (flat). Overlay sets ti,eq-mode=0
# already; this is belt-and-braces if a control exists.
amixer -c "$CARD" -q sset 'Mid Equalizer'    0 2>/dev/null || true
amixer -c "$CARD" -q sset 'Woofer Equalizer' 0 2>/dev/null || true
amixer -c "$CARD" -q sset 'Ribbon Equalizer' 0 2>/dev/null || true

echo "louder-hat-init: done — Mid DV70/AG25, Woofer DV70/AG20, Ribbon DV50/AG15, EQ off"
AMIXER_EOF
chmod 755 "$AMIXER_PATH"
log_ok "wrote $AMIXER_PATH (Triple TDM — flat gain staging)"

# ─── systemd service ─────────────────────────────────────────────────────────
cat > /etc/systemd/system/louder-hat-init.service <<EOF
[Unit]
Description=Louder Hat amplifier level init (Triple TDM — Lounge, flat)
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
