"""Serial-protocol field escape — guards the ESP32 parser against
pathological track titles.

Bug it prevents: a title containing '|' (e.g. "Killing in the Name |
Live") would be split by the ESP32 parser as if the second half were
a new KEY:value field, dropping trailing fields like LV: and TM:.
Newline in metadata (rare but seen with some podcast feeds) would
terminate the line early and crash the parser even harder."""

from beatbird.display.amoled import AmoledDisplay


def test_pipe_replaced_with_em_dash():
    out = AmoledDisplay._esc_field("Killing in the Name | Live")
    assert "|" not in out
    assert "—" in out


def test_newline_replaced_with_space():
    out = AmoledDisplay._esc_field("foo\nbar")
    assert "\n" not in out
    assert "foo bar" == out


def test_carriage_return_replaced():
    out = AmoledDisplay._esc_field("foo\r\nbar")
    assert "\r" not in out
    assert "\n" not in out


def test_normal_title_unchanged():
    """The 99.9 % case: no special chars, no transformation."""
    title = "Hey Jude"
    assert AmoledDisplay._esc_field(title) == title


def test_empty_string_passes_through():
    assert AmoledDisplay._esc_field("") == ""


def test_none_safe():
    """Some sources return None instead of '' for missing metadata."""
    assert AmoledDisplay._esc_field(None) == ""
