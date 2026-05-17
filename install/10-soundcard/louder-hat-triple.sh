#!/usr/bin/env bash
# install/10-soundcard/louder-hat-triple.sh
# Lounge: 1× Louder Hat Plus 2X (2× TAS5825M) + 1× Louder Hat 1X non-Plus (1× TAS5805M)
#
# Stack: Pi 4 → Plus 2X → non-Plus 1X → Lochrasterplatine
# DT Overlay: Sonocotta tas58xx-triple (bereits im Treiber enthalten)
#
# Kanal-Mapping (verifiziert 09.05.2026):
#   0x4C (TAS5825M, Stereo)  → Mid L / Mid R
#   0x4D (TAS5825M, PBTL)    → Woofer 8"
#   0x2E (TAS5805M, Stereo)  → Ribbon L / Ribbon R
#
# Non-Plus hat Default-Adresse 0x2D, wird auf 0x2E überschrieben.

source "$(dirname "$0")/../_lib.sh"

log_step "config.txt overlay (Triple DAC Stack)"
ensure_line_in_config_txt "dtparam=i2c_arm=on"
ensure_line_in_config_txt "dtparam=i2s=on"

# Sonocotta tas58xx-triple Overlay — Primary-Adresse auf 0x2E überschreiben
# weil der non-Plus auf 0x2E statt Default 0x2D konfiguriert ist.
ensure_line_in_config_txt "dtoverlay=tas58xx-triple,i2creg_primary=0x2e"

# WICHTIG: ti,fault-monitor NICHT aktivieren im Multi-DAC-Modus —
# periodisches I2C-Polling stört den geteilten I2S-Bus
# (GLOBAL1=0x04 Clock-Faults und Sub-Dropouts)

ensure_module_loaded snd-aloop

if ! modinfo snd-soc-tas58xx >/dev/null 2>&1; then
  log_warn "snd-soc-tas58xx kernel module not found — install per Sonocotta docs."
fi

# ─── amixer-init: Gain-Staging + Crossover ───────────────────────────────────
AMIXER_PATH=/usr/local/sbin/beatbird-louder-hat-init
cat > "$AMIXER_PATH" <<'AMIXER_EOF'
#!/bin/bash
# beatbird-louder-hat-init — Triple DAC Stack (Lounge)
# Setzt Gain-Staging und EQ-Crossover für alle drei TAS-Chips.
#
# Kanal-Mapping:
#   Mid    (0x4C): Stereo, 15-Band-EQ als Bandpass ~200–3150 Hz
#   Woofer (0x4D): PBTL, interner Crossover LP @ 150 Hz
#   Ribbon (0x2E): Stereo, 15-Band-EQ als Highpass ~3150 Hz

CARD=LouderRaspberry
MAX_TRIES=60

for i in $(seq 1 $MAX_TRIES); do
  amixer -c "$CARD" scontents >/dev/null 2>&1 && break
  sleep 0.5
done
amixer -c "$CARD" scontents >/dev/null 2>&1 || {
  echo "louder-hat-init: $CARD not found after $MAX_TRIES tries" >&2; exit 1; }

echo "louder-hat-init: Konfiguriere Triple DAC Stack..."

# ─── Gain-Staging ────────────────────────────────────────────────────────────
# Ribbon (0x2E, TAS5805M): Digital Volume 50, Analog Gain 20
# ACHTUNG: TAS5805M Overcurrent ab ~60% Digital Volume bei Fullrange!
amixer -c "$CARD" -q sset 'Ribbon Digital Volume' 50 2>/dev/null || true
amixer -c "$CARD" -q sset 'Ribbon Analog Gain' 20 2>/dev/null || true

# Mid (0x4C, TAS5825M): Digital Volume 70, Analog Gain 25 (= -3 dB)
amixer -c "$CARD" -q sset 'Mid Digital Volume' 70 2>/dev/null || true
amixer -c "$CARD" -q sset 'Mid Analog Gain' 25 2>/dev/null || true

# Woofer (0x4D, TAS5825M, PBTL): Digital Volume 70, Analog Gain 20
amixer -c "$CARD" -q sset 'Woofer Digital Volume' 70 2>/dev/null || true
amixer -c "$CARD" -q sset 'Woofer Analog Gain' 20 2>/dev/null || true

# ─── EQ aktivieren ───────────────────────────────────────────────────────────
amixer -c "$CARD" -q sset 'Mid Equalizer' 1 2>/dev/null || true
amixer -c "$CARD" -q sset 'Ribbon Equalizer' 1 2>/dev/null || true
amixer -c "$CARD" -q sset 'Woofer Equalizer' 1 2>/dev/null || true

# ─── Woofer: interner LP @ 150 Hz ───────────────────────────────────────────
amixer -c "$CARD" -q sset 'Woofer Crossover Frequency' 150 2>/dev/null || true

# ─── Mid: Bandpass ~200 Hz – 3150 Hz (15-Band-EQ) ───────────────────────────
# Werte: 0 = -15dB, 3 = -12dB, 9 = -6dB, 15 = 0dB
set_band() { amixer -c "$CARD" -q sset "$1 $2 Hz" "$3" 2>/dev/null || true; }

set_band "Mid" "00020"  0    # -15 dB
set_band "Mid" "00032"  0    # -15 dB
set_band "Mid" "00050"  0    # -15 dB
set_band "Mid" "00080"  3    # -12 dB
set_band "Mid" "00125"  9    #  -6 dB
set_band "Mid" "00200" 15    #   0 dB  ← Passband
set_band "Mid" "00315" 15    #   0 dB
set_band "Mid" "00500" 15    #   0 dB
set_band "Mid" "00800" 15    #   0 dB
set_band "Mid" "01250" 15    #   0 dB
set_band "Mid" "02000" 15    #   0 dB
set_band "Mid" "03150" 15    #   0 dB  ← Passband Ende
set_band "Mid" "05000"  9    #  -6 dB
set_band "Mid" "08000"  3    # -12 dB
set_band "Mid" "16000"  0    # -15 dB

# ─── Ribbon: Highpass ~3150 Hz (15-Band-EQ) ──────────────────────────────────
set_band "Ribbon" "00020"  0    # -15 dB
set_band "Ribbon" "00032"  0    # -15 dB
set_band "Ribbon" "00050"  0    # -15 dB
set_band "Ribbon" "00080"  0    # -15 dB
set_band "Ribbon" "00125"  0    # -15 dB
set_band "Ribbon" "00200"  0    # -15 dB
set_band "Ribbon" "00315"  0    # -15 dB
set_band "Ribbon" "00500"  0    # -15 dB
set_band "Ribbon" "00800"  0    # -15 dB
set_band "Ribbon" "01250"  0    # -15 dB
set_band "Ribbon" "02000"  3    # -12 dB
set_band "Ribbon" "03150"  9    #  -6 dB
set_band "Ribbon" "05000" 15    #   0 dB  ← Passband
set_band "Ribbon" "08000" 15    #   0 dB
set_band "Ribbon" "16000" 15    #   0 dB

echo "louder-hat-init: Triple DAC Stack konfiguriert"
echo "  Woofer: LP @ 150 Hz (intern), DV=70, AG=20"
echo "  Mid:    BP ~200–3150 Hz (EQ), DV=70, AG=25"
echo "  Ribbon: HP ~3150 Hz (EQ), DV=50, AG=20"
AMIXER_EOF
chmod 755 "$AMIXER_PATH"
log_ok "wrote $AMIXER_PATH (Triple DAC Crossover + Gain-Staging)"

# ─── systemd service ─────────────────────────────────────────────────────────
cat > /etc/systemd/system/louder-hat-init.service <<EOF
[Unit]
Description=Louder Hat amplifier level init (Triple DAC — Lounge)
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
