#!/usr/bin/env bash
# install/10-soundcard/louder-hat-plus-1x.sh
# Sonocotta Louder Hat Plus 1X — single TAS5825M stereo @ primary.
#
# Same driver family as Plus 2X, but only one chip (no sub channel).

source "$(dirname "$0")/../_lib.sh"

PRIMARY="$(pq_or soundcard.primary_i2c 0x4c)"

log_step "config.txt overlay"
ensure_line_in_config_txt "dtparam=i2c_arm=on"
ensure_line_in_config_txt "dtparam=i2s=on"
ensure_line_in_config_txt "dtoverlay=tas58xx,i2creg=$PRIMARY"
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

enable_service louder-hat-init.service
