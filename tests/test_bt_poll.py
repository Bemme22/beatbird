"""BluetoothSource.poll() must not talk to BlueALSA while nothing is connected.

The GetPCMs call is only ever consumed inside the per-device loop, so with an
empty device list it is pure cost: a busctl fork every 2 s plus a debug line in
bluealsa's journal (on beatpi that was two thirds of a RAM-backed journal).
These tests pin the skip down — and, on the other side, that a connected device
still gets its PCM properties, so the guard can't quietly disable volume sync.

No D-Bus here: every shell-out the poll makes is monkeypatched.
"""

import pytest

from beatbird.sources import bluetooth as bt


MAC = "AA:BB:CC:DD:EE:FF"
PCM_PATH = "/org/bluealsa/hci0/dev_AA_BB_CC_DD_EE_FF/a2dpsnk/source"


@pytest.fixture
def calls(monkeypatch):
    """Stub out every subprocess the poll makes; count the GetPCMs calls."""
    counter = {"getpcms": 0}

    def fake_get_pcms():
        counter["getpcms"] += 1
        return {
            PCM_PATH: {
                "Device": "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
                "Transport": "A2DP-sink",
                "Mode": "source",
                "Volume": (64 << 8) | 64,       # → 50 %
            }
        }

    monkeypatch.setattr(bt, "_get_bluealsa_pcms", fake_get_pcms)
    monkeypatch.setattr(bt, "_is_streaming", lambda mac: False)
    monkeypatch.setattr(bt, "set_trusted", lambda mac, on: True)
    return counter


def test_no_device_skips_getpcms(calls, monkeypatch):
    monkeypatch.setattr(bt, "_list_connected_devices", lambda: [])

    state = bt.BluetoothSource().poll()

    assert calls["getpcms"] == 0
    assert state.devices == []


def test_connected_device_still_reads_pcm(calls, monkeypatch):
    monkeypatch.setattr(
        bt, "_list_connected_devices",
        lambda: [bt.BTDevice(mac=MAC, alias="Phone", connected=True)],
    )

    state = bt.BluetoothSource().poll()

    assert calls["getpcms"] == 1
    assert state.devices[0].pcm_path == PCM_PATH
    assert state.devices[0].volume_pct == 50
