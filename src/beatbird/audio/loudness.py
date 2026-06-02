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


DEFAULT_KNEE_LOW = 10
DEFAULT_KNEE_HIGH = 75


def offset_curve(volume_pct: int, curve: str = "legacy",
                 knee_low: int = DEFAULT_KNEE_LOW,
                 knee_high: int = DEFAULT_KNEE_HIGH) -> float:
    """Return 0.0..1.0 bass-boost compensation factor for a given volume.

    ``curve`` selects the response shape:
      * "legacy"     — original ``((80-vol)/75)^1.5``, fades 1.0 → 0.0 over
                        vol=5..80. Fixed shape; ignores the knees.
      * "smoothstep" — plateau at full boost up to ``knee_low``, cubic-
                        smoothstep decay through ``knee_high``. The knees are
                        web-UI tunable ("voller Boost bis / kein Boost ab").
    """
    if curve == "smoothstep":
        lo = knee_low
        hi = max(knee_high, knee_low + 1)     # guard against hi <= lo
        if volume_pct <= lo:
            return 1.0
        if volume_pct >= hi:
            return 0.0
        t = (volume_pct - lo) / float(hi - lo)
        smoothstep = 3.0 * t * t - 2.0 * t * t * t
        return 1.0 - smoothstep

    # Default: legacy curve (fixed shape)
    if volume_pct >= 80:
        return 0.0
    if volume_pct <= 5:
        return 1.0
    return ((80 - volume_pct) / 75.0) ** 1.5


# Built-in per-filter voicing: type / freq / q + base_gain (the gain at HIGH
# volume, i.e. offset 0). The profile selects which filters are active and
# their max_boost; settings-overrides (web UI) can retune base_gain + max_boost
# live. Kept here so the bridge AND the webserver share one source of truth
# (previously the bridge hard-coded this, which made it un-tunable).
DEFAULT_BASE = {
    "bass_shelf":   {"type": "Lowshelf",  "freq": 120,  "base_gain": 10, "q": 0.6},
    "sub_punch":    {"type": "Peaking",   "freq": 45,   "base_gain": 5,  "q": 0.7},
    "timpani_body": {"type": "Peaking",   "freq": 70,   "base_gain": 3,  "q": 1.0},
    "fullness":     {"type": "Peaking",   "freq": 200,  "base_gain": 3,  "q": 1.0},
    "air_lift":     {"type": "Highshelf", "freq": 8000, "base_gain": 0,  "q": 0.7},
}


def build_loudness(profile, overrides: dict | None = None):
    """Merge profile loudness config + DEFAULT_BASE + web-UI overrides into the
    runtime loudness definition.

    Precedence per field: settings-overrides > profile > DEFAULT_BASE.
    Returns ``(filters, curve, knee_low, knee_high)`` — the webserver calls this
    to render the current values, the bridge to drive the controller. One shared
    definition, so the two processes never drift.
    """
    ov = (overrides or {}).get("loudness") or {}
    ov_filters = ov.get("filters") or {}
    curve = ov.get("curve") or profile.audio.loudness.curve
    knee_low = int(ov.get("knee_low", DEFAULT_KNEE_LOW))
    knee_high = int(ov.get("knee_high", DEFAULT_KNEE_HIGH))

    filters: list[LoudnessFilter] = []
    for f in profile.audio.loudness.filters:
        base = DEFAULT_BASE.get(f.name)
        if base is None:
            log.warning("loudness: unknown filter %r, skipping", f.name)
            continue
        o = ov_filters.get(f.name) or {}
        filters.append(LoudnessFilter(
            name=f.name, type=base["type"], freq=base["freq"], q=base["q"],
            base_gain=float(o.get("base_gain", base["base_gain"])),
            max_boost=float(o.get("max_boost", f.max_boost_db)),
        ))
    return filters, curve, knee_low, knee_high


class LoudnessController:
    """Patches CamillaDSP filter gains based on current volume."""

    def __init__(self, dsp: CamillaDSP, filters: list[LoudnessFilter],
                 curve: str = "legacy",
                 knee_low: int = DEFAULT_KNEE_LOW,
                 knee_high: int = DEFAULT_KNEE_HIGH):
        self.dsp = dsp
        self.filters = filters
        self.curve = curve
        self.knee_low = knee_low
        self.knee_high = knee_high
        self._last_offset: float | None = None

    def set_params(self, filters: list[LoudnessFilter], curve: str,
                   knee_low: int, knee_high: int) -> None:
        """Swap the loudness definition (base/max/curve/knees) at runtime —
        used by the bridge when the web UI writes new settings-overrides.
        Resets the offset throttle so the very next apply() re-patches even
        when the volume (offset) hasn't moved."""
        self.filters = filters
        self.curve = curve
        self.knee_low = knee_low
        self.knee_high = knee_high
        self._last_offset = None

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

        offset = offset_curve(volume_pct, self.curve, self.knee_low, self.knee_high)
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
