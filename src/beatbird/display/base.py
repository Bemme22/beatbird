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

    @abstractmethod
    def close(self) -> None: ...
