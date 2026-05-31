#!/usr/bin/env bash
# install/16-pi-power.sh — small, safe headless power/RAM tweaks.
#
# These are deliberately conservative — the big idle-power lever is the
# amp (see the bridge deep-idle work), NOT the SoC. What's here is the
# "costs nothing, take it" tier for a headless 24/7 speaker:
#
#   1. Onboard LEDs off. The green ACT LED blinks on every SD/I²C access
#      and the PWR LED sits solid — nobody sees them inside a speaker
#      case. Turning them off saves a few mA and stops the constant blink
#      (also marginally less EMI near the I²S lines). Real, tiny.
#   2. gpu_mem=16. On a 512 MB Pi Zero 2W the default 64 MB GPU split is
#      pure waste — the display is an external ESP32 over USB-CDC, vc4/KMS
#      is already disabled (disable_fw_kms_setup=1, no vc4 module), so the
#      Pi never renders anything. Dropping to the 16 MB minimum hands ~48 MB
#      back to the RAM-tight stack. This is a HEADROOM win, not a watt win.
#   3. camera_auto_detect=0. No camera — skip the boot-time probe.
#
# Deliberately NOT done (verified no-ops / risky on this platform):
#   - HDMI/vc4 disable — already off (disable_fw_kms_setup=1, vc4 not loaded).
#   - CPU governor — already `ondemand` (scales 600↔1000 MHz). Leave it.
#   - ARM underclock — the DSP needs the headroom; not worth the risk.
#
# Idempotent + persistent in /boot/firmware/config.txt. config.txt changes
# need a reboot; the LED change is also applied live so it takes immediately.

source "$(dirname "$0")/_lib.sh"

log_step "Onboard LEDs off (config.txt + live)"
# Persistent: dtparam knobs are honoured by the Pi firmware for the standard
# ACT + PWR LEDs across Pi Zero 2W .. Pi 5 (harmless where a given LED is absent).
ensure_line_in_config_txt "dtparam=act_led_trigger=none"
ensure_line_in_config_txt "dtparam=act_led_activelow=off"
ensure_line_in_config_txt "dtparam=pwr_led_trigger=none"
ensure_line_in_config_txt "dtparam=pwr_led_activelow=off"
# Live: don't wait for a reboot to stop the blink. The kernel exposes the
# LEDs by label (ACT, default-on/PWR); set trigger=none + brightness 0.
for led in /sys/class/leds/*/; do
  name="$(basename "$led")"
  case "$name" in
    ACT|led0|default-on|PWR|led1|pwr)
      echo none > "$led/trigger" 2>/dev/null || true
      echo 0    > "$led/brightness" 2>/dev/null || true
      ;;
  esac
done
log_ok "onboard LEDs disabled"

log_step "gpu_mem=16 (free RAM — display is external, no GPU use)"
ensure_line_in_config_txt "gpu_mem=16"
log_ok "gpu_mem=16 set (active after reboot; frees ~48 MB on a 64 MB split)"

log_step "camera_auto_detect off (no camera)"
CONFIG_TXT=/boot/firmware/config.txt
[[ -f "$CONFIG_TXT" ]] || CONFIG_TXT=/boot/config.txt
if [[ -f "$CONFIG_TXT" ]]; then
  if grep -q "^camera_auto_detect=1" "$CONFIG_TXT"; then
    sed -i 's/^camera_auto_detect=1/camera_auto_detect=0/' "$CONFIG_TXT"
    log_ok "camera_auto_detect 1 → 0"
  else
    ensure_line_in_config_txt "camera_auto_detect=0"
    log_ok "camera_auto_detect=0 ensured"
  fi
fi

log_ok "Pi power/RAM tweaks applied (config.txt changes need a reboot)"
