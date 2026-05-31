"""
beatbird.hardware.louder_hat — status reader for Louder Hat Plus (TAS5825M).

Reads the CHAN_FAULT register (0x78) via ``i2cget -f`` and translates the bit
pattern into a human-readable status string.
"""

from __future__ import annotations

import logging
import subprocess

from beatbird.hardware.base import HardwareInterface

log = logging.getLogger("beatbird.hw.louder_hat")

TAS_I2C_BUS = 1
REG_CHAN_FAULT = 0x78

# Power management. The TAS58xx control registers live on book 0 / page 0;
# DEVICE_CTRL2 (0x03) CTRL_STATE[1:0] selects the run state. We measured a
# Louder Hat Plus 1X drop from 4 W → 2 W moving the chip play→deep-sleep with
# no signal — verified the driver leaves 0x03 alone afterwards (doesn't fight
# the write) and audio resumes cleanly on the way back. We force book/page 0
# before the write so a stray DSP page can never turn the state write into a
# coefficient corruption.
REG_BOOK = 0x7f
REG_PAGE = 0x00
REG_DEVICE_CTRL2 = 0x03
CTRL_STATE_PLAY = 0x03
CTRL_STATE_DEEP_SLEEP = 0x00


def _set_power_state(addr: int, state: int) -> bool:
    """Write DEVICE_CTRL2 CTRL_STATE on one TAS chip (book/page 0 first)."""
    for reg, val in ((REG_BOOK, 0x00), (REG_PAGE, 0x00), (REG_DEVICE_CTRL2, state)):
        try:
            r = subprocess.run(
                ["i2cset", "-f", "-y", str(TAS_I2C_BUS), hex(addr), hex(reg), hex(val)],
                capture_output=True, text=True, timeout=3,
            )
        except Exception as e:
            log.debug("i2cset @%#x reg %#x failed: %s", addr, reg, e)
            return False
        if r.returncode != 0:
            log.warning("i2cset @%#x reg %#x=%#x failed: %s",
                        addr, reg, val, r.stderr.strip() or "(no output)")
            return False
    return True


def _set_all(addrs, state: int) -> bool:
    """Drive every amp address to a power state; attempt all, succeed iff all do."""
    return all([_set_power_state(a, state) for a in addrs])


def _read_fault(addr: int) -> str:
    """Read one TAS5825M CHAN_FAULT register and decode the common faults."""
    try:
        result = subprocess.run(
            ["i2cget", "-f", "-y", str(TAS_I2C_BUS), hex(addr), hex(REG_CHAN_FAULT)],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0:
            return "error"
        val = int(result.stdout.strip(), 16)
    except Exception as e:
        log.debug("i2cget @%#x failed: %s", addr, e)
        return "error"

    if val == 0x00:
        return "ok"
    faults = []
    if val & 0x30: faults.append("OT")   # Over-temperature
    if val & 0x03: faults.append("OC")   # Over-current
    if val & 0x0C: faults.append("DC")   # DC fault
    return ",".join(faults) if faults else f"fault(0x{val:02x})"


class LouderHatPlus2X(HardwareInterface):
    """Dual TAS5825M: stereo amp at 0x4C, sub amp at 0x4D."""

    driver_name = "louder-hat-plus-2x"

    def __init__(self, primary: int = 0x4c, secondary: int = 0x4d):
        self.primary = primary
        self.secondary = secondary

    def read_status(self) -> dict[str, str]:
        return {
            "stereo": _read_fault(self.primary),
            "sub":    _read_fault(self.secondary),
        }

    def sleep(self) -> bool:
        return _set_all([self.primary, self.secondary], CTRL_STATE_DEEP_SLEEP)

    def wake(self) -> bool:
        return _set_all([self.primary, self.secondary], CTRL_STATE_PLAY)


class LouderHatPlus1X(HardwareInterface):
    """Single TAS5825M stereo."""

    driver_name = "louder-hat-plus-1x"

    def __init__(self, primary: int = 0x4c):
        self.primary = primary

    def read_status(self) -> dict[str, str]:
        return {"stereo": _read_fault(self.primary)}

    def sleep(self) -> bool:
        return _set_all([self.primary], CTRL_STATE_DEEP_SLEEP)

    def wake(self) -> bool:
        return _set_all([self.primary], CTRL_STATE_PLAY)


class LouderHatTriple(HardwareInterface):
    """3× TAS5825M for Lounge — stub until custom DT overlay is available."""

    driver_name = "louder-hat-triple"

    def __init__(self, primary: int = 0x4c, secondary: int = 0x4d, tertiary: int = 0x4e):
        self.addrs = {"left": primary, "right": secondary, "sub": tertiary}

    def read_status(self) -> dict[str, str]:
        return {name: _read_fault(a) for name, a in self.addrs.items()}

    def sleep(self) -> bool:
        return _set_all(list(self.addrs.values()), CTRL_STATE_DEEP_SLEEP)

    def wake(self) -> bool:
        return _set_all(list(self.addrs.values()), CTRL_STATE_PLAY)


def from_profile(soundcard) -> HardwareInterface:
    """Factory — pick the right hardware driver for a Soundcard profile section."""
    driver = soundcard.driver
    if driver == "louder-hat-plus-2x":
        return LouderHatPlus2X(
            primary=soundcard.primary_i2c or 0x4c,
            secondary=soundcard.secondary_i2c or 0x4d,
        )
    if driver == "louder-hat-plus-1x":
        return LouderHatPlus1X(primary=soundcard.primary_i2c or 0x4c)
    if driver == "louder-hat-triple":
        return LouderHatTriple(
            primary=soundcard.primary_i2c or 0x4c,
            secondary=soundcard.secondary_i2c or 0x4d,
            tertiary=soundcard.tertiary_i2c or 0x4e,
        )
    # Innomaker and anything else: no queryable status yet
    from beatbird.hardware.base import NullHardware
    return NullHardware()
