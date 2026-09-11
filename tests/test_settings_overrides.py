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


# ─── Palette: the slots the firmware fills in by itself ───────────────────────
# These mirror C++ (theme.cpp). If someone changes one side, this fails.

def test_derive_glow_pushes_the_top_channel_to_full():
    assert so.derive_glow("#f0cb7b") == "#ffd883"   # champagne default
    assert so.derive_glow("#e0913f") == "#ffa548"   # RobinPi bronze
    assert so.derive_glow("#2c95a8") == "#43e2ff"   # petrol


def test_derive_glow_keeps_the_saturation():
    """The regression this exists for: glow must be brightened by SATURATING,
    never by mixing in white. The strip renders it at ~full level, where a
    pastel is just white light (RobinPi's white VU meter, 05.09.2026)."""
    for accent in ("#f0cb7b", "#e0913f", "#2c95a8", "#3f7a2d"):
        a = so._rgb(accent)
        g = so._rgb(so.derive_glow(accent))
        assert max(g) == 255                                   # is brighter
        assert abs((1 - min(g) / max(g)) - (1 - min(a) / max(a))) < 0.01


def test_derive_glow_edge_cases():
    assert so.derive_glow("#000000") == "#000000"              # no hue to keep
    assert so.derive_glow("#ff0000") == "#ff0000"              # already at full


def test_derive_dim_is_a_quarter():
    assert so.derive_dim("#f0cb7b") == "#3c321e"
    assert so.derive_dim("#e0913f") == "#38240f"


def test_fill_derived_palette_completes_an_accent_only_profile():
    pal, derived = so.fill_derived_palette({"a": "#e0913f"})
    assert pal["g"] == "#ffa548" and pal["d"] == "#38240f"
    assert pal["p"] == "#f4efe0" and pal["e"] == "#c73e2c"
    assert set(derived) == {"g", "d", "p", "s", "e"}
    # an empty slot must never come back as black — that is what a save then
    # persisted as an override (invisible text, dark strip)
    assert "#000000" not in pal.values()


def test_fill_derived_palette_keeps_explicit_slots():
    pal, derived = so.fill_derived_palette({"a": "#2c95a8", "g": "#63c6d8"})
    assert pal["g"] == "#63c6d8"        # zipp-mini-2's hand-picked glow wins
    assert "g" not in derived
    assert "d" in derived


# ─── merge_palette: PATCH per slot, not replace ─────────────────────────────
# The regression these guard against: a save that carried only the slots the
# user had just touched used to REPLACE the override set, silently dropping the
# rest. That is how RobinPi ended up with a glow derived from one accent, a dim
# derived from another, and no accent at all.

def test_merge_palette_keeps_untouched_slots():
    cur = {"a": "#e0913f", "e": "#c73e2c"}
    assert so.merge_palette(cur, {"g": "#ffa548"}) == {
        "a": "#e0913f", "e": "#c73e2c", "g": "#ffa548"}


def test_merge_palette_overwrites_a_given_slot():
    cur = {"a": "#e0913f"}
    assert so.merge_palette(cur, {"a": "#ff9500"}) == {"a": "#ff9500"}


def test_merge_palette_clears_a_slot_sent_empty():
    cur = {"a": "#e0913f", "g": "#ffa548"}
    assert so.merge_palette(cur, {"g": ""}) == {"a": "#e0913f"}


def test_merge_palette_returns_none_when_nothing_is_left():
    assert so.merge_palette({"a": "#e0913f"}, {"a": ""}) is None
    assert so.merge_palette(None, {}) is None


def test_merge_palette_survives_a_partial_hand_written_request():
    # The exact shape of a curl that only sets the accent: everything else the
    # speaker already had must still be there afterwards.
    cur = {"a": "#f0cb7b", "g": "#ffa548", "d": "#6c5b37",
           "p": "#f4efe0", "s": "#a89e89", "e": "#c73e2c"}
    out = so.merge_palette(cur, {"a": "#e0913f"})
    assert out["a"] == "#e0913f"
    assert len(out) == 6 and out["d"] == "#6c5b37"
