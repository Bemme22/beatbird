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


class LouderHatPlus1X(HardwareInterface):
    """Single TAS5825M stereo."""

    driver_name = "louder-hat-plus-1x"

    def __init__(self, primary: int = 0x4c):
        self.primary = primary

    def read_status(self) -> dict[str, str]:
        return {"stereo": _read_fault(self.primary)}


class LouderHatTriple(HardwareInterface):
    """3× TAS5825M for Lounge — stub until custom DT overlay is available."""

    driver_name = "louder-hat-triple"

    def __init__(self, primary: int = 0x4c, secondary: int = 0x4d, tertiary: int = 0x4e):
        self.addrs = {"left": primary, "right": secondary, "sub": tertiary}

    def read_status(self) -> dict[str, str]:
        return {name: _read_fault(a) for name, a in self.addrs.items()}


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
