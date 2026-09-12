"""FaceStore: what HA publishes must survive the trip to the panel intact.

The store sits on the MQTT callback thread and feeds a line-based ASCII
protocol, so the tests pin down three things in particular: a bad publisher
cannot break the parser (delimiters, non-JSON, silly ttl), a re-publish of
unchanged state does not churn the display, and expiry beats priority.
"""

import pytest

from beatbird.ha.faces import (
    DEFAULT_TTL_S,
    MAX_TTL_S,
    FaceStore,
    face_id_from_topic,
)


def payload(**kw) -> str:
    import json
    base = {"big": "3.4", "ttl": 600}
    base.update(kw)
    return json.dumps(base)


# ─── Ingest ─────────────────────────────────────────────────────────────────

def test_face_is_stored_and_rendered():
    s = FaceStore()
    assert s.update("luften", payload(prio=40, unit="K", top="LÜFTEN",
                                      bot="außen 9.8 · innen 13.2"), now=0)
    line = s.active(now=1)[0].line()
    assert line.startswith("FACE:id=luften|prio=40|big=3.4|unit=K|")
    # Umlauts folded to digraphs — the firmware's split-flap animates
    # byte-by-byte and would tear multi-byte UTF-8.
    assert "top=LUEFTEN" in line
    assert "aussen 9.8 - innen 13.2" in line


def test_empty_payload_clears_the_face():
    s = FaceStore()
    s.update("luften", payload(), now=0)
    assert len(s) == 1
    assert s.update("luften", "", now=1)          # retained-delete idiom
    assert len(s) == 0


def test_ttl_zero_clears_the_face():
    s = FaceStore()
    s.update("wm", payload(), now=0)
    assert s.update("wm", payload(ttl=0), now=1)
    assert len(s) == 0


@pytest.mark.parametrize("bad", ["not json", "[1,2]", "null", '"text"', "{"])
def test_malformed_payload_is_ignored_not_raised(bad):
    s = FaceStore()
    s.update("keep", payload(), now=0)
    assert s.update("keep", bad, now=1) is False
    assert len(s) == 1                            # previous value survives


@pytest.mark.parametrize("bad_big", [":washer:", "ON", "12x", "n/a"])
def test_unrenderable_big_value_is_rejected(bad_big):
    # inter_clock is a digits+colon SUBSET font: letters have no glyph and LVGL
    # draws hollow boxes, which reads as a broken panel. Catch it on the Pi,
    # where it is a log line instead of an undebuggable display.
    s = FaceStore()
    assert s.update("x", payload(big=bad_big), now=0) is False
    assert len(s) == 0


@pytest.mark.parametrize("good_big", ["-3.9", "+12", "2:14", "21.5", "180"])
def test_renderable_big_values_pass(good_big):
    s = FaceStore()
    assert s.update("x", payload(big=good_big), now=0) is True


def test_face_without_big_value_is_rejected():
    # The whole concept is "one far-readable value per face" — a face without
    # one is a publisher bug, not a display state.
    s = FaceStore()
    assert s.update("empty", payload(big=""), now=0) is False
    assert len(s) == 0


def test_delimiters_in_text_cannot_desync_the_parser():
    s = FaceStore()
    s.update("x", payload(top="a|b", bot="c\nd|e"), now=0)
    line = s.active(now=0)[0].line()
    assert line.count("|") == len([p for p in line.split("|")]) - 1
    assert "top=a b" in line and "bot=c d e" in line


def test_ttl_is_capped():
    s = FaceStore()
    s.update("x", payload(ttl=10 ** 9), now=0)
    assert s.active(now=0)[0].deadline == pytest.approx(MAX_TTL_S)


def test_missing_ttl_falls_back_to_default():
    import json
    s = FaceStore()
    s.update("x", json.dumps({"big": "1"}), now=0)
    assert s.active(now=0)[0].deadline == pytest.approx(DEFAULT_TTL_S)


# ─── Churn ──────────────────────────────────────────────────────────────────

def test_republishing_unchanged_state_reports_no_change():
    # HA re-sends retained state on every reconnect; that must not restart the
    # rotation or flash the panel.
    s = FaceStore()
    assert s.update("luften", payload(prio=40), now=0) is True
    assert s.update("luften", payload(prio=40), now=30) is False


def test_changed_value_reports_a_change():
    s = FaceStore()
    s.update("luften", payload(big="3.4"), now=0)
    assert s.update("luften", payload(big="4.1"), now=30) is True


# ─── Expiry and ordering ────────────────────────────────────────────────────

def test_face_expires_after_ttl():
    s = FaceStore()
    s.update("wm", payload(ttl=600), now=0)
    assert len(s.active(now=599)) == 1
    assert s.active(now=601) == []


def test_higher_priority_sorts_first_ties_broken_stably():
    s = FaceStore()
    s.update("b_low", payload(prio=10), now=0)
    s.update("a_high", payload(prio=70), now=0)
    s.update("a_low", payload(prio=10), now=0)
    assert [f.id for f in s.active(now=0)] == ["a_high", "a_low", "b_low"]


def test_expiry_beats_priority_when_evicting():
    # A stale high-prio face must never squeeze out a fresh low-prio one.
    s = FaceStore(max_faces=1)
    s.update("stale_high", payload(prio=90, ttl=10), now=0)
    s.update("fresh_low", payload(prio=1, ttl=600), now=20)
    assert [f.id for f in s.active(now=20)] == ["fresh_low"]


def test_store_stays_bounded():
    s = FaceStore(max_faces=3)
    for i in range(10):
        s.update(f"f{i}", payload(prio=i), now=0)
    active = s.active(now=0)
    assert len(active) == 3
    assert [f.id for f in active] == ["f9", "f8", "f7"]   # lowest prio dropped


# ─── Wire form ──────────────────────────────────────────────────────────────

def test_lines_are_terminated_so_the_firmware_can_drop_stale_faces():
    s = FaceStore()
    s.update("a", payload(prio=50), now=0)
    s.update("b", payload(prio=10), now=0)
    lines = s.lines(now=0)
    assert len(lines) == 3
    assert lines[0].startswith("FACE:id=a")
    assert lines[-1].startswith("FACE:end")


def test_empty_store_still_sends_the_terminator():
    # Otherwise a speaker whose last face just cleared would keep showing it.
    assert FaceStore(dwell_s=8).lines(now=0) == ["FACE:end|dwell=8"]


# ─── Topic mapping ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("topic,expected", [
    ("beatbird/hints/luften", "luften"),
    ("beatbird/hints/", None),
    ("beatbird/hints", None),
    ("beatbird/hints/a/b", None),          # someone else's namespace
    ("other/hints/luften", None),
])
def test_face_id_from_topic(topic, expected):
    assert face_id_from_topic(topic, "beatbird/hints") == expected


def test_trailing_slash_in_configured_prefix_is_tolerated():
    assert face_id_from_topic("beatbird/hints/x", "beatbird/hints/") == "x"


# ─── Firmware budgets ───────────────────────────────────────────────────────
# These limits are not style, they are DRAM: every char is multiplied by
# MAX_FACES and by the firmware's two staging sets. The first version of the
# face code overflowed dram0_0_seg by 352 bytes in every ESP32 env, and nothing
# caught it — the simulator has no such region and no test connected the two
# sides. Parsing the header is ugly; discovering the mismatch as a linker error
# on a pushed branch is uglier.

def _firmware_constants() -> dict[str, int]:
    import re
    from pathlib import Path

    header = Path(__file__).resolve().parents[1] / (
        "firmware/amoled-1.43/include/faces.h")
    found = dict(re.findall(r"constexpr int (\w+)\s*=\s*(\d+)", header.read_text()))
    return {k: int(v) for k, v in found.items()}


@pytest.mark.parametrize("py_name,fw_name", [
    ("MAX_BIG", "LEN_BIG"),
    ("MAX_UNIT", "LEN_UNIT"),
    ("MAX_TOP", "LEN_TOP"),
    ("MAX_BOT", "LEN_BOT"),
])
def test_field_budgets_match_the_firmware_buffers(py_name, fw_name):
    """The Pi truncates; the firmware's buffer must hold that plus a NUL."""
    from beatbird.ha import faces as mod

    fw = _firmware_constants()
    assert fw[fw_name] == getattr(mod, py_name) + 1, (
        f"{py_name} and {fw_name} drifted apart — the panel would silently "
        f"cut text the Pi considered fine")


def test_the_pi_never_sends_more_faces_than_the_firmware_can_hold():
    # Otherwise the surplus is dropped on the ESP32 without a word, and the
    # bridge logs a set it did not actually show.
    from beatbird.ha import faces as mod

    assert mod.MAX_FACES == _firmware_constants()["MAX_FACES"]


def test_the_firmware_does_not_store_what_it_never_draws():
    # id and prio are consumed by the bridge (addressing, ordering). Storing
    # them cost 216 bytes of the scarcest memory in this project for data no
    # screen reads; see include/faces.h.
    fw = _firmware_constants()
    assert "LEN_ID" not in fw


def test_the_face_set_fits_the_dram_budget():
    # Two staging sets of MAX_FACES entries. 1248 bytes was the version that
    # overflowed; this keeps the successor honest if someone adds a field.
    fw = _firmware_constants()
    per_face = fw["LEN_BIG"] + fw["LEN_UNIT"] + fw["LEN_TOP"] + fw["LEN_BOT"]
    assert 2 * fw["MAX_FACES"] * per_face <= 560


# ─── Icons ──────────────────────────────────────────────────────────────────
# An icon may replace the number when the event itself is the message. The
# firmware draws them as geometry, so the vocabulary is closed on both sides.

def test_icon_alone_is_enough():
    s = FaceStore()
    assert s.update("waschmaschine", payload(big="", icon="wash", top="WAESCHE"))
    line = s.lines(now=0)[0]
    assert "icon=wash" in line
    assert "big=" not in line       # nothing to decode in the hero slot


def test_icon_and_number_can_coexist_on_the_wire():
    # The firmware prefers the icon; the number stays available for the detail
    # line's sake and for a future layout, so the Pi does not drop it.
    s = FaceStore()
    assert s.update("x", payload(big="21", icon="bolt", unit="kWh"))
    line = s.lines(now=0)[0]
    assert "icon=bolt" in line and "big=21" in line


def test_unknown_icon_is_refused_not_silently_dropped():
    # Two hops away an unknown name is an empty hero slot nobody can explain.
    s = FaceStore()
    assert not s.update("x", payload(big="", icon="teapot"))


def test_a_face_with_neither_number_nor_icon_is_refused():
    s = FaceStore()
    assert not s.update("x", payload(big=""))


def test_icon_vocabulary_matches_the_firmware():
    # The firmware maps names in icon_from_name(); a name the Pi allows but the
    # firmware cannot draw would render as an empty slot.
    from pathlib import Path
    import re

    from beatbird.ha.faces import ICONS

    src = Path(__file__).resolve().parents[1] / (
        "firmware/amoled-1.43/src/app/faces.cpp")
    fw = set(re.findall(r'strcmp\(name, "(\w+)"\)', src.read_text()))
    assert fw == set(ICONS)
