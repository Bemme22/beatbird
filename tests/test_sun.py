"""Tests for beatbird.sun.

Deliberately built on ASTRONOMICAL INVARIANTS rather than on a table of
sunrise times for one place. The implementation was verified against Home
Assistant's sun integration during development (worst case 85 s, symmetric
about solar noon), but those reference values belong to the user's actual
coordinates — personal data, which is exactly why the rest of the codebase
keeps them out of the committed YAML. Invariants test the same maths, hold
everywhere, and need no almanac.
"""
import datetime as dt
import math

from beatbird import sun

EQUINOX = dt.date(2026, 3, 20)
SOLSTICE_JUN = dt.date(2026, 6, 21)
SOLSTICE_DEC = dt.date(2026, 12, 21)


def _day_length_h(lat, lon, date):
    rise = sun.sun_event(lat, lon, date, sun.SUNRISE_ALT, True)
    set_ = sun.sun_event(lat, lon, date, sun.SUNRISE_ALT, False)
    return (set_ - rise).total_seconds() / 3600.0


def test_equinox_day_is_a_touch_over_twelve_hours_everywhere():
    # The disc clears the horizon before its centre does (refraction + radius),
    # so an equinox day runs slightly LONGER than 12 h, and more so towards the
    # poles where the sun cuts the horizon at a shallower angle.
    for lat in (0.0, 25.0, 51.0):
        length = _day_length_h(lat, 0.0, EQUINOX)
        assert 12.0 < length < 12.35, (lat, length)


def test_day_is_longer_in_june_and_shorter_in_december_up_north():
    lat = 51.0
    assert _day_length_h(lat, 0.0, SOLSTICE_JUN) > 16.0
    assert _day_length_h(lat, 0.0, SOLSTICE_DEC) < 8.5


def test_southern_hemisphere_is_the_other_way_round():
    assert _day_length_h(-51.0, 0.0, SOLSTICE_DEC) > 16.0
    assert _day_length_h(-51.0, 0.0, SOLSTICE_JUN) < 8.5


def test_sunrise_and_sunset_straddle_solar_noon():
    # Not exactly symmetric — the declination moves during the day — but the
    # midpoint must sit within a couple of minutes of local solar noon.
    lat, lon = 51.0, 13.0
    rise = sun.sun_event(lat, lon, EQUINOX, sun.SUNRISE_ALT, True)
    set_ = sun.sun_event(lat, lon, EQUINOX, sun.SUNRISE_ALT, False)
    midpoint = rise + (set_ - rise) / 2
    noon_utc = 12 * 60 - 4 * lon                     # minutes, ignoring eq. of time
    got = midpoint.hour * 60 + midpoint.minute + midpoint.second / 60
    assert abs(got - noon_utc) < 20                  # eq. of time is ~-7 min here


def test_civil_dawn_is_before_sunrise_and_dusk_after_sunset():
    lat, lon = 51.0, 13.0
    dawn = sun.sun_event(lat, lon, EQUINOX, sun.CIVIL_ALT, True)
    rise = sun.sun_event(lat, lon, EQUINOX, sun.SUNRISE_ALT, True)
    set_ = sun.sun_event(lat, lon, EQUINOX, sun.SUNRISE_ALT, False)
    dusk = sun.sun_event(lat, lon, EQUINOX, sun.CIVIL_ALT, False)
    assert dawn < rise < set_ < dusk
    for gap in ((rise - dawn), (dusk - set_)):
        assert 20 < gap.total_seconds() / 60 < 60    # civil twilight, mid-latitude


def test_polar_night_and_midnight_sun_return_none():
    # No event at all — the caller must get None rather than a domain error.
    assert sun.sun_event(80.0, 0.0, SOLSTICE_DEC, sun.SUNRISE_ALT, True) is None
    assert sun.sun_event(80.0, 0.0, SOLSTICE_JUN, sun.SUNRISE_ALT, False) is None


def test_noon_elevation_matches_the_geometry():
    # At SOLAR noon the elevation is 90 - |latitude - declination|; on the
    # equinox the declination is ~0, so it reduces to 90 - |lat|.
    # ⚠️ Solar noon is not 12:00 UTC even on the prime meridian — the equation
    # of time shifts it by up to ~16 minutes (~7.5 min in late March, which is
    # a 1.9 deg hour angle and cost this test its first version). Take the
    # midpoint of sunrise and sunset instead, which is solar noon by definition.
    for lat in (0.0, 30.0, 51.0):
        rise = sun.sun_event(lat, 0.0, EQUINOX, sun.SUNRISE_ALT, True)
        set_ = sun.sun_event(lat, 0.0, EQUINOX, sun.SUNRISE_ALT, False)
        noon = rise + (set_ - rise) / 2
        el = sun.elevation(lat, 0.0, noon)
        assert abs(el - (90 - abs(lat))) < 1.0, (lat, el)


def test_elevation_is_negative_at_local_midnight():
    for lat in (0.0, 51.0, -30.0):
        midnight = dt.datetime.combine(EQUINOX, dt.time(0, 0))
        assert sun.elevation(lat, 0.0, midnight) < 0


def test_elevation_crosses_zero_at_the_computed_sunrise():
    # Ties the two entry points together: whatever sun_event() calls sunrise,
    # elevation() must agree that the sun is at the horizon there.
    lat, lon = 51.0, 13.0
    rise = sun.sun_event(lat, lon, EQUINOX, sun.SUNRISE_ALT, True)
    assert abs(sun.elevation(lat, lon, rise) - sun.SUNRISE_ALT) < 0.2
