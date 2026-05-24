"""
beatbird.display.base — abstract display interface.

Two implementations exist:
  - AmoledDisplay — Waveshare ESP32-S3 over USB serial (Beat, Zipp Mini 2, Zipp 2)
  - LedButtonDisplay — GPIO ring + single button (LT300, Lounge)

Both expose the same small set of methods so the bridge doesn't care which
physical UI is attached.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable


@dataclass
class DisplayState:
    """Snapshot of everything the display needs to render."""
    playback: str = "stop"       # play | pause | stop | standby
    source: str = "none"         # spotify | bluetooth | toslink | snapcast | none
    title: str = ""
    artist: str = ""
    volume: int = 0              # 0..100
    position_ms: int = 0
    duration_ms: int = 1
    signal_level: int = 0        # 0..100
    time_hhmm: str = ""
    spectrum: list[int] | None = None   # optional N-band 0..100


@dataclass
class DisplaySystemStatus:
    """Less-frequently updated system health for diagnostics screens."""
    cpu_temp: float = 0.0
    wifi_rssi: int = 0
    amp_statuses: dict[str, str] = None    # type: ignore[assignment]
    dsp_active: bool = False
    spotify_active: bool = False
    gateway_reachable: bool = True         # default true so a stale push doesn't lie
    spotify_stuck_recent: bool = False     # bridge fired a go-librespot restart in last 60s


# Callbacks fired by the display when the user interacts with it.
# The bridge sets these during setup().
CommandCallback = Callable[[str], None]   # "PLAY" | "PAUSE" | "NEXT" | "PREV" | "STOP"
VolumeCallback = Callable[[int], None]    # 0..100


class DisplayInterface(ABC):
    @abstractmethod
    def setup(
        self,
        on_command: CommandCallback | None = None,
        on_volume: VolumeCallback | None = None,
    ) -> None: ...

    @abstractmethod
    def push_state(self, state: DisplayState) -> None: ...

    @abstractmethod
    def push_system(self, status: DisplaySystemStatus) -> None: ...

    @abstractmethod
    def poll(self) -> None:
        """Drain any input from the display (non-blocking). Call from main loop."""

    def push_raw(self, line: str) -> None:
        """Send an arbitrary single line (with trailing newline already
        appended) to the display. Used by background pushers (e.g. the
        weather poller) that don't fit the state/system schema. Default
        is a no-op for displays that don't support out-of-band pushes."""

    def push_idle_message(self, text: str) -> None:
        """Show a short text on the standby screen via the split-flap
        animation. Bridge sends one every ~45s while idle, so the standby
        screen has personality instead of staring back blankly. Default
        is a no-op for displays without a standby screen."""

    @abstractmethod
    def close(self) -> None: ...
