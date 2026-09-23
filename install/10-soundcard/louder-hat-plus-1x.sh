#!/usr/bin/env bash
# install/10-soundcard/louder-hat-plus-1x.sh
# Sonocotta Louder Hat Plus 1X — single TAS5825M @ primary.
#
# Same driver family as Plus 2X, but only one chip (no sub channel).
# Two output modes, selected by `soundcard.pbtl` in the profile:
#   pbtl:false (default) → stereo BTL, 2 channels on the screw terminal
#   pbtl:true            → PBTL bridge mono (OUT_A||OUT_B) for ONE high-power
#                          driver (RobinPi). Requires closing the SJ5+SJ6
#                          solder bridges on the board's back side too — the
#                          overlay only sets the chip's modulation; the parallel
#                          wiring is physical. bridge_mode=1 + mixer_mode=1 make
#                          the chip sum L+R to mono in-chip, matching the
#                          Sonocotta sub reference (tas58xx-lanes-overlay.dts).

source "$(dirname "$0")/../_lib.sh"

PRIMARY="$(pq_or soundcard.primary_i2c 0x4c)"
PBTL="$(pq_bool soundcard.pbtl)"

if [[ "$PBTL" == true ]]; then
  OVERLAY="dtoverlay=tas58xx,i2creg=$PRIMARY,bridge_mode=1,mixer_mode=1"
  log_step "config.txt overlay (PBTL bridge mono @ $PRIMARY)"
  log_warn "PBTL selected — confirm the SJ5+SJ6 solder bridges are CLOSED on the board."
else
  OVERLAY="dtoverlay=tas58xx,i2creg=$PRIMARY"
  log_step "config.txt overlay (stereo @ $PRIMARY)"
fi

ensure_line_in_config_txt "dtparam=i2c_arm=on"
ensure_line_in_config_txt "dtparam=i2s=on"
ensure_line_in_config_txt "$OVERLAY"
ensure_module_loaded snd-aloop

if ! modinfo snd-soc-tas58xx >/dev/null 2>&1; then
  log_warn "snd-soc-tas58xx kernel module not found — install per Sonocotta docs."
fi

# Pegel aus dem Profil in das Boot-Skript einsetzen. Das Skript läuft als
# systemd-oneshot und kann das Profil dort nicht lesen — hartkodierte Werte
# hätten aber wieder beschrieben, was NICHT eingestellt ist (s. Kopf des
# Skripts). Analog Gain: Control 0…31, −15,5 dB … 0 dB in 0,5-dB-Schritten
# ⇒ Wert = 31 + 2·dB. Digital 103 = Register 0x30 = 0 dB.
ANALOG_GAIN_DB="$(pq_or soundcard.analog_gain_db -3)"
ANALOG_GAIN_VAL="$(awk -v d="$ANALOG_GAIN_DB" 'BEGIN{v=int(31+2*d+0.5); if(v<0)v=0; if(v>31)v=31; print v}')"
DIGITAL_VAL=103

AMIXER_PATH=/usr/local/sbin/beatbird-louder-hat-init
sed -e "s/@ANALOG_GAIN@/$ANALOG_GAIN_VAL/g" -e "s/@DIGITAL@/$DIGITAL_VAL/g" \
    "$(dirname "$0")/_amixer-init-plus-1x.sh" > "$AMIXER_PATH"
chmod 755 "$AMIXER_PATH"
log_ok "wrote $AMIXER_PATH (Analog Gain $ANALOG_GAIN_DB dB = $ANALOG_GAIN_VAL, Digital $DIGITAL_VAL)"

cat > /etc/systemd/system/louder-hat-init.service <<EOF
[Unit]
Description=Louder Hat amplifier level init (Plus 1X)
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
