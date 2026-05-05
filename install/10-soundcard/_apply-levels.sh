#!/usr/bin/env bash
# install/10-soundcard/_apply-levels.sh — called by `make amixer-apply`.
#
# Just re-runs the amixer init binary that the per-driver installer wrote
# to /usr/local/sbin/. Useful after a reboot if louder-hat-init.service
# was disabled, or to test new values without a reboot.

source "$(dirname "$0")/../_lib.sh"

INIT_BIN=/usr/local/sbin/beatbird-louder-hat-init
if [[ -x "$INIT_BIN" ]]; then
  "$INIT_BIN"
else
  log_err "$INIT_BIN not found — run 'make install' first"
  exit 1
fi
