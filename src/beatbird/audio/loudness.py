"""
beatbird.audio.loudness — volume-dependent low-frequency compensation.

Based on simplified ISO 226 equal-loudness contours: at low playback volumes,
the ear hears bass less efficiently, so we add a small boost to the bass and
lower-mid filters. The boost scales from 0 (at 80% volume and above) up to a
per-filter ``max_boost_db`` at very low volumes.

The filter names must match those in the active CamillaDSP config; base
gains are read from a dictionary supplied by the caller (usually parsed
from the profile + DSP YAML at startup).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from beatbird.audio.camilladsp import CamillaDSP

log = logging.getLogger("beatbird.loudness")


@dataclass
class LoudnessFilter:
    name: str
    type: str           # "Lowshelf" | "Peaking" | …
    freq: float
    base_gain: float
    q: float
    max_boost: float    # max additional boost at volume=0


def offset_curve(volume_pct: int) -> float:
    """Return 0.0..1.0 compensation factor for a given volume.

    0 at volume >= 80, 1.0 at volume <= 5, smooth curve in between.
    """
    if volume_pct >= 80:
        return 0.0
    if volume_pct <= 5:
        return 1.0
    return ((80 - volume_pct) / 75.0) ** 1.5


class LoudnessController:
    """Patches CamillaDSP filter gains based on current volume."""

    def __init__(self, dsp: CamillaDSP, filters: list[LoudnessFilter]):
        self.dsp = dsp
        self.filters = filters
        self._last_offset: float | None = None

    def apply(self, volume_pct: int) -> None:
        """Compute and send a PatchConfig for the current volume."""
        if not self.filters:
            return

        offset = offset_curve(volume_pct)
        # Only patch if offset actually changed meaningfully
        if self._last_offset is not None and abs(offset - self._last_offset) < 0.02:
            return
        self._last_offset = offset

        patch: dict[str, dict] = {}
        for f in self.filters:
            gain = round(f.base_gain + (f.max_boost * offset), 1)
            patch[f.name] = {
                "parameters": {
                    "type": f.type, "freq": f.freq, "gain": gain, "q": f.q,
                }
            }
        self.dsp.patch_filters(patch)
        log.debug("loudness vol=%d%% offset=%.2f", volume_pct, offset)
