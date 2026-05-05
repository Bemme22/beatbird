"""Small helpers for reading Linux-level system stats."""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger("beatbird.system")


def cpu_temp() -> float:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return -1.0


def wifi_rssi() -> int:
    """Signal strength in dBm, or 0 if unavailable."""
    try:
        with open("/proc/net/wireless") as f:
            lines = f.readlines()
            if len(lines) >= 3:
                parts = lines[2].split()
                return int(float(parts[3].rstrip(".")))
    except Exception:
        pass
    return 0


def service_active(name: str) -> bool:
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "--quiet", name], timeout=3,
        )
        return r.returncode == 0
    except Exception:
        return False
