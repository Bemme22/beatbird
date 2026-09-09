"""
sun.py — where the sun is, computed locally.

Display and strip brightness should follow actual daylight, not the clock: at
this latitude sunset moves by more than three hours across the year, so fixed
hours are either too bright in December or too dim in June.

Deliberately NOT taken from Home Assistant, which does publish `sun.sun` and
`sensor.sun_next_*`. Sun position is arithmetic over date and coordinates — it
needs no network, no broker and no integration, and making the brightness of a
speaker depend on a smart-home server would mean the display misbehaves when
that server is down. The bridge's own rule is that modules degrade gracefully;
a dependency for something this self-contained buys nothing.

HA is still useful here — as a REFERENCE. Verified against its sun integration
at the installation site on 2026-09-10: dawn -27 s, sunrise -64 s, sunset
+85 s, dusk +49 s. The residual is symmetric about solar noon (midpoints agree
to 10 s), so it is a slightly different refraction constant than astral's, not
an error in the ephemeris. Two orders of magnitude better than this needs to be.

No dependencies beyond the standard library on purpose — this runs on a Pi Zero.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

__all__ = ["elevation", "sun_event", "SUNRISE_ALT", "CIVIL_ALT"]

# Altitude of the sun's centre at the moment the disc touches the horizon:
# refraction (~34') plus the apparent radius (~16').
SUNRISE_ALT = -0.833
# Civil twilight — "you can still read outside".
CIVIL_ALT = -6.0


def _julian_day(d: dt.date) -> float:
    y, m = d.year, d.month
    if m <= 2:
        y, m = y - 1, m + 12
    a = y // 100
    b = 2 - a + a // 4
    return (math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1))
            + d.day + b - 1524.5)


def _solar(t: float) -> tuple[float, float]:
    """Declination in degrees and the equation of time in minutes, for Julian
    century `t`. Standard NOAA series."""
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t))
         + math.sin(2 * mr) * (0.019993 - 0.000101 * t)
         + math.sin(3 * mr) * 0.000289)
    lam = l0 + c - 0.00569 - 0.00478 * math.sin(math.radians(125.04 - 1934.136 * t))
    e0 = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
    eps = e0 + 0.00256 * math.cos(math.radians(125.04 - 1934.136 * t))
    dec = math.degrees(math.asin(math.sin(math.radians(eps)) * math.sin(math.radians(lam))))
    y = math.tan(math.radians(eps / 2)) ** 2
    l0r = math.radians(l0)
    eqt = 4 * math.degrees(
        y * math.sin(2 * l0r) - 2 * e * math.sin(mr)
        + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
        - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))
    return dec, eqt


def elevation(lat: float, lon: float, when_utc: dt.datetime) -> float:
    """Sun elevation in degrees above the horizon. Negative = below."""
    t = (_julian_day(when_utc.date()) - 2451545.0) / 36525.0
    dec, eqt = _solar(t)
    minutes = when_utc.hour * 60 + when_utc.minute + when_utc.second / 60.0
    true_solar = (minutes + eqt + 4 * lon) % 1440
    hour_angle = true_solar / 4 - 180
    phi, d, h = math.radians(lat), math.radians(dec), math.radians(hour_angle)
    zenith = math.acos(math.sin(phi) * math.sin(d)
                       + math.cos(phi) * math.cos(d) * math.cos(h))
    return 90.0 - math.degrees(zenith)


def sun_event(lat: float, lon: float, date: dt.date, altitude_deg: float,
              rising: bool, iterations: int = 3) -> Optional[dt.datetime]:
    """UTC time at which the sun passes `altitude_deg`, or None if it never
    does that day (polar cases — irrelevant here, but the caller must not crash).

    Iterated: the declination moves during the day (~0.4°/day near the
    equinoxes). Evaluating it only at solar noon places sunrise and sunset
    symmetrically about noon, and the error then falls in opposite directions —
    measured at -35 s / +116 s before iterating, -64 s / +85 s after.
    """
    jd0 = _julian_day(date)
    fraction = 0.5                     # start at noon UTC
    minutes = 720.0
    for _ in range(iterations):
        t = (jd0 + fraction - 0.5 - 2451545.0) / 36525.0
        dec, eqt = _solar(t)
        phi, d = math.radians(lat), math.radians(dec)
        cos_h = ((math.sin(math.radians(altitude_deg)) - math.sin(phi) * math.sin(d))
                 / (math.cos(phi) * math.cos(d)))
        if cos_h > 1.0 or cos_h < -1.0:
            return None                # sun stays above or below all day
        h = math.degrees(math.acos(cos_h))
        minutes = (720 - 4 * lon - eqt) + (-4 * h if rising else 4 * h)
        fraction = minutes / 1440.0
    return dt.datetime.combine(date, dt.time()) + dt.timedelta(minutes=minutes)
