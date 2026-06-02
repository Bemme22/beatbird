"""offset_curve — pure-math compensation factor for loudness EQ."""

from beatbird.audio.loudness import offset_curve


# ─── Legacy curve ───────────────────────────────────────────────────────────

def test_legacy_max_boost_at_low_vol():
    assert offset_curve(0, "legacy") == 1.0
    assert offset_curve(5, "legacy") == 1.0


def test_legacy_zero_boost_at_high_vol():
    assert offset_curve(80, "legacy") == 0.0
    assert offset_curve(100, "legacy") == 0.0


def test_legacy_monotonic_decay():
    """Boost must shrink (or stay equal) as volume rises."""
    last = offset_curve(0, "legacy")
    for v in range(0, 101):
        cur = offset_curve(v, "legacy")
        assert cur <= last + 1e-9, f"non-monotonic at vol={v}: {cur} > {last}"
        last = cur


def test_legacy_in_unit_interval():
    for v in range(0, 101):
        x = offset_curve(v, "legacy")
        assert 0.0 <= x <= 1.0, f"out of [0,1] at vol={v}: {x}"


# ─── Smoothstep curve ───────────────────────────────────────────────────────

def test_smoothstep_full_boost_plateau():
    """The whole point of smoothstep is a flat full-boost region up to
    vol=10 — at low vol the bass shelf should be at its compensated max,
    not already decaying."""
    assert offset_curve(0,  "smoothstep") == 1.0
    assert offset_curve(5,  "smoothstep") == 1.0
    assert offset_curve(10, "smoothstep") == 1.0


def test_smoothstep_decays_to_zero_at_75():
    assert offset_curve(75,  "smoothstep") == 0.0
    assert offset_curve(100, "smoothstep") == 0.0


def test_smoothstep_monotonic_decay():
    last = offset_curve(0, "smoothstep")
    for v in range(0, 101):
        cur = offset_curve(v, "smoothstep")
        assert cur <= last + 1e-9, f"non-monotonic at vol={v}: {cur} > {last}"
        last = cur


def test_smoothstep_midpoint_at_half():
    """At vol=42.5 (midpoint of the 10..75 transition) smoothstep
    cubic produces exactly 0.5 boost. Sanity check that the curve
    matches its mathematical definition rather than some hand-tuned
    table."""
    # 3t^2 - 2t^3 at t=0.5 equals 0.5
    mid = offset_curve(42, "smoothstep")  # close to t=0.5
    # 42 is t=(42-10)/65=0.492 → smoothstep≈0.486 → boost≈0.514
    assert 0.40 < mid < 0.60


def test_smoothstep_in_unit_interval():
    for v in range(0, 101):
        x = offset_curve(v, "smoothstep")
        assert 0.0 <= x <= 1.0, f"out of [0,1] at vol={v}: {x}"


# ─── Unknown curve name falls back ──────────────────────────────────────────

def test_unknown_curve_defaults_to_legacy():
    """Misspelt curve name shouldn't crash or return None — should
    silently fall through to legacy so a typo'd profile still plays."""
    for v in (0, 50, 100):
        assert offset_curve(v, "doesnotexist") == offset_curve(v, "legacy")


# ─── Web-tunable knees ──────────────────────────────────────────────────────

def test_smoothstep_custom_knees():
    """The web UI moves the knees ('voller Boost bis' / 'kein Boost ab')."""
    assert offset_curve(20, "smoothstep", knee_low=20, knee_high=60) == 1.0
    assert offset_curve(60, "smoothstep", knee_low=20, knee_high=60) == 0.0
    # midpoint of 20..60 = 40 → smoothstep 0.5 → boost 0.5
    assert abs(offset_curve(40, "smoothstep", 20, 60) - 0.5) < 1e-9


def test_smoothstep_knee_guard_hi_le_lo():
    """hi <= lo must not divide-by-zero or invert — guarded to lo+1."""
    for v in (0, 30, 100):
        x = offset_curve(v, "smoothstep", knee_low=50, knee_high=50)
        assert 0.0 <= x <= 1.0


# ─── build_loudness merge (defaults + profile + overrides) ──────────────────

class _F:
    def __init__(self, name, max_boost_db):
        self.name = name
        self.max_boost_db = max_boost_db


class _Loud:
    enabled = True
    curve = "smoothstep"

    def __init__(self, filters):
        self.filters = filters


class _Audio:
    def __init__(self, loud):
        self.loudness = loud


class _Profile:
    def __init__(self, filters):
        self.audio = _Audio(_Loud(filters))


def test_build_loudness_defaults():
    from beatbird.audio.loudness import build_loudness
    p = _Profile([_F("bass_shelf", 6.0), _F("timpani_body", 3.0)])
    filters, curve, lo, hi = build_loudness(p, {})
    by = {f.name: f for f in filters}
    assert by["bass_shelf"].base_gain == 10  # from DEFAULT_BASE
    assert by["bass_shelf"].max_boost == 6.0  # from profile
    assert by["bass_shelf"].freq == 120
    assert (curve, lo, hi) == ("smoothstep", 10, 75)


def test_build_loudness_override_wins():
    from beatbird.audio.loudness import build_loudness
    p = _Profile([_F("bass_shelf", 6.0), _F("timpani_body", 3.0)])
    ov = {"loudness": {"curve": "legacy", "knee_low": 5, "knee_high": 70,
                       "filters": {"bass_shelf": {"base_gain": 2, "max_boost": 11}}}}
    filters, curve, lo, hi = build_loudness(p, ov)
    by = {f.name: f for f in filters}
    assert (by["bass_shelf"].base_gain, by["bass_shelf"].max_boost) == (2.0, 11.0)
    # untouched filter keeps profile/default
    assert (by["timpani_body"].base_gain, by["timpani_body"].max_boost) == (3.0, 3.0)
    assert (curve, lo, hi) == ("legacy", 5, 70)


def test_build_loudness_skips_unknown_filter():
    from beatbird.audio.loudness import build_loudness
    p = _Profile([_F("bass_shelf", 6.0), _F("not_a_real_filter", 5.0)])
    filters, *_ = build_loudness(p, {})
    assert [f.name for f in filters] == ["bass_shelf"]
