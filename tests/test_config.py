"""Profile schema validation — load every YAML in profiles/ and check
that it parses cleanly. This is the smoke test that catches profile
drift the moment someone forgets to update one of six speakers when
the schema changes."""

from pathlib import Path

import pytest
import yaml

from beatbird.config import Profile


PROFILES_DIR = Path(__file__).parent.parent / "profiles"


def _profile_yamls():
    """All committed speaker profiles, excluding the _template / _stub
    helpers that aren't meant to load on their own."""
    return sorted(
        p for p in PROFILES_DIR.glob("*.yml")
        if not p.name.startswith("_")
        and p.name != "current.yml"  # symlink to active profile
    )


@pytest.mark.parametrize("yml", _profile_yamls(), ids=lambda p: p.stem)
def test_profile_loads(yml):
    """Every committed profile must validate against the Pydantic schema.
    Doesn't run the speaker, just checks the static config."""
    with open(yml) as f:
        data = yaml.safe_load(f)
    assert data is not None, f"{yml.name} parses to None"
    Profile.model_validate(data)


# ─── format= canonicalisation (S32LE → S32_LE) ──────────────────────────────

def test_format_legacy_s32le_canonicalised():
    """Historical profiles use 'S32LE' (no underscore); the validator
    must accept and rewrite it to the CDSP-canonical 'S32_LE'."""
    p = Profile.model_validate({
        "audio": {"format": "S32LE"},
        "soundcard": {"driver": "louder-hat-plus-1x"},
    })
    assert p.audio.format == "S32_LE"


def test_format_canonical_passes():
    p = Profile.model_validate({
        "audio": {"format": "S32_LE"},
        "soundcard": {"driver": "louder-hat-plus-1x"},
    })
    assert p.audio.format == "S32_LE"


def test_format_lowercase_canonicalised():
    p = Profile.model_validate({
        "audio": {"format": "s32le"},
        "soundcard": {"driver": "louder-hat-plus-1x"},
    })
    assert p.audio.format == "S32_LE"


def test_format_unknown_rejected():
    """Bogus value should be rejected with a clear error, not silently
    accepted (which would let CDSP crash mysteriously later)."""
    with pytest.raises(Exception):  # noqa: B017 — pydantic.ValidationError, kept loose for v1/v2
        Profile.model_validate({
            "audio": {"format": "F32_LE"},  # not a real ALSA format
            "soundcard": {"driver": "louder-hat-plus-1x"},
        })


# ─── Driver Literal — innomaker removed ─────────────────────────────────────

def test_innomaker_driver_rejected():
    """innomaker-amp-pro was the burnt-out Zipp Mini 2 amp; removing
    the Literal entry catches stale profiles that still reference it."""
    with pytest.raises(Exception):  # noqa: B017
        Profile.model_validate({
            "soundcard": {"driver": "innomaker-amp-pro"},
        })


def test_louder_hat_drivers_accepted():
    for drv in ("louder-hat-plus-2x", "louder-hat-plus-1x", "louder-hat-triple"):
        p = Profile.model_validate({"soundcard": {"driver": drv}})
        assert p.soundcard.driver == drv


# ─── Amp deep-sleep (idle power save) ───────────────────────────────────────

def test_amp_deep_sleep_default_off():
    """Power-save must be opt-in: an unconfigured profile leaves the amp
    awake (no surprise behaviour on speakers that haven't been verified)."""
    p = Profile.model_validate({"soundcard": {"driver": "louder-hat-plus-1x"}})
    assert p.audio.amp_deep_sleep.enabled is False
    assert p.audio.amp_deep_sleep.timeout_s == 600


def test_amp_deep_sleep_enable_parses():
    p = Profile.model_validate({
        "soundcard": {"driver": "louder-hat-plus-1x"},
        "audio": {"amp_deep_sleep": {"enabled": True, "timeout_s": 300}},
    })
    assert p.audio.amp_deep_sleep.enabled is True
    assert p.audio.amp_deep_sleep.timeout_s == 300


# ─── Main-loop timing (profile-driven poll cadences) ────────────────────────

def test_timing_defaults_match_historical_constants():
    """Defaults must reproduce the old bridge.py module constants exactly, so
    making them profile-driven is a no-op for every existing speaker."""
    t = Profile.model_validate({"soundcard": {"driver": "louder-hat-plus-1x"}}).timing
    assert t.status_interval_s == 5.0
    assert t.spotify_poll_interval_s == 2.0
    assert t.snapcast_poll_interval_s == 3.0
    assert t.level_poll_interval_s == 0.1
    assert t.state_push_playing_s == 0.2
    assert t.state_push_idle_s == 2.0
    assert t.spotify_health_restart_threshold == 15


def test_timing_overrides_parse():
    p = Profile.model_validate({
        "soundcard": {"driver": "louder-hat-plus-1x"},
        "timing": {"status_interval_s": 2.0, "level_poll_interval_s": 0.05,
                   "spotify_health_restart_threshold": 30},
    })
    assert p.timing.status_interval_s == 2.0
    assert p.timing.level_poll_interval_s == 0.05
    assert p.timing.spotify_health_restart_threshold == 30
    # untouched fields keep their defaults
    assert p.timing.snapcast_poll_interval_s == 3.0
