"""
beatbird.sources.spotify — go-librespot HTTP client.

P1 fix: uses POST /player/close to end sessions gracefully instead of
restarting the entire service. Falls back to service restart only if
close fails.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("beatbird.spotify")

API_BASE = "http://localhost:3678"


@dataclass
class SpotifyState:
    stopped: bool = True
    paused: bool = False
    title: str = ""
    artist: str = ""
    album: str = ""
    track_uri: str = ""
    position_ms: int = 0
    duration_ms: int = 1
    volume: int = 0
    volume_steps: int = 65535
    album_cover_url: str = ""    # go-librespot puts it under track.album_cover_url


class SpotifyClient:
    def __init__(self, base: str = API_BASE, timeout: float = 2.0):
        self.base = base
        self.timeout = timeout
        self._requests = None

    def _req(self):
        if self._requests is None:
            try:
                import requests
                self._requests = requests
            except ImportError:
                log.error("requests not installed")
        return self._requests

    def _call(self, method: str, endpoint: str, **kwargs) -> Optional[dict]:
        """Call the go-librespot HTTP API. Consistent contract:

          * ``None``  — the call did not succeed: no requests lib, a network
            error, or a non-2xx status. Callers test ``is None`` for "failed".
          * ``dict``  — a 2xx response. The parsed JSON body, or an EMPTY dict
            ``{}`` for a no-content success (204, or a 200 with no/invalid
            body). Callers that need *data* must treat a falsy dict as
            "succeeded but nothing to read" (see get_state) — only the
            fire-and-forget POSTs and close_session rely on the
            success-vs-None distinction alone.
        """
        req = self._req()
        if not req:
            return None
        url = f"{self.base}{endpoint}"
        try:
            if method == "GET":
                r = req.get(url, timeout=self.timeout)
            else:
                r = req.post(url, timeout=self.timeout, **kwargs)
        except Exception as e:
            log.debug("HTTP %s %s: %s", method, endpoint, e)
            return None
        if r.status_code == 200:
            try:
                return r.json()
            except Exception:
                return {}
        if r.status_code == 204:
            return {}
        log.debug("HTTP %s %s → %d", method, endpoint, r.status_code)
        return None

    # ─── State ──────────────────────────────────────────────────────────────

    def get_state(self) -> Optional[SpotifyState]:
        status = self._call("GET", "/status")
        # Treat BOTH a failed call (None) AND a content-less success ({}) as
        # "no state" — an empty dict would otherwise build a degenerate
        # all-defaults SpotifyState (stopped=True, no track) that looks like a
        # real idle state but came from a 204/empty body, not go-librespot.
        if not status:
            return None
        track = status.get("track") or {}

        artist = ""
        artist_names = track.get("artist_names")
        if artist_names and isinstance(artist_names, list) and artist_names:
            artist = str(artist_names[0])
        else:
            artists_objs = track.get("artists")
            if artists_objs and isinstance(artists_objs, list) and artists_objs:
                first = artists_objs[0]
                artist = first.get("name", "") if isinstance(first, dict) else str(first)

        return SpotifyState(
            stopped=status.get("stopped", True),
            paused=status.get("paused", False),
            title=track.get("name", ""),
            artist=artist,
            album=track.get("album_name", "") or (track.get("album") or {}).get("name", ""),
            track_uri=track.get("uri", ""),
            position_ms=track.get("position") or 0,
            duration_ms=max(1, track.get("duration") or 0),
            volume=status.get("volume", 0) or 0,
            volume_steps=status.get("volume_steps", 65535) or 65535,
            album_cover_url=track.get("album_cover_url") or "",
        )

    # ─── Playback control ───────────────────────────────────────────────────

    def playpause(self) -> None: self._call("POST", "/player/playpause")
    def play(self) -> None:      self._call("POST", "/player/resume")
    def pause(self) -> None:     self._call("POST", "/player/pause")
    def next(self) -> None:      self._call("POST", "/player/next")
    def prev(self) -> None:      self._call("POST", "/player/prev")

    def set_volume(self, pct: int, volume_steps: int = 65535) -> None:
        val = round(pct / 100.0 * volume_steps)
        self._call("POST", "/player/volume", json={"volume": val})

    def close_session(self) -> None:
        """Gracefully end the Spotify session without restarting the service.

        Uses POST /player/close (go-librespot v0.8.0+). Falls back to
        service restart if close fails. This preserves Zeroconf registration
        so the speaker stays visible in Spotify Connect — much better UX
        than a full service restart which causes a 2-3s disappearance.
        """
        result = self._call("POST", "/player/close")
        if result is not None:
            log.info("Spotify session closed via API")
            return
        # Fallback: hard restart
        log.info("Spotify /player/close failed, falling back to service restart")
        try:
            subprocess.run(
                ["systemctl", "restart", "go-librespot"],
                capture_output=True, timeout=10,
            )
        except Exception as e:
            log.error("service restart failed: %s", e)
