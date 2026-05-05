#!/usr/bin/env bash
# install/10-soundcard/innomaker-amp-pro.sh
# Innomaker AMP Pro Mini Hat — MA12070P (Infineon/Merus) Class-D amplifier.
#
# The MA12070P is a Class-D amp with I2S input. On Raspberry Pi OS Bookworm,
# it works with the generic hifiberry-dac overlay which sets up I2S. The chip
# handles its own power-on sequencing and does not need an amixer init service.
#
# ALSA card name: "sndrpihifiberry" (from the overlay).
# If InnoMaker provides a dedicated overlay in the future, update here.

source "$(dirname "$0")/../_lib.sh"

log_step "config.txt overlay (Innomaker AMP Pro Mini / MA12070P)"
ensure_line_in_config_txt "dtparam=i2c_arm=on"
ensure_line_in_config_txt "dtparam=i2s=on"

# The MA12070P needs only an I2S clock — the generic hifiberry-dac overlay
# configures that. If InnoMaker's own overlay becomes available in apt,
# switch to it here.
ensure_line_in_config_txt "dtoverlay=hifiberry-dac"

# ALSA Loopback for source multiplexing (same as all other profiles)
ensure_line_in_config_txt "dtoverlay=snd-aloop"

# USB autosuspend disabled — critical on Pi Zero 2W with USB WiFi dongle.
# Without this, the WiFi adapter drops off after ~2s idle.
ensure_line_in_config_txt "dtoverlay=dwc2"
CMDLINE="/boot/firmware/cmdline.txt"
[[ -f "$CMDLINE" ]] || CMDLINE="/boot/cmdline.txt"
if ! grep -q "usbcore.autosuspend=-1" "$CMDLINE" 2>/dev/null; then
  sed -i 's/$/ usbcore.autosuspend=-1/' "$CMDLINE"
  log_ok "USB autosuspend disabled in cmdline.txt"
fi

# No amixer init needed — MA12070P handles gain internally via I2C defaults.
# If you need to adjust the chip's gain later, use:
#   i2cset -y 1 0x20 <reg> <val>
# (0x20 is the MA12070P's default I2C address)

log_ok "Innomaker AMP Pro Mini configured (overlay=hifiberry-dac, loopback=snd-aloop)"
log_warn "After reboot, verify with: aplay -l | grep hifiberry"
log_warn "If the card doesn't appear, check InnoMaker's GitHub for a board-specific overlay."
