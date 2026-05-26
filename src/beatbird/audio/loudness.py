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


def offset_curve(volume_pct: int, curve: str = "legacy") -> float:
    """Return 0.0..1.0 bass-boost compensation factor for a given volume.

    ``curve`` selects the response shape:
      * "legacy"     — original ``((80-vol)/75)^1.5``, fades 1.0 → 0.0 over
                        vol=5..80. Conservative; modest mid-range boost.
      * "smoothstep" — plateau at full boost up to vol=10, cubic-smoothstep
                        decay through vol=75. Matches the "bass max at low
                        vol, mids/highs grow alone at high vol" intent.
    """
    if curve == "smoothstep":
        if volume_pct <= 10:
            return 1.0
        if volume_pct >= 75:
            return 0.0
        t = (volume_pct - 10) / 65.0          # 0 at vol=10, 1 at vol=75
        smoothstep = 3.0 * t * t - 2.0 * t * t * t
        return 1.0 - smoothstep

    # Default: legacy curve
    if volume_pct >= 80:
        return 0.0
    if volume_pct <= 5:
        return 1.0
    return ((80 - volume_pct) / 75.0) ** 1.5


class LoudnessController:
    """Patches CamillaDSP filter gains based on current volume."""

    def __init__(self, dsp: CamillaDSP, filters: list[LoudnessFilter],
                 curve: str = "legacy"):
        self.dsp = dsp
        self.filters = filters
        self.curve = curve
        self._last_offset: float | None = None

    def apply(self, volume_pct: int) -> None:
        """Compute and send a PatchConfig for the current volume."""
        if not self.filters:
            return

        # Defensive clamp. set_volume in the bridge already clamps but
        # apply() is also called directly from MQTT / Spotify-sync paths
        # and a stray out-of-range value would feed offset_curve a
        # negative volume and yield offset > 1.0 → bass spikes well
        # past max_boost.
        if not 0 <= volume_pct <= 100:
            log.warning("loudness apply: out-of-range vol=%s, clamping", volume_pct)
            volume_pct = max(0, min(100, int(volume_pct)))

        offset = offset_curve(volume_pct, self.curve)
        # Belt-and-braces — offset_curve should never return outside
        # [0, 1] given a clamped vol_pct, but log if it ever does.
        if not 0.0 <= offset <= 1.0:
            log.error("loudness apply: offset=%.3f out of [0,1] at vol=%d; clamping",
                      offset, volume_pct)
            offset = max(0.0, min(1.0, offset))

        # Only patch if offset actually changed meaningfully
        if self._last_offset is not None and abs(offset - self._last_offset) < 0.02:
            return
        self._last_offset = offset

        patch: dict[str, dict] = {}
        gains_log: list[str] = []
        for f in self.filters:
            gain = round(f.base_gain + (f.max_boost * offset), 1)
            patch[f.name] = {
                "parameters": {
                    "type": f.type, "freq": f.freq, "gain": gain, "q": f.q,
                }
            }
            gains_log.append(f"{f.name}={gain}dB")
        self.dsp.patch_filters(patch)
        # INFO so the bridge journal carries a record every time bass
        # gain moves — lets us correlate "bass jumped to max" reports
        # with a specific volume value or restart event.
        log.info("loudness vol=%d%% offset=%.2f  %s",
                 volume_pct, offset, " ".join(gains_log))
