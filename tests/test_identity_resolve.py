"""Identity resolution — the resolved_* properties derive <model>-<short_id>
unless a field is explicitly pinned (identity-split phase 2–3).
See docs/identity-split.md."""

from beatbird.config import Profile

DRV = {"soundcard": {"driver": "louder-hat-plus-1x"}}


def _profile(identity=None, instance_id=None, **extra):
    """Build a minimal valid Profile and inject the hardware instance id the
    way load_profile() does at runtime."""
    data = dict(DRV)
    if identity is not None:
        data["identity"] = identity
    data.update(extra)
    p = Profile.model_validate(data)
    p._instance_id = instance_id
    return p


# ─── explicit pin wins (back-compat for existing units) ───────────────────────

def test_pinned_speaker_id_wins_over_derivation():
    p = _profile({"model": "beat", "speaker_id": "beatpi_speaker"}, instance_id="3f2a")
    assert p.resolved_speaker_id == "beatpi_speaker"


def test_pinned_values_survive_even_with_instance_id():
    p = _profile(
        {"model": "beat", "hostname": "beatpi", "friendly_name": "Beat #1",
         "speaker_id": "beatpi_speaker"},
        instance_id="3f2a",
    )
    assert p.resolved_hostname == "beatpi"
    assert p.resolved_friendly_name == "Beat #1"
    assert p.resolved_speaker_id == "beatpi_speaker"


# ─── derivation when unset + an instance id is present ────────────────────────

def test_derived_from_model_and_instance_id():
    p = _profile({"model": "beat"}, instance_id="3f2a")
    assert p.resolved_speaker_id == "beat_3f2a"
    assert p.resolved_hostname == "beat-3f2a"
    assert p.resolved_friendly_name == "Beat 3f2a"


def test_derived_friendly_name_titlecases_multiword_model():
    p = _profile({"model": "zipp-mini"}, instance_id="ab12")
    assert p.resolved_friendly_name == "Zipp Mini ab12"
    assert p.resolved_hostname == "zipp-mini-ab12"
    assert p.resolved_speaker_id == "zipp-mini_ab12"  # slug keeps model verbatim


# ─── fallback when unset + no instance id (dev box / CI / non-Pi) ─────────────

def test_unconfigured_falls_back_to_legacy_generic_speaker_id():
    # Back-compat: a bare profile on a box with no readable CPU serial must
    # reproduce the old default speaker_id so nothing in MQTT-land shifts.
    p = _profile(None, instance_id=None)
    assert p.short_id == "generic"
    assert p.resolved_speaker_id == "beatbird_generic"
    assert p.resolved_hostname == "beatbird-generic"


# ─── mqtt_topic_base no longer crashes on an unset (None) speaker_id ──────────

def test_mqtt_topic_base_handles_unset_identity():
    # Regression: the old `if self.identity.speaker_id in base` raised
    # TypeError once speaker_id became Optional[None]. It now routes through
    # resolved_speaker_id (always a str).
    p = _profile(None, instance_id="3f2a", mqtt={"base_topic": "homeassistant/beat"})
    assert p.mqtt_topic_base == "homeassistant/beat/beatbird_3f2a"


def test_mqtt_topic_base_pinned_still_appends():
    p = _profile({"speaker_id": "zipp_mini_2"}, mqtt={"base_topic": "homeassistant/x"})
    assert p.mqtt_topic_base == "homeassistant/x/zipp_mini_2"
