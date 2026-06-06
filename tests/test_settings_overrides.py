"""settings_overrides — the override schema + the pure friendly_name layering
(identity-split phase 4). Kept dependency-free so it runs in CI without the
webserver's FastAPI stack."""

from beatbird import settings_overrides as so


def test_empty_has_friendly_name_slot():
    e = so.empty()
    assert e["friendly_name"] is None
    # the other slots are still there (don't silently drop one)
    assert set(e) == {"palette", "idle", "loudness", "dsp_config",
                      "friendly_name", "eq_editing"}


# ─── effective_friendly_name: override wins, else the resolved default ─────────

def test_override_name_wins():
    assert so.effective_friendly_name({"friendly_name": "Küche"}, "Beat 3f2a") == "Küche"


def test_override_is_trimmed():
    assert so.effective_friendly_name({"friendly_name": "  Küche  "}, "x") == "Küche"


def test_blank_override_falls_back_to_default():
    assert so.effective_friendly_name({"friendly_name": "   "}, "Beat 3f2a") == "Beat 3f2a"


def test_none_override_falls_back_to_default():
    assert so.effective_friendly_name({"friendly_name": None}, "Beat 3f2a") == "Beat 3f2a"


def test_missing_key_falls_back_to_default():
    assert so.effective_friendly_name({}, "Beat 3f2a") == "Beat 3f2a"


def test_non_dict_overrides_fall_back_to_default():
    assert so.effective_friendly_name(None, "Beat 3f2a") == "Beat 3f2a"
