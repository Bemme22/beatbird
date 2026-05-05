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
ensure_line_in_config_txt "dtoverlay=snd-aloop"

if ! modinfo snd-soc-tas5825m >/dev/null 2>&1; then
  log_warn "snd-soc-tas5825m kernel module not found — install per Sonocotta docs."
fi

AMIXER_PATH=/usr/local/sbin/beatbird-louder-hat-init
cat > "$AMIXER_PATH" <<'AMIXER_EOF'
#!/bin/bash
# beatbird-louder-hat-init — retry-on-boot amixer settings for Louder Hat Plus 1X
CARD=LouderRaspberry
MAX_TRIES=60

for i in $(seq 1 $MAX_TRIES); do
  amixer -c "$CARD" scontents >/dev/null 2>&1 && break
  sleep 0.5
done
amixer -c "$CARD" scontents >/dev/null 2>&1 || {
  echo "louder-hat-init: $CARD not found" >&2; exit 1; }

amixer -c "$CARD" cset numid=1 103   # Digital vol
amixer -c "$CARD" cset numid=2 25    # Analog gain -3 dB
amixer -c "$CARD" cset numid=4 0
amixer -c "$CARD" cset numid=5 0
amixer -c "$CARD" cset numid=3 1     # EQ OFF

echo "louder-hat-init: $CARD configured (stereo only)"
AMIXER_EOF
chmod 755 "$AMIXER_PATH"

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
