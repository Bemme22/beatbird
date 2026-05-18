"""
beatbird.hardware.power_button — long-press shutdown button.

Watches a single GPIO pin (default GPIO3) in a background thread. Press → low,
release → high (internal pull-up). Behaviour:

  - press starts a hold timer; after ``warn_after_s`` (150 ms) the bridge gets
    ``on_warn`` so it can show "halt to power off" feedback on the display
  - if released before ``long_press_s``, ``on_cancel`` is fired and the
    display returns to normal
  - if held past ``long_press_s``, ``on_confirm`` fires and the thread exits
    (the system is going down — no point continuing to poll)

GPIO3 is the canonical Pi power-button pin: the BCM283x hardware wakes the
SoC from halt state when GPIO3 is pulled low, so the same button shuts the
Pi down AND turns it back on.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger("beatbird.power_button")


class PowerButton:
    def __init__(
        self,
        gpio: int = 3,
        long_press_s: float = 2.0,
        warn_after_s: float = 0.15,
        on_warn: Callable[[], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
        on_confirm: Callable[[], None] | None = None,
    ):
        self.gpio = gpio
        self.long_press_s = long_press_s
        self.warn_after_s = warn_after_s
        self.on_warn = on_warn
        self.on_cancel = on_cancel
        self.on_confirm = on_confirm

        self._running = False
        self._thread: threading.Thread | None = None
        self._gpio = None

    def start(self) -> None:
        # lgpio (which RPi.GPIO wraps on Trixie+) creates its ".lgd-nfy*"
        # notification pipe in the process's CWD at import time. The bridge's
        # systemd unit has ProtectSystem=strict + ProtectHome=read-only,
        # which makes the whole filesystem read-only EXCEPT what's listed in
        # ReadWritePaths (/var/lib/beatbird and /etc/beatbird). /tmp is NOT
        # writable from this service. Chdir to /var/lib/beatbird before the
        # import, then restore.
        import os
        _cwd_save = os.getcwd()
        try:
            os.chdir("/var/lib/beatbird")
            import RPi.GPIO as GPIO
        except Exception as e:
            # Never let a power-button issue take down the bridge.
            log.warning("power button disabled: %s (%s)", type(e).__name__, e)
            return
        finally:
            try:
                os.chdir(_cwd_save)
            except Exception:
                pass

        try:
            GPIO.setmode(GPIO.BCM)
            # Button pulls GPIO to GND on press; internal pull-up keeps it HIGH idle.
            GPIO.setup(self.gpio, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        except Exception as e:
            log.warning("power button GPIO setup failed: %s (%s)", type(e).__name__, e)
            return
        self._gpio = GPIO

        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="power-button"
        )
        self._thread.start()
        log.info(
            "power button ready on GPIO%d (long_press=%.1fs)",
            self.gpio, self.long_press_s,
        )

    def stop(self) -> None:
        self._running = False
        if self._gpio is not None:
            try:
                self._gpio.cleanup(self.gpio)
            except Exception:
                pass

    def _loop(self) -> None:
        GPIO = self._gpio
        poll_s = 0.05  # 50 ms poll — quick enough for human-perceived response

        while self._running:
            if GPIO.input(self.gpio) == GPIO.HIGH:
                time.sleep(poll_s)
                continue

            press_started = time.monotonic()
            warned = False

            while self._running and GPIO.input(self.gpio) == GPIO.LOW:
                held_for = time.monotonic() - press_started

                if not warned and held_for >= self.warn_after_s:
                    warned = True
                    self._safe_call(self.on_warn, "on_warn")

                if held_for >= self.long_press_s:
                    log.info("power button long-press confirmed → shutdown")
                    self._safe_call(self.on_confirm, "on_confirm")
                    return  # system is shutting down

                time.sleep(poll_s)

            if warned and self._running:
                held_for = time.monotonic() - press_started
                log.info(
                    "power button released after %.2fs, cancelling shutdown",
                    held_for,
                )
                self._safe_call(self.on_cancel, "on_cancel")

    @staticmethod
    def _safe_call(cb: Callable[[], None] | None, name: str) -> None:
        if cb is None:
            return
        try:
            cb()
        except Exception as e:
            log.error("power_button %s: %s", name, e)
