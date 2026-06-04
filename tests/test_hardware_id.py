"""hardware instance id — the pure serial→short-id hash (identity-split phase 1)."""

import re

from beatbird.system import _short_id_from_serial


def test_short_id_is_4_hex():
    out = _short_id_from_serial("100000003f2a1b2c")
    assert re.fullmatch(r"[0-9a-f]{4}", out)


def test_short_id_is_deterministic():
    assert _short_id_from_serial("abc123") == _short_id_from_serial("abc123")


def test_short_id_ignores_surrounding_whitespace():
    assert _short_id_from_serial("  abc123\n") == _short_id_from_serial("abc123")


def test_short_id_differs_for_different_serials():
    # Different boards must (overwhelmingly likely) get different ids.
    a = _short_id_from_serial("10000000aaaaaaaa")
    b = _short_id_from_serial("10000000bbbbbbbb")
    assert a != b
