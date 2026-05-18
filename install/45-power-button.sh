#!/usr/bin/env bash
# install/45-power-button.sh — Power button (GPIO long-press → poweroff).
#
# Hardware: button between GPIO3 (Pin 5) and GND. Internal pull-up keeps the
# pin HIGH; press grounds it. GPIO3 is also the canonical Pi wake-from-halt
# pin — same button shuts down AND turns back on, no overlay needed.
#
# Software side: a Python thread in beatbird-bridge polls the pin and calls
# `/sbin/poweroff` via sudo on a confirmed long-press. This file installs the
# sudoers rule that allows the bridge user to do that without a password.

source "$(dirname "$0")/_lib.sh"

PB_ENABLED="$(pq_bool hardware.power_button.enabled)"

if [[ "$PB_ENABLED" != "true" ]]; then
  log_step "power button disabled in profile, skipping"
  exit 0
fi

log_step "installing sudoers rule for power button"

SUDOERS_FILE=/etc/sudoers.d/beatbird-power
cat > "$SUDOERS_FILE" <<EOF
# Allow the beatbird bridge to power the system off without a password.
# Used by src/beatbird/hardware/power_button.py on confirmed long-press.
$BEATBIRD_USER ALL=(root) NOPASSWD: /sbin/poweroff
EOF
chmod 0440 "$SUDOERS_FILE"

# visudo -c validates the file; refuse to leave broken sudoers around
if ! visudo -cf "$SUDOERS_FILE" >/dev/null; then
  log_err "sudoers file failed validation, removing"
  rm -f "$SUDOERS_FILE"
  exit 1
fi

log_ok "sudoers rule installed at $SUDOERS_FILE"
log_ok "power button ready (GPIO$(pq_or hardware.power_button.gpio 3), long_press=$(pq_or hardware.power_button.long_press_s 2.0)s)"
