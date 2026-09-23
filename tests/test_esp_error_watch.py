"""Display-ESP I²C error watch — decides when a touch-controller hang warrants
an ESP32 reset.

Found 2026-09-23 on Zipp Mini 2: touch dead for 18 days while the ESP kept
rendering and sending [hb], so the heartbeat watchdog never fired. The only
signal were these error lines, logged at DEBUG as "unknown RX".
"""

from beatbird.display.amoled import AmoledDisplay, EspErrorWatch, is_i2c_error

# Verbatim from Zipp's serial port, 2026-09-23.
REAL_LINES = [
    "E (1568421583) i2c.master: I2C transaction unexpected nack detected",
    "E (1568421584) i2c.master: s_i2c_synchronous_transaction(945): I2C transaction failed",
    "E (1568421586) i2c.master: i2c_master_transmit_receive(1241): I2C transaction failed",
    "[1568421595][E][esp32-hal-i2c-ng.c:359] i2cWriteReadNonStop(): "
    "i2c_master_transmit_receive failed: [259] ESP_ERR_INVALID_STATE",
    "[1568421617][E][Wire.cpp:516] requestFrom(): i2cWriteReadNonStop returned Error 259",
]


def test_real_zipp_lines_are_recognised():
    assert all(is_i2c_error(line) for line in REAL_LINES)


def test_protocol_and_other_errors_are_not_i2c():
    for line in ("[hb] t=123 heap=4567", "FW:fw-v0.9.15", "CMD:PLAYPAUSE",
                 "VOL:40", "E (12) spi_master: dma error",
                 "[12][E][WiFi.cpp:1] nope", "[boot]", "info: i2c scan ok"):
        assert not is_i2c_error(line), line


def test_single_glitch_does_not_reset():
    w = EspErrorWatch(threshold=15, window_s=300)
    # One hang cycle = 5 lines; a transient glitch must not reset the ESP.
    assert not any(w.feed(100.0 + i * 0.01) for i in range(5))


def test_sustained_errors_reset_once_then_wait():
    w = EspErrorWatch(threshold=15, window_s=300, min_interval_s=900)
    fired = [w.feed(float(t)) for t in range(0, 60, 3)]   # 20 lines in 60 s
    assert fired.count(True) == 1
    # Still failing right after the reset → no second reset inside 15 min.
    assert not any(w.feed(float(t)) for t in range(60, 900, 3))
    # After the interval it may try again.
    assert any(w.feed(float(t)) for t in range(960, 1100, 3))


def test_errors_spread_beyond_window_do_not_accumulate():
    w = EspErrorWatch(threshold=15, window_s=300)
    # 1 line every 30 s → at most 10 inside any 300-s window.
    assert not any(w.feed(float(t)) for t in range(0, 3600, 30))


def test_reset_budget_gives_up():
    w = EspErrorWatch(threshold=2, window_s=300, min_interval_s=10,
                      max_resets=3, budget_window_s=3600)
    resets = sum(w.feed(float(t)) for t in range(0, 600))
    assert resets == 3
    assert w.budget_exhausted(600.0)
    # Budget refills once the old resets age out.
    assert not w.budget_exhausted(600.0 + 3600)


class _FakeSerial:
    def __init__(self):
        self.is_open = True
        self.dtr = True
        self.rts = True
        self.trace = []
        self.closed = False

    def __setattr__(self, k, v):
        if k in ("dtr", "rts") and "trace" in self.__dict__:
            self.trace.append((k, v))
        object.__setattr__(self, k, v)

    def close(self):
        self.closed = True
        self.is_open = False


def test_hard_reset_pulls_en_via_rts_with_dtr_low(monkeypatch):
    monkeypatch.setattr("beatbird.display.amoled.time.sleep", lambda s: None)
    d = AmoledDisplay(reset_on_start=False)
    fake = _FakeSerial()
    d.ser = fake
    assert d.hard_reset("test")
    # DTR low first (else RTS+DTR both high = no reset on USB-Serial-JTAG),
    # then RTS pulse.
    assert fake.trace == [("dtr", False), ("rts", True), ("rts", False)]
    assert fake.closed and d.ser is None


def test_burst_of_real_lines_triggers_reset(monkeypatch):
    monkeypatch.setattr("beatbird.display.amoled.time.sleep", lambda s: None)
    d = AmoledDisplay(reset_on_start=False)
    fake = _FakeSerial()
    d.ser = fake
    for _ in range(3):                     # three hang cycles = 15 lines
        for line in REAL_LINES:
            if d.ser is None:
                break
            d._handle_rx(line)
    assert fake.closed, "15 I2C error lines should have reset the ESP"
