"""TAS amp power-state (deep-sleep) command construction.

Guards the I2C sequence the bridge uses to idle the amp: book 0 → page 0 →
DEVICE_CTRL2. The book/page-first ordering matters — without it a stray DSP
page would turn a power-state write into a coefficient corruption.
"""

from types import SimpleNamespace
from unittest import mock

from beatbird.hardware import louder_hat as lh


def _ok(*_a, **_k):
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def _i2cset_calls(run_mock):
    """Extract (addr, reg, val) hex triples from i2cset invocations."""
    out = []
    for c in run_mock.call_args_list:
        argv = c.args[0]
        if argv[0] == "i2cset":
            # i2cset -f -y <bus> <addr> <reg> <val>
            out.append((argv[4], argv[5], argv[6]))
    return out


def test_sleep_writes_deep_sleep_after_book_page():
    hw = lh.LouderHatPlus1X(primary=0x4c)
    with mock.patch.object(lh.subprocess, "run", side_effect=_ok) as run:
        assert hw.sleep() is True
    calls = _i2cset_calls(run)
    # book 0, page 0, then DEVICE_CTRL2 = deep sleep (0x00)
    assert calls == [("0x4c", "0x7f", "0x0"), ("0x4c", "0x0", "0x0"), ("0x4c", "0x3", "0x0")]


def test_wake_writes_play():
    hw = lh.LouderHatPlus1X(primary=0x4c)
    with mock.patch.object(lh.subprocess, "run", side_effect=_ok) as run:
        assert hw.wake() is True
    assert _i2cset_calls(run)[-1] == ("0x4c", "0x3", "0x3")  # CTRL_STATE = play


def test_dual_amp_drives_both_addresses():
    hw = lh.LouderHatPlus2X(primary=0x4c, secondary=0x4d)
    with mock.patch.object(lh.subprocess, "run", side_effect=_ok) as run:
        assert hw.sleep() is True
    addrs = {addr for addr, _reg, _val in _i2cset_calls(run)}
    assert addrs == {"0x4c", "0x4d"}


def test_sleep_reports_failure_on_i2c_error():
    hw = lh.LouderHatPlus1X(primary=0x4c)
    fail = SimpleNamespace(returncode=1, stdout="", stderr="Remote I/O error")
    with mock.patch.object(lh.subprocess, "run", return_value=fail):
        assert hw.sleep() is False
