"""
beatbird.audio.spectrum — background FFT thread that reads from the ALSA
loopback device and produces an N-band spectrum array.

Output is an integer list (0..100) of length ``bands``, updated every
``interval`` seconds. Designed to be cheap to poll — the consumer just calls
``get_bands()`` whenever it wants to push to the display.

numpy/sounddevice are optional imports; if they're not present, the class
silently reports a flat spectrum and no spectrum frames are sent to the
display. This keeps the bridge running on Pi Zeros where numpy may be too
heavy.
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("beatbird.fft")


class SpectrumAnalyzer:
    """Background ALSA capture + FFT → N-band spectrum."""

    def __init__(
        self,
        device: str = "hw:Loopback,1,0",
        rate: int = 48000,
        bands: int = 16,
        size: int = 2048,
        freq_lo: float = 40.0,
        freq_hi: float = 16000.0,
        db_min: float = -65.0,
        db_max: float = -5.0,
        interval: float = 0.15,
    ):
        self.device = device
        self.rate = rate
        self.bands_count = bands
        self.size = size
        self.freq_lo = freq_lo
        self.freq_hi = freq_hi
        self.db_min = db_min
        self.db_max = db_max
        self.interval = interval

        self.bands: list[int] = [0] * bands
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False

        try:
            import numpy as np
            import sounddevice as sd  # noqa: F401
            self._np = np
            self._available = True
        except ImportError:
            self._np = None
            self._available = False
            log.warning("numpy/sounddevice not installed — spectrum disabled")
            return

        self._edges = np.logspace(
            np.log10(freq_lo), np.log10(freq_hi), bands + 1
        )
        self._freqs = np.fft.rfftfreq(size, 1.0 / rate)
        self._window = np.hanning(size).astype(np.float32)
        self._masks = [
            (self._freqs >= self._edges[i]) & (self._freqs < self._edges[i + 1])
            for i in range(bands)
        ]

    # ─── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self._available or self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="fft")
        self._thread.start()
        log.info("spectrum started: %d bands, %.0f–%.0f Hz",
                 self.bands_count, self.freq_lo, self.freq_hi)

    def stop(self) -> None:
        self._running = False

    def get_bands(self) -> list[int]:
        with self._lock:
            return list(self.bands)

    # ─── Worker ─────────────────────────────────────────────────────────────

    def _run(self) -> None:
        fail = 0
        while self._running:
            t0 = time.monotonic()
            try:
                result = self._compute()
                if result is not None:
                    with self._lock:
                        self.bands = result
                    fail = 0
                else:
                    fail += 1
            except Exception as e:
                fail += 1
                log.debug("fft error: %s", e)

            if fail > 10:
                log.warning("spectrum: %s unreachable, pausing 10s", self.device)
                time.sleep(10.0)
                fail = 0
                continue

            elapsed = time.monotonic() - t0
            time.sleep(max(0.0, self.interval - elapsed))

    def _compute(self) -> list[int] | None:
        import sounddevice as sd
        np = self._np
        try:
            audio = sd.rec(
                self.size, samplerate=self.rate, channels=1,
                device=self.device, dtype="float32", blocking=True,
            )
        except sd.PortAudioError as e:
            log.debug("portaudio: %s", e)
            return None

        samples = audio[:, 0] * self._window
        mag = np.abs(np.fft.rfft(samples))

        energies = []
        for mask in self._masks:
            energies.append(float(np.mean(mag[mask])) if mask.any() else 1e-9)

        db = 20.0 * np.log10(np.maximum(energies, 1e-9))
        normalized = np.clip(
            (db - self.db_min) / (self.db_max - self.db_min) * 100.0,
            0.0, 100.0,
        )
        return [int(v) for v in normalized]
