"""
beatbird.display.led_button — GPIO WS2812 ring + single button (LT300, Lounge).

Design sketch (to be filled in at teardown time):

  - N-LED ring (configurable) driven by ``rpi_ws281x`` on the PWM pin.
    Visualises:
      * Volume as an arc that fills clockwise (0..N LEDs lit)
      * Spectrum as equalizer columns (each LED colour-mapped by band energy)
      * Status: one solid colour per source (Spotify=green, BT=blue, TOSLINK=
        amber, Snapcast=cyan, standby=dim white)
      * Pairing/boot: pulsing white

  - Single button with:
      * short press → play/pause
      * double press → next track
      * long press (1.5s) → BT pairing mode

The class keeps the same interface as AmoledDisplay so the bridge can be
switched between them by profile alone.
"""

from __future__ import annotations

import logging
import threading
import time

from beatbird.display.base import (
    CommandCallback,
    DisplayInterface,
    DisplayState,
    DisplaySystemStatus,
    VolumeCallback,
)

log = logging.getLogger("beatbird.display.led")


class LedButtonDisplay(DisplayInterface):
    def __init__(
        self,
        led_pin: int = 18,
        led_count: int = 12,
        button_pin: int = 17,
        brightness: int = 128,
        spectrum_bands: int = 12,
    ):
        self.led_pin = led_pin
        self.led_count = led_count
        self.button_pin = button_pin
        self.brightness = brightness
        self.spectrum_bands = spectrum_bands

        self.on_command: CommandCallback | None = None
        self.on_volume: VolumeCallback | None = None

        self._strip = None
        self._gpio = None
        self._button_thread: threading.Thread | None = None
        self._running = False
        self._available = False

    # ─── Lifecycle ──────────────────────────────────────────────────────────

    def setup(
        self,
        on_command: CommandCallback | None = None,
        on_volume: VolumeCallback | None = None,
    ) -> None:
        self.on_command = on_command
        self.on_volume = on_volume
        try:
            from rpi_ws281x import PixelStrip, Color  # noqa: F401
            import RPi.GPIO as GPIO
        except ImportError:
            log.warning("rpi_ws281x / RPi.GPIO not available — LED display disabled")
            return

        self._strip = PixelStrip(
            self.led_count, self.led_pin, 800_000, 10, False, self.brightness,
        )
        self._strip.begin()
        self._gpio = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.button_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        self._available = True
        self._running = True
        self._button_thread = threading.Thread(
            target=self._button_loop, daemon=True, name="btn",
        )
        self._button_thread.start()
        log.info("LED ring (%d LEDs, GPIO%d) + button (GPIO%d) ready",
                 self.led_count, self.led_pin, self.button_pin)

    def close(self) -> None:
        self._running = False
        if self._strip is not None:
            for i in range(self.led_count):
                self._strip.setPixelColor(i, 0)
            self._strip.show()
        if self._gpio is not None:
            self._gpio.cleanup(self.button_pin)

    # ─── Rendering ──────────────────────────────────────────────────────────
    # TODO: flesh out with actual animations once LT300 / Lounge are on the
    # bench. The stubs below make the bridge happy and cause no harm.

    def push_state(self, state: DisplayState) -> None:
        if not self._available:
            return
        # Minimal: volume arc + source-coloured tail
        self._paint_volume_arc(state.volume, self._source_colour(state.source))

    def push_system(self, status: DisplaySystemStatus) -> None:
        if not self._available:
            return
        # Non-visual right now; could flash red on amp fault later.

    def poll(self) -> None:
        # Button handling lives in its own thread; nothing to do here.
        pass

    # ─── Internal helpers ───────────────────────────────────────────────────

    def _source_colour(self, source: str) -> tuple[int, int, int]:
        return {
            "spotify":   (0, 255,   0),
            "bluetooth": (0,   0, 255),
            "toslink":   (255, 140, 0),
            "snapcast":  (0, 200, 200),
            "none":      (30,  30, 30),
        }.get(source, (30, 30, 30))

    def _paint_volume_arc(self, volume_pct: int, rgb: tuple[int, int, int]) -> None:
        from rpi_ws281x import Color
        lit = int(round(volume_pct / 100.0 * self.led_count))
        r, g, b = rgb
        for i in range(self.led_count):
            self._strip.setPixelColor(i, Color(r, g, b) if i < lit else 0)
        self._strip.show()

    def _button_loop(self) -> None:
        """Detect short/double/long presses on a single GPIO button."""
        GPIO = self._gpio
        PRESS = 0   # active low (pull-up)

        last_release = 0.0
        while self._running:
            # Wait for press
            while self._running and GPIO.input(self.button_pin) != PRESS:
                time.sleep(0.01)
            if not self._running:
                return
            press_t = time.monotonic()

            # Wait for release (with long-press cap at 1.5s)
            while self._running and GPIO.input(self.button_pin) == PRESS:
                if time.monotonic() - press_t > 1.5:
                    self._emit_command("BT_PAIR")
                    # Wait for actual release to avoid repeats
                    while GPIO.input(self.button_pin) == PRESS:
                        time.sleep(0.02)
                    break
                time.sleep(0.02)
            else:
                if not self._running:
                    return
                # Short press — look for double-click within 300ms
                release_t = time.monotonic()
                if release_t - last_release < 0.3:
                    self._emit_command("NEXT")
                    last_release = 0.0
                else:
                    time.sleep(0.3)
                    if GPIO.input(self.button_pin) != PRESS:
                        self._emit_command("PLAYPAUSE")
                    last_release = release_t

    def _emit_command(self, cmd: str) -> None:
        log.debug("button: %s", cmd)
        if self.on_command:
            self.on_command(cmd)
