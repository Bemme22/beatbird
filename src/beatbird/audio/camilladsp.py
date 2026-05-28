"""
beatbird.audio.camilladsp — persistent websocket client for CamillaDSP 4.x.

P1 fix: single long-lived WS connection instead of connect/disconnect per call.
Reconnects automatically on failure.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

log = logging.getLogger("beatbird.dsp")

CDSP_VOL_MIN_DB = -60.0
CDSP_VOL_MAX_DB = 0.0


def pct_to_db(pct: int, min_db: float = CDSP_VOL_MIN_DB,
              max_db: float = CDSP_VOL_MAX_DB, gamma: float = 1.0) -> float:
    """Map 0..100 to dB with an optional audio-taper curve.

    A linear UI percentage mapped linearly to dB feels deeply broken: dB is
    already logarithmic, so a "flat" 0..100% slider crams 30 dB of useful
    range into the top 30% of the slider and leaves the bottom 30% mostly
    inaudible. ``gamma`` reshapes the curve to match how loudness is
    perceived (Stevens' Power Law, exponent ~0.5–0.6):

      * gamma=1.0  → linear (legacy behaviour)
      * gamma=2.0  → Sonos-style taper: lower half of the slider gets the
                    finer resolution, upper half compresses toward max.
                    At 50% UI the perceived loudness is ~50%, not ~6%.

    The formula is ``scaled = (pct/100) ** (1/gamma)``. Same convention as
    image gamma — higher gamma stretches the lower range.
    """
    pct = max(0, min(100, pct))
    p = pct / 100.0
    if gamma != 1.0:
        p = p ** (1.0 / gamma)
    db = min_db + (max_db - min_db) * p
    return round(db, 1)


def db_to_pct(db: float, min_db: float = CDSP_VOL_MIN_DB,
              max_db: float = CDSP_VOL_MAX_DB, gamma: float = 1.0) -> int:
    """Inverse of pct_to_db. Used to convert a DSP-reported dB back to UI %."""
    if db <= min_db:
        return 0
    if db >= max_db:
        return 100
    scaled = (db - min_db) / (max_db - min_db)
    if gamma != 1.0:
        scaled = scaled ** gamma
    return int(round(100.0 * scaled))


class CamillaDSP:
    """Persistent websocket client for CamillaDSP 4.x.

    Keeps a single connection open and reconnects on failure. Thread-safe.
    """

    def __init__(self, host: str = "localhost", port: int = 1234,
                 timeout: float = 2.0, reconnect_delay: float = 1.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.reconnect_delay = reconnect_delay
        self._ws = None
        self._lock = threading.Lock()
        self._last_connect_attempt = 0.0

    def _ensure_connected(self):
        """Open WS if not already connected. Returns ws or None."""
        if self._ws is not None:
            return self._ws
        now = time.monotonic()
        if now - self._last_connect_attempt < self.reconnect_delay:
            return None
        self._last_connect_attempt = now
        try:
            import websocket
            self._ws = websocket.create_connection(
                f"ws://{self.host}:{self.port}", timeout=self.timeout,
            )
            log.info("CamillaDSP WS connected")
            return self._ws
        except Exception as e:
            log.debug("CamillaDSP connect failed: %s", e)
            self._ws = None
            return None

    def _disconnect(self):
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def close(self):
        with self._lock:
            self._disconnect()

    # ─── Low-level ───────────────────────────────────────────────────────────

    def _cmd(self, cmd: Any, timeout: float | None = None) -> Any:
        """Send a command and return the parsed value, or None on error."""
        with self._lock:
            ws = self._ensure_connected()
            if ws is None:
                return None
            try:
                ws.send(json.dumps(cmd))
                ws.settimeout(timeout or self.timeout)
                resp = json.loads(ws.recv())
                if isinstance(resp, dict):
                    for val in resp.values():
                        if isinstance(val, dict) and val.get("result") == "Ok":
                            return val.get("value")
                return resp
            except Exception as e:
                log.debug("CamillaDSP cmd %s failed: %s", cmd, e)
                self._disconnect()
                return None

    # ─── Volume ──────────────────────────────────────────────────────────────

    def get_volume_db(self) -> float | None:
        return self._cmd("GetVolume")

    def set_volume_db(self, db: float) -> None:
        self._cmd({"SetVolume": db})

    # ─── Signal level ────────────────────────────────────────────────────────

    def get_signal_level(self) -> int:
        """Return playback signal level as 0-100 for the firmware energy ring.

        Uses PEAK-since-last-call (capture_peak), not RMS. RMS is a
        sliding-window average that smooths out short transients — kick
        drums, snare hits, punchy basses get lost in the average and the
        ring barely flickered on instrumental music. Peak captures the
        loudest sample within the polling window (default 100 ms) so
        every transient registers. Tail is left to the firmware-side
        low-pass (alpha=0.12 at 60 Hz) so the visual response stays
        musical, not strobe-y.

        Falls back to capture_rms if peak isn't in the response — some
        CDSP versions / configs omit one or the other depending on
        which filter has captured channels."""
        # CamillaDSP 4.x: no-arg commands are sent as bare strings, NOT as
        # {"GetSignalLevels": null} — the dict form was rejected silently and
        # this returned 0 (energy ring froze).
        resp = self._cmd("GetSignalLevels", timeout=0.5)
        if not resp or not isinstance(resp, dict):
            return 0
        try:
            peak_list = resp.get("capture_peak")
            rms_list  = resp.get("capture_rms")
            # Peak preferred; fall back to RMS so we still get something
            # on older CDSP / unusual configs.
            level_db = None
            if peak_list:
                level_db = max(peak_list)
            elif rms_list:
                level_db = max(rms_list)
            if level_db is None:
                return 0
            # Map -60..-6 dB → 0..100. -6 dB is the practical ceiling
            # for clean playback (we already clip volume max at -10 dB
            # in the profile), so leaving a 4 dB safety margin still
            # lets the ring saturate during loud passages.
            return max(0, min(100, int((level_db + 60.0) * 100.0 / 54.0)))
        except Exception:
            return 0

    # ─── Config queries ──────────────────────────────────────────────────────

    def get_config(self) -> dict | None:
        """Return the full running config as parsed YAML dict."""
        raw = self._cmd("GetConfig")
        if raw and isinstance(raw, str):
            import yaml
            return yaml.safe_load(raw)
        return None

    # ─── Live filter patching ────────────────────────────────────────────────

    def patch_filters(self, patch: dict[str, dict]) -> None:
        """Apply a PatchConfig for filter parameter changes."""
        self._cmd({"PatchConfig": {"filters": patch}})
