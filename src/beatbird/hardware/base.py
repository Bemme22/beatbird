"""
beatbird.hardware.base — abstract hardware interface.

Each soundcard family (Louder Hat, Innomaker) implements this so the bridge
can report amp status to Home Assistant and the display without knowing the
specific chip details.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class HardwareInterface(ABC):
    """Minimal interface every soundcard driver exposes to the bridge."""

    driver_name: str = "unknown"

    @abstractmethod
    def read_status(self) -> dict[str, str]:
        """Return a dict of amp-channel-name → status string.

        Status strings are free-form but should use short tokens:
          "ok"     — normal operation
          "error"  — I2C unreachable, module not loaded, etc.
          "OT"     — over-temperature
          "OC"     — over-current
          "DC"     — DC fault
          "OT,OC"  — multiple
        The bridge just passes them through to MQTT/display.
        """


class NullHardware(HardwareInterface):
    """No-op implementation for speakers without queryable amps."""

    driver_name = "none"

    def read_status(self) -> dict[str, str]:
        return {}
