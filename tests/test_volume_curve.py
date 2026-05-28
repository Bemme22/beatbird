"""pct_to_db / db_to_pct — pure-math, no hardware needed."""

import math

import pytest

from beatbird.audio.camilladsp import pct_to_db, db_to_pct


# ─── Endpoint behaviour ─────────────────────────────────────────────────────

def test_zero_pct_maps_to_min_db():
    assert pct_to_db(0, min_db=-60.0, max_db=0.0, gamma=1.0) == -60.0


def test_hundred_pct_maps_to_max_db():
    assert pct_to_db(100, min_db=-60.0, max_db=0.0, gamma=1.0) == 0.0


def test_negative_pct_clamped_to_zero():
    assert pct_to_db(-10) == pct_to_db(0)


def test_over_hundred_clamped():
    assert pct_to_db(150) == pct_to_db(100)


# ─── Linear gamma (legacy behaviour) ────────────────────────────────────────

def test_linear_gamma_midpoint_is_midpoint():
    db = pct_to_db(50, min_db=-60.0, max_db=0.0, gamma=1.0)
    assert db == pytest.approx(-30.0, abs=0.5)


# ─── Sonos-style audio taper ────────────────────────────────────────────────

def test_gamma_2_pulls_half_slider_to_perceptual_half():
    """At gamma=2.0, 50 % UI should land near the perceptual half-loudness
    point (~-6 dB on a -60..0 dB scale), not the geometric midpoint."""
    db = pct_to_db(50, min_db=-60.0, max_db=0.0, gamma=2.0)
    # gamma=2 → p = (0.5)^0.5 = 0.707 → db = -60 + 60*0.707 = -17.6
    assert db == pytest.approx(-17.6, abs=1.0)


def test_audio_taper_monotonic():
    """No backslides as the slider goes up — must be strictly non-decreasing."""
    last = -math.inf
    for pct in range(0, 101):
        db = pct_to_db(pct, gamma=2.0)
        assert db >= last - 0.01, f"backslide at {pct}: {db} < {last}"
        last = db


# ─── Round-trip pct → db → pct ──────────────────────────────────────────────

@pytest.mark.parametrize("pct", [0, 5, 25, 37, 50, 75, 100])
@pytest.mark.parametrize("gamma", [1.0, 2.0])
def test_roundtrip_within_1_pct(pct, gamma):
    """db_to_pct(pct_to_db(x)) ≈ x within rounding tolerance (1 pct)."""
    db = pct_to_db(pct, gamma=gamma)
    back = db_to_pct(db, gamma=gamma)
    assert abs(back - pct) <= 1, f"roundtrip {pct} → {db} → {back}"


# ─── db_to_pct edge clamps ──────────────────────────────────────────────────

def test_db_below_min_returns_zero():
    assert db_to_pct(-100.0) == 0


def test_db_above_max_returns_100():
    assert db_to_pct(20.0) == 100
