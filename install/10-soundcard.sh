#!/usr/bin/env bash
# install/10-soundcard.sh — dispatcher that calls the right driver script.
#
# Each driver lives in install/10-soundcard/<driver>.sh and is responsible for:
#   1. Writing the relevant lines to /boot/firmware/config.txt
#   2. Installing any driver packages (DKMS, kernel overlay, etc.)
#   3. Creating a systemd unit (if needed) that applies amixer levels after
#      the ALSA card appears — waits with a retry loop for Pi Zero 2W timing
#   4. Exposing an `_apply-levels.sh` shim for `make amixer-apply`

source "$(dirname "$0")/_lib.sh"

DRIVER="$(pq soundcard.driver)"
if [[ -z "$DRIVER" ]]; then
  log_err "soundcard.driver not set in profile"
  exit 1
fi

SUB_SCRIPT="$(dirname "$0")/10-soundcard/${DRIVER}.sh"
if [[ ! -f "$SUB_SCRIPT" ]]; then
  log_err "Unknown soundcard driver: $DRIVER"
  log_err "Expected: $SUB_SCRIPT"
  exit 1
fi

log_step "Soundcard: $DRIVER"
bash "$SUB_SCRIPT"

# ─── ALSA system config (dmix on Loopback for SFX + music coexistence) ───
# The dmix lets multiple writers (go-librespot + our UI SFX aplay) share
# the same Loopback playback stream. Without it, librespot held Loopback
# exclusively and SFX during music was silent.
log_step "Installing /etc/asound.conf (beatbird_mix dmix on Loopback)"
install -m 644 "$REPO_DIR/config/alsa/beatbird-asound.conf" /etc/asound.conf
