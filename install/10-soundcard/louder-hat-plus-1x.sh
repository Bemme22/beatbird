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

AMIXER_PATH=/usr/local/sbin/beatbird-louder-hat-init
install -m 755 "$(dirname "$0")/_amixer-init-plus-1x.sh" "$AMIXER_PATH"
log_ok "wrote $AMIXER_PATH"

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
