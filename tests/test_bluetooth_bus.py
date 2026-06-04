"""BluetoothBus plumbing — the sync↔async bridge (dbus-fast migration, step 1).

Exercises the loop-thread + submit() facade with plain coroutines, so it needs
no dbus-fast and no real D-Bus. The actual BlueZ calls port onto this next."""

import asyncio
import threading

import pytest

from beatbird.sources.bluetooth_bus import BluetoothBus


def test_submit_runs_coroutine_and_returns_result():
    bus = BluetoothBus()
    try:
        async def add():
            return 40 + 2
        assert bus.submit(add()) == 42
    finally:
        bus.close()


def test_submit_runs_on_a_separate_daemon_thread():
    bus = BluetoothBus()
    try:
        async def who():
            return threading.get_ident()
        worker = bus.submit(who())
        assert worker != threading.get_ident()
        assert bus._thread is not None and bus._thread.daemon
    finally:
        bus.close()


def test_submit_propagates_exceptions():
    bus = BluetoothBus()
    try:
        async def boom():
            raise ValueError("nope")
        with pytest.raises(ValueError, match="nope"):
            bus.submit(boom())
    finally:
        bus.close()


def test_submit_timeout_raises():
    bus = BluetoothBus()
    try:
        async def slow():
            await asyncio.sleep(2.0)
        with pytest.raises(TimeoutError):
            bus.submit(slow(), timeout=0.1)
    finally:
        bus.close()


def test_lazy_no_thread_before_first_use():
    bus = BluetoothBus()
    try:
        assert bus._loop is None
        assert bus._thread is None
    finally:
        bus.close()


def test_loop_is_reused_across_calls():
    bus = BluetoothBus()
    try:
        async def noop():
            return 1
        bus.submit(noop())
        loop1 = bus._loop
        bus.submit(noop())
        assert bus._loop is loop1            # same loop, not restarted per call
    finally:
        bus.close()


def test_close_is_idempotent():
    bus = BluetoothBus()
    bus.close()      # never started — must not raise
    async def noop():
        return 1
    bus.submit(noop())
    bus.close()
    bus.close()      # double close — must not raise
    assert bus._loop is None


def test_instance_is_singleton():
    assert BluetoothBus.instance() is BluetoothBus.instance()
