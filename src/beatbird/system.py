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


# ─── Network diagnostics (used by /api/health) ──────────────────────────────
#
# Everything below is "best effort" and time-bounded — the health endpoint
# is supposed to answer fast even when something is broken. No exception
# escapes; failures are reported as the value the field would carry on a
# successful probe (e.g. empty string, 0, False).


def hostname() -> str:
    try:
        with open("/etc/hostname") as f:
            return f.read().strip()
    except Exception:
        return ""


def ip_address() -> str:
    """Primary outgoing IP — same one the default route uses."""
    try:
        r = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=2)
        return r.stdout.strip().split()[0] if r.stdout.strip() else ""
    except Exception:
        return ""


def default_gateway() -> str:
    try:
        with open("/proc/net/route") as f:
            for line in f.read().splitlines()[1:]:
                parts = line.split("\t")
                if len(parts) > 7 and parts[1] == "00000000":
                    # destination 0.0.0.0 — convert hex little-endian to dotted IP
                    hexgw = parts[2]
                    octets = [int(hexgw[i:i+2], 16) for i in (6, 4, 2, 0)]
                    return ".".join(str(o) for o in octets)
    except Exception:
        pass
    return ""


def wifi_ssid() -> str:
    try:
        r = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=2)
        return r.stdout.strip()
    except Exception:
        return ""


def ping(host: str, timeout_s: float = 1.5) -> dict:
    """Single ping, returns {"ok": bool, "rtt_ms": float|None}."""
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", str(int(timeout_s)), host],
            capture_output=True, text=True, timeout=timeout_s + 1,
        )
        if r.returncode != 0:
            return {"ok": False, "rtt_ms": None}
        # parse "time=4.36 ms"
        rtt = None
        for tok in r.stdout.split():
            if tok.startswith("time="):
                try: rtt = float(tok[5:])
                except ValueError: pass
        return {"ok": True, "rtt_ms": rtt}
    except Exception:
        return {"ok": False, "rtt_ms": None}


def tcp_reachable(host: str, port: int, timeout_s: float = 1.5) -> bool:
    """True iff a TCP handshake completes within `timeout_s`."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def http_probe(url: str, timeout_s: float = 2.5) -> dict:
    """HEAD probe — returns {"ok": bool, "code": int, "rtt_ms": float|None}."""
    import time as _t
    import urllib.request
    t0 = _t.monotonic()
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return {"ok": True, "code": r.status, "rtt_ms": (_t.monotonic() - t0) * 1000}
    except urllib.error.HTTPError as e:
        # Server answered (even with error) — that's reachable
        return {"ok": True, "code": e.code, "rtt_ms": (_t.monotonic() - t0) * 1000}
    except Exception:
        return {"ok": False, "code": 0, "rtt_ms": None}


def journal_recent_errors(unit: str, max_lines: int = 30) -> list[str]:
    """Last N error/warning lines from one systemd unit. Used in the
    health page so users see what the bridge has been complaining about
    without an SSH session."""
    try:
        r = subprocess.run(
            ["journalctl", "-u", unit, "-p", "warning", "-n", str(max_lines),
             "--no-pager", "--output=short"],
            capture_output=True, text=True, timeout=3,
        )
        return [l for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        return []
