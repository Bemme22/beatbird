"""
bluetooth_bus.py — a persistent asyncio MessageBus on a daemon thread, with a
synchronous facade. The migration target for the subprocess calls in
sources/bluetooth.py (docs/bluetooth-dbus-fast.md, Option B).

Step 1 of the migration: plumbing only. Nothing in the codebase calls this yet —
the read-paths (GetManagedObjects, BlueALSA PCM/volume) port onto it next.

Why a dedicated loop thread: dbus-fast is async-only, the rest of BeatBird is
synchronous. ``asyncio.run()`` per call would tear down and reconnect the bus
every time (slow + racy). Instead we run ONE event loop on a background daemon
thread, connect the system bus once, and bridge sync callers to it via
``run_coroutine_threadsafe(...).result(timeout)``.

Lazily started: the thread and the bus come up on first use, so importing this
module costs nothing — and ``dbus_fast`` is imported lazily inside ``_connect``
so the package still imports on a box without it (e.g. CI).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Awaitable, Optional, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

_DEFAULT_TIMEOUT = 4.0


class BluetoothBus:
    """Owns one asyncio event loop on a dedicated daemon thread plus a single
    long-lived dbus-fast system-bus connection, and bridges sync callers to it.

    Use :meth:`instance` for the app-wide singleton; construct directly in
    tests so each test gets an isolated loop thread (call :meth:`close` after).
    """

    _instance: "BluetoothBus | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._bus: Any = None                 # dbus_fast.aio.MessageBus
        self._start_lock = threading.Lock()

    # ─── Singleton ──────────────────────────────────────────────────────────

    @classmethod
    def instance(cls) -> "BluetoothBus":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ─── Loop thread ────────────────────────────────────────────────────────

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Start the daemon loop thread once; return the running loop."""
        loop = self._loop
        if loop is not None and loop.is_running():
            return loop
        with self._start_lock:
            loop = self._loop
            if loop is not None and loop.is_running():
                return loop
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _run_loop() -> None:
                asyncio.set_event_loop(loop)
                loop.call_soon(ready.set)
                loop.run_forever()

            t = threading.Thread(target=_run_loop, name="beatbird-btbus", daemon=True)
            t.start()
            if not ready.wait(timeout=5.0):
                raise RuntimeError("bluetooth bus loop thread failed to start")
            self._loop = loop
            self._thread = t
            return loop

    def submit(self, coro: Awaitable[T], timeout: float = _DEFAULT_TIMEOUT) -> T:
        """Run an awaitable on the bus loop from a sync caller and block for its
        result (up to ``timeout`` seconds). The sync↔async bridge every facade
        method goes through. Raises ``TimeoutError`` if the coroutine overruns;
        any exception the coroutine raises propagates to the caller."""
        loop = self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout)

    # ─── Bus connection ─────────────────────────────────────────────────────

    async def _connect(self) -> Any:
        """Connect to the system bus, reusing the existing connection unless it
        dropped. Runs on the loop thread. Imports dbus_fast lazily so the module
        stays importable without it."""
        bus = self._bus
        if bus is not None and getattr(bus, "connected", False):
            return bus
        from dbus_fast.aio import MessageBus
        from dbus_fast.constants import BusType
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        log.info("bluetooth: dbus-fast system bus connected")
        return self._bus

    def bus(self, timeout: float = _DEFAULT_TIMEOUT) -> Any:
        """Sync accessor for the connected ``MessageBus`` (connecting on first
        use). Facade methods use this to fetch proxy objects."""
        return self.submit(self._connect(), timeout)

    # ─── Teardown ───────────────────────────────────────────────────────────

    def close(self) -> None:
        """Disconnect the bus and stop the loop thread. Idempotent. Mainly for
        tests and clean shutdown — in normal operation the daemon thread just
        dies with the process."""
        loop = self._loop
        if loop is None:
            return

        async def _disc() -> None:
            if self._bus is not None:
                try:
                    self._bus.disconnect()
                except Exception:
                    pass
                self._bus = None

        try:
            asyncio.run_coroutine_threadsafe(_disc(), loop).result(2.0)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._loop = None
        self._thread = None
