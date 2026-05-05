#!/usr/bin/env bash
# install/75-display/amoled.sh — Waveshare ESP32-S3 AMOLED 1.43 over USB serial.
#
# Nothing to install on the Pi side other than:
#   - ensuring the bridge user is in dialout
#   - optional udev rule for a stable /dev/beatbird-display symlink

source "$(dirname "$0")/../_lib.sh"

usermod -a -G dialout "$BEATBIRD_USER" || true

# udev symlink — ESP32-S3 USB CDC reports VID 0x303A.
cat > /etc/udev/rules.d/99-beatbird-display.rules <<'EOF'
# BeatBird AMOLED display — Waveshare ESP32-S3 (VID 0x303A)
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", SYMLINK+="beatbird-display", MODE="0660", GROUP="dialout"
EOF
udevadm control --reload-rules || true
udevadm trigger --subsystem-match=tty || true

log_ok "AMOLED display udev rules installed (/dev/beatbird-display symlink)"
log_warn "Flash firmware with: cd firmware/amoled-1.43 && pio run -t upload"
