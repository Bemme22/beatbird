"""
beatbird.audio.camilladsp — persistent websocket client for CamillaDSP 4.x.

P1 fix: single long-lived WS connection instead of connect/disconnect per call.
Reconnects automatically on failure.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from typing import Any

log = logging.getLogger("beatbird.dsp")

CDSP_VOL_MIN_DB = -60.0
CDSP_VOL_MAX_DB = 0.0


def pct_to_db(pct: int, min_db: float = CDSP_VOL_MIN_DB,
              max_db: float = CDSP_VOL_MAX_DB) -> float:
    """Map 0..100 to dB, linearly over MIN..MAX range."""
    pct = max(0, min(100, pct))
    db_range = max_db - min_db
    db = min_db + db_range * (pct / 100.0)
    return round(db, 1)


def db_to_pct(db: float, min_db: float = CDSP_VOL_MIN_DB,
              max_db: float = CDSP_VOL_MAX_DB) -> int:
    if db <= min_db:
        return 0
    if db >= max_db:
        return 100
    db_range = max_db - min_db
    return int(round(100.0 * (db - min_db) / db_range))


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
        """Return playback signal RMS as 0-100."""
        # CamillaDSP 4.x: no-arg commands are sent as bare strings, NOT as
        # {"GetSignalLevels": null} — the dict form was rejected silently and
        # this returned 0 (energy ring froze).
        resp = self._cmd("GetSignalLevels", timeout=0.5)
        if not resp:
            return 0
        try:
            rms_list = resp.get("capture_rms") if isinstance(resp, dict) else None
            if not rms_list:
                return 0
            rms_db = max(rms_list)
            return max(0, min(100, int((rms_db + 60.0) * 100.0 / 54.0)))
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
