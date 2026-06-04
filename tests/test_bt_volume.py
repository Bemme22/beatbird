"""BlueALSA volume packing — pure math, no D-Bus needed.

BlueALSA packs L/R volume into a uint16 as (L<<8)|R, each byte 0..127. These
conversions are the part of sources/bluetooth.py that survives the dbus-fast
migration unchanged, so pin them down now.
"""

from beatbird.sources.bluetooth import _vol_raw_to_pct, _vol_pct_to_raw


def test_raw_to_pct_extremes():
    assert _vol_raw_to_pct(0) == 0
    assert _vol_raw_to_pct((127 << 8) | 127) == 100


def test_raw_to_pct_midpoint():
    assert _vol_raw_to_pct((64 << 8) | 64) == 50


def test_raw_to_pct_masks_high_bit():
    # Top bit of each byte is ignored (mask 0x7F) — 0xFFFF must read as full.
    assert _vol_raw_to_pct(0xFFFF) == 100


def test_raw_to_pct_averages_l_r():
    # L=127, R=0 → avg 63 → ~50 %.
    assert _vol_raw_to_pct((127 << 8) | 0) == round(63 * 100 / 127)


def test_pct_to_raw_extremes():
    assert _vol_pct_to_raw(0) == 0
    assert _vol_pct_to_raw(100) == (127 << 8) | 127


def test_pct_to_raw_clamps():
    assert _vol_pct_to_raw(150) == (127 << 8) | 127
    assert _vol_pct_to_raw(-10) == 0


def test_roundtrip_is_near_identity():
    # pct -> raw -> pct should land within 1 % (byte quantisation).
    for pct in (0, 10, 25, 50, 75, 90, 100):
        assert abs(_vol_raw_to_pct(_vol_pct_to_raw(pct)) - pct) <= 1
