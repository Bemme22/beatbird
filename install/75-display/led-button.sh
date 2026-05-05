#!/usr/bin/env bash
# install/75-display/led-button.sh — GPIO-driven LED ring + single button.
#
# For LT300 and Lounge: reuses the original Libratone button + LED-ring
# interface. Implementation lives in src/beatbird/display/led_button.py
# (stub for now — will grow once LT300 enclosure is open).

source "$(dirname "$0")/../_lib.sh"

usermod -a -G gpio "$BEATBIRD_USER" || true

# WS2812/NeoPixel on GPIO18 needs PWM; disable audio on PWM if it conflicts.
# (BeatBird routes all audio through I²S to the TAS5825M, so PWM audio is free.)
LED_PIN="$(pq_or display.led_pin 18)"
log_step "LED strip on GPIO$LED_PIN"

# python rpi-ws281x is easier than writing kernel code.
ensure_pkg python3-rpi.gpio
"$VENV/bin/pip" install --quiet rpi_ws281x 2>/dev/null || \
  /opt/beatbird/venv/bin/pip install --quiet rpi_ws281x 2>/dev/null || \
  log_warn "Install rpi_ws281x into venv manually if LED ring fails"

log_ok "LED + button display prepared (stub — implementation in src/beatbird/display/led_button.py)"
