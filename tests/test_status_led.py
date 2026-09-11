"""Status-strip wiring: profile schema + the LED: line the bridge pushes.

The point of these tests is the contract that made the strip a profile
setting in the first place: one firmware image serves every speaker, so
pin/count/chip travel over the wire. Two failure modes are worth locking
down — a speaker without a strip must send n=0 (not "nothing", which would
leave a previously configured firmware driving a pin), and a strip pin must
never silently fall back to a default, because GPIO18 is the RobinPi strip
pin but MAIN_I2C_SDA on the 1.43 board.
"""

import pytest
from pydantic import ValidationError

from beatbird.config import Profile
from beatbird.display.amoled import AmoledDisplay


# ─── Profile schema ─────────────────────────────────────────────────────────

def test_status_led_absent_defaults_to_disabled():
    p = Profile.model_validate({"soundcard": {"driver": "louder-hat-plus-1x"}})
    assert p.display.status_led.enabled is False
    assert p.display.status_led.count == 0


def test_status_led_parses():
    p = Profile.model_validate({
        "soundcard": {"driver": "louder-hat-plus-1x"},
        "display": {
            "type": "amoled",
            "status_led": {
                "enabled": True, "pin": 18, "count": 46,
                "chip": "sk6812-rgbw", "brightness": 120, "mapping": "area",
            },
        },
    })
    sl = p.display.status_led
    assert (sl.enabled, sl.pin, sl.count, sl.mapping) == (True, 18, 46, "area")


@pytest.mark.parametrize("bad", [
    {"pin": 99},              # not an ESP32-S3 GPIO
    {"pin": -1},
    {"count": 500},           # beyond any plausible strip
    {"brightness": 300},      # 8-bit cap
    {"chip": "apa102"},       # not a NeoPixel-protocol chip
    {"mapping": "radial"},    # firmware knows area | mirror | bloom
    {"white_mix": 101},       # percent
    {"twinkle_period_s": 1},  # a 1 s cycle would be a strobe, not a star
])
def test_status_led_rejects_nonsense(bad):
    with pytest.raises(ValidationError):
        Profile.model_validate({
            "soundcard": {"driver": "louder-hat-plus-1x"},
            "display": {"type": "amoled", "status_led": {"enabled": True, **bad}},
        })


def test_robinpi_profile_carries_the_verified_wiring():
    """Guards the hardware facts checked on 2026-09-03: GPIO18, 46 pixels,
    GRBW byte order. A silent edit here would light nothing."""
    import yaml
    from pathlib import Path
    data = yaml.safe_load(
        (Path(__file__).parent.parent / "profiles" / "robinpi.yml").read_text(encoding="utf-8")
    )
    sl = Profile.model_validate(data).display.status_led
    assert sl.enabled and sl.pin == 18 and sl.count == 46
    assert sl.chip == "sk6812-rgbw"
    assert sl.mapping == "bloom"    # jeder Balken blueht aus SEINER Mitte auf
    # Calibration measured on 06.09.2026 — see docs/protocol.md. These are not
    # taste settings: wp balances this strip's dies against each other, and
    # without it every warm colour drifts green or white.
    assert sl.white_point == "FF8C64"
    assert sl.white_mix == 45


# ─── LED: line rendering ────────────────────────────────────────────────────

def _capture(status_led):
    """AmoledDisplay with _send stubbed — no serial port involved."""
    d = AmoledDisplay(status_led=status_led)
    sent: list[str] = []
    d._send = sent.append          # type: ignore[method-assign]
    d._send_led_config()
    return sent


def test_led_line_format():
    sent = _capture({
        "enabled": True, "pin": 18, "count": 46,
        "chip": "sk6812-rgbw", "brightness": 120, "mapping": "area",
    })
    assert sent == ["LED:pin=18|n=46|rgbw=1|bri=120|wmix=45|wp=FFFFFF"
                    "|twk=30|map=area|join=inner"]


def test_rgb_chip_clears_the_rgbw_flag():
    sent = _capture({
        "enabled": True, "pin": 17, "count": 12,
        "chip": "ws2812-rgb", "brightness": 60, "mapping": "mirror",
    })
    assert sent == ["LED:pin=17|n=12|rgbw=0|bri=60|wmix=45|wp=FFFFFF"
                    "|twk=30|map=mirror|join=inner"]


def test_disabled_strip_is_sent_as_zero_not_skipped():
    """A speaker that lost its strip in the profile must actively tell the
    firmware to release the pin — silence would leave it driving GPIO."""
    sent = _capture({"enabled": False, "pin": 18, "count": 46})
    assert len(sent) == 1
    assert "n=0" in sent[0]


def test_no_status_led_block_sends_nothing():
    """Speakers whose profile predates the schema stay untouched."""
    d = AmoledDisplay()
    sent: list[str] = []
    d._send = sent.append          # type: ignore[method-assign]
    d._send_led_config()
    assert sent == []


# ─── Calibration fields on the wire (added 09.09.2026) ──────────────────────
# wp and wmix balance one strip's dies; twk carries the starfield's calm axis.
# All three are per-enclosure facts, so they travel with pin/count/chip rather
# than living in the firmware — and they must actually reach it.

def test_calibration_fields_reach_the_wire():
    sent = _capture({
        "enabled": True, "pin": 18, "count": 46, "chip": "sk6812-rgbw",
        "brightness": 40, "mapping": "bloom", "chain_join": "inner",
        "white_point": "FF8C64", "white_mix": 45, "twinkle_period_s": 30,
    })
    assert sent == ["LED:pin=18|n=46|rgbw=1|bri=40|wmix=45|wp=FF8C64"
                    "|twk=30|map=bloom|join=inner"]


def test_white_point_is_normalised_without_the_hash():
    # The profile may carry "#ff8c64"; the wire format is six bare hex chars.
    sent = _capture({
        "enabled": True, "pin": 18, "count": 46, "chip": "sk6812-rgbw",
        "brightness": 40, "mapping": "bloom", "white_point": "#ff8c64",
    })
    assert "|wp=FF8C64|" in sent[0]


def test_defaults_are_the_neutral_ones():
    """A strip that says nothing about colour must be rendered unchanged:
    wp=FFFFFF is 'no correction'. A speaker inheriting someone else's
    calibration would be worse than none."""
    sent = _capture({
        "enabled": True, "pin": 18, "count": 46, "chip": "sk6812-rgbw",
        "brightness": 40, "mapping": "area",
    })
    assert "|wp=FFFFFF|" in sent[0]
