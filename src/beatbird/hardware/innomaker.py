"""
beatbird.hardware.innomaker — MA12070P status reader (stub).

The MA12070P exposes some status via I2C too; fill in when the card is in
hand and we can verify register addresses. For now we return "ok" as long
as the kernel module is loaded.
"""

from __future__ import annotations

import logging
import subprocess

from beatbird.hardware.base import HardwareInterface

log = logging.getLogger("beatbird.hw.innomaker")


class InnomakerAMPPro(HardwareInterface):
    driver_name = "innomaker-amp-pro"

    def read_status(self) -> dict[str, str]:
        # Minimal: is the card present in aplay -l?
        try:
            out = subprocess.run(
                ["aplay", "-l"], capture_output=True, text=True, timeout=2,
            ).stdout
            return {"stereo": "ok" if "MA12070P" in out or "hifiberry-dac" in out.lower() else "error"}
        except Exception as e:
            log.debug("aplay probe failed: %s", e)
            return {"stereo": "error"}
