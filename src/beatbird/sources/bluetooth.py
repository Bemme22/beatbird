"""
beatbird_bluetooth.py — Bluetooth A2DP source + AVRCP remote + bidirectional
volume sync for Beat #1.

Drop this next to beatbird-bridge.py (in pi/). The bridge imports BluetoothSource,
send_avrcp and disconnect_all_bt from here.

Path/API notes that bit us in earlier versions:
    - BlueALSA exposes A2DP-sink PCMs at paths ending in "/source"
      (from BlueALSA's perspective: what's a sink for BT is a source for ALSA).
      The segment before that is "a2dpsnk" (three letters, not "a2dp-sink").
    - Volume property: uint16 packed as (L << 8) | R, each byte 0..127.
      Confirmed empirically: phone slider at 0% → q 2056 (8/8),
      phone at max → q 32639 (127/127).
    - Official enumeration is via org.bluealsa.Manager1.GetPCMs — we use that
      instead of pattern-matching the busctl tree output.

Volume sync design (2s poll interval, matches Spotify):
    Phone slider moves → bluealsa sees AVRCP → our poll reads Volume →
        on_volume_from_phone(pct) → bridge.set_volume(pct) which calls
        push_volume_to_phone() to close the loop. The _last_pushed_volume
        guard suppresses the echo on the next poll.
    Bridge fader moves → bridge.set_volume(pct) calls push_volume_to_phone()
        directly.

Hard handoff: activating BT triggers on_became_active (spotify_stop in bridge).
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass, field

log = logging.getLogger("beatbird.bt")


@dataclass
class BTDevice:
    mac: str
    alias: str = ""
    connected: bool = False
    streaming: bool = False
    volume_pct: int | None = None   # 0..100, derived from AVRCP 0..127
    pcm_path: str | None = None     # /org/bluealsa/.../source


@dataclass
class BTState:
    devices: list[BTDevice] = field(default_factory=list)

    @property
    def streaming_device(self) -> BTDevice | None:
        for d in self.devices:
            if d.streaming:
                return d
        return None

    @property
    def is_active(self) -> bool:
        return self.streaming_device is not None


# ─── bluetoothctl (for listing + disconnect) ─────────────────────────────────

def _btctl(*commands: str, timeout: float = 3.0) -> str:
    script = "\n".join(commands) + "\nquit\n"
    try:
        r = subprocess.run(
            ["bluetoothctl"],
            input=script, text=True, capture_output=True, timeout=timeout,
        )
        return r.stdout
    except Exception as e:
        log.debug("bluetoothctl: %s", e)
        return ""


_DEV_LINE = re.compile(r"^Device\s+([0-9A-F:]{17})\s+(.*)$", re.I)


def _list_connected_devices() -> list[BTDevice]:
    out = _btctl("devices Connected")
    devs: list[BTDevice] = []
    for line in out.splitlines():
        m = _DEV_LINE.match(line.strip())
        if m:
            devs.append(BTDevice(mac=m.group(1).upper(),
                                 alias=m.group(2).strip(), connected=True))
    return devs


# ─── BlueALSA PCM enumeration (the CORRECT way) ─────────────────────────────
# Using GetPCMs over D-Bus instead of parsing busctl tree output. This gives
# us the dict of {pcm_path: properties} including Volume, Mode, Device, etc.
#
# Output format from `busctl call ... GetPCMs`:
#   a{oa{sv}} <count> "<path>" <propcount> "key" <type> <value> "key" ...
# We shell out and parse with a tolerant regex approach because pydbus/dbus-next
# would be another dependency.

_PCM_ENTRY = re.compile(r'"(/org/bluealsa/[^"]+)"\s+(\d+)\s+(.*?)(?=\s+"/org/bluealsa/|$)',
                        re.DOTALL)


def _get_bluealsa_pcms() -> dict[str, dict]:
    """Return {pcm_path: {property_name: value}} for every active BlueALSA PCM."""
    try:
        r = subprocess.run(
            ["busctl", "--system", "call", "org.bluealsa", "/org/bluealsa",
             "org.bluealsa.Manager1", "GetPCMs"],
            capture_output=True, text=True, timeout=2, check=True,
        )
    except Exception as e:
        log.debug("GetPCMs failed: %s", e)
        return {}

    out = r.stdout.strip()
    # Strip leading signature: a{oa{sv}} <count>
    m = re.match(r"a\{oa\{sv\}\}\s+\d+\s+", out)
    if not m:
        return {}
    body = out[m.end():]

    pcms: dict[str, dict] = {}
    # Each entry: "<path>" <propcount> "<k>" <type> <val> "<k>" <type> <val> ...
    # We can't regex this reliably across all types, so we parse linearly.
    pos = 0
    while pos < len(body):
        path_m = re.match(r'\s*"(/org/bluealsa/[^"]+)"\s+(\d+)\s+', body[pos:])
        if not path_m:
            break
        path = path_m.group(1)
        propcount = int(path_m.group(2))
        pos += path_m.end()

        props: dict = {}
        for _ in range(propcount):
            # "key" <type> <value…>
            key_m = re.match(r'"([^"]+)"\s+(\S+)\s+', body[pos:])
            if not key_m:
                break
            key = key_m.group(1)
            sig = key_m.group(2)
            pos += key_m.end()

            # Parse value based on signature. For our purposes we only care
            # about q (uint16) and s (string) and b (bool); others we skip.
            if sig == "q" or sig == "u" or sig == "y":
                v_m = re.match(r"(\d+)\s*", body[pos:])
                if v_m:
                    props[key] = int(v_m.group(1))
                    pos += v_m.end()
            elif sig == "s" or sig == "o":
                v_m = re.match(r'"([^"]*)"\s*', body[pos:])
                if v_m:
                    props[key] = v_m.group(1)
                    pos += v_m.end()
            elif sig == "b":
                v_m = re.match(r"(true|false)\s*", body[pos:])
                if v_m:
                    props[key] = (v_m.group(1) == "true")
                    pos += v_m.end()
            else:
                # Unknown type — skip to next key by finding next '"'
                next_q = body.find('"', pos)
                if next_q < 0:
                    break
                pos = next_q

        pcms[path] = props

    return pcms


def _mac_from_device_path(dev_path: str) -> str:
    """Extract MAC from '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF'."""
    m = re.search(r"dev_([0-9A-F_]{17})", dev_path, re.I)
    if not m:
        return ""
    return m.group(1).replace("_", ":").upper()


def _find_sink_pcm(mac: str) -> tuple[str, dict] | None:
    """Locate the A2DP-sink PCM for a given device MAC.

    Remember: for BlueALSA, an A2DP-sink role looks like Mode='source' because
    ALSA clients read (capture) audio from it. We want Transport='A2DP-sink'
    and Mode='source'.
    """
    for path, props in _get_bluealsa_pcms().items():
        dev = props.get("Device", "")
        if _mac_from_device_path(dev) != mac:
            continue
        if props.get("Transport") == "A2DP-sink" and props.get("Mode") == "source":
            return path, props
    return None


# ─── Transport state (active/idle) ──────────────────────────────────────────
# Still via org.bluez (not bluealsa). We find MediaTransport1 paths by matching
# on MAC + /fd<N> suffix.

def _is_streaming(mac: str) -> bool:
    mac_us = mac.replace(":", "_")
    try:
        r = subprocess.run(
            ["busctl", "--system", "tree", "org.bluez", "--list"],
            capture_output=True, text=True, timeout=2,
        )
        tx_path = None
        for line in r.stdout.splitlines():
            line = line.strip()
            if mac_us in line and "/fd" in line:
                tx_path = line
                break
        if not tx_path:
            return False
        p = subprocess.run(
            ["busctl", "--system", "get-property", "org.bluez", tx_path,
             "org.bluez.MediaTransport1", "State"],
            capture_output=True, text=True, timeout=2,
        )
        m = re.search(r'"([^"]+)"', p.stdout)
        return m.group(1) == "active" if m else False
    except Exception as e:
        log.debug("streaming-check %s: %s", mac, e)
        return False


# ─── Volume read/write ──────────────────────────────────────────────────────

def _vol_raw_to_pct(raw: int) -> int:
    """BlueALSA packs L/R into uint16 as (L<<8)|R, each byte 0..127."""
    left = (raw >> 8) & 0x7F
    right = raw & 0x7F
    avg = (left + right) // 2
    return max(0, min(100, round(avg * 100 / 127)))


def _vol_pct_to_raw(pct: int) -> int:
    pct = max(0, min(100, pct))
    v = round(pct * 127 / 100)
    return (v << 8) | v


def _read_bt_volume_by_path(pcm_path: str) -> int | None:
    try:
        r = subprocess.run(
            ["busctl", "--system", "get-property", "org.bluealsa", pcm_path,
             "org.bluealsa.PCM1", "Volume"],
            capture_output=True, text=True, timeout=2,
        )
        m = re.search(r"q\s+(\d+)", r.stdout)
        if m:
            return _vol_raw_to_pct(int(m.group(1)))
    except Exception as e:
        log.debug("read_bt_volume %s: %s", pcm_path, e)
    return None


def _write_bt_volume_by_path(pcm_path: str, pct: int) -> bool:
    raw = _vol_pct_to_raw(pct)
    try:
        subprocess.run(
            ["busctl", "--system", "set-property", "org.bluealsa", pcm_path,
             "org.bluealsa.PCM1", "Volume", "q", str(raw)],
            capture_output=True, text=True, timeout=2, check=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        log.debug("write_bt_volume %s: %s / %s", pcm_path, e, e.stderr)
    except Exception as e:
        log.debug("write_bt_volume %s: %s", pcm_path, e)
    return False


# ─── AVRCP playback remote (org.bluez MediaPlayer1) ─────────────────────────

def send_avrcp(device_mac: str, command: str) -> bool:
    cmd_map = {
        "PLAY": "Play", "PLAYPAUSE": "Pause", "PAUSE": "Pause",
        "NEXT": "Next", "PREV": "Previous", "STOP": "Stop",
    }
    method = cmd_map.get(command.upper())
    if not method:
        return False
    mac_us = device_mac.replace(":", "_")
    try:
        r = subprocess.run(
            ["busctl", "--system", "tree", "org.bluez", "--list"],
            capture_output=True, text=True, timeout=2,
        )
        player_path = None
        for line in r.stdout.splitlines():
            line = line.strip()
            if mac_us in line and "/player" in line:
                player_path = line
                break
        if not player_path:
            log.debug("no MediaPlayer1 for %s", device_mac)
            return False
        subprocess.run(
            ["busctl", "--system", "call", "org.bluez", player_path,
             "org.bluez.MediaPlayer1", method],
            capture_output=True, text=True, timeout=2, check=True,
        )
        return True
    except Exception as e:
        log.debug("AVRCP %s → %s: %s", method, device_mac, e)
        return False


def disconnect_all_bt() -> None:
    """Drop every active BT connection. Paired devices stay paired."""
    for d in _list_connected_devices():
        log.info("BT: disconnecting %s (%s)", d.alias, d.mac)
        _btctl(f"disconnect {d.mac}")


# ─── Main source tracker ────────────────────────────────────────────────────

class BluetoothSource:
    def __init__(self, on_became_active=None, on_volume_from_phone=None):
        self.on_became_active = on_became_active
        self.on_volume_from_phone = on_volume_from_phone
        self._last_state = BTState()
        self._last_poll = 0.0
        self._last_pushed_volume: int | None = None

    def poll(self) -> BTState:
        now = time.monotonic()
        if now - self._last_poll < 1.5:
            return self._last_state
        self._last_poll = now

        # Single GetPCMs call gives us all BlueALSA info at once
        pcms = _get_bluealsa_pcms()
        devices = _list_connected_devices()

        for d in devices:
            # Find this device's sink PCM (if any)
            for path, props in pcms.items():
                dev_path = props.get("Device", "")
                if _mac_from_device_path(dev_path) != d.mac:
                    continue
                if props.get("Transport") == "A2DP-sink" and \
                   props.get("Mode") == "source":
                    d.pcm_path = path
                    raw = props.get("Volume")
                    if isinstance(raw, int):
                        d.volume_pct = _vol_raw_to_pct(raw)
                    break
            d.streaming = _is_streaming(d.mac)

        new_state = BTState(devices=devices)
        was_active = self._last_state.is_active
        is_active = new_state.is_active

        # Handoff: inactive → active
        if is_active and not was_active:
            active = new_state.streaming_device
            log.info("BT source became active: %s", active.alias if active else "?")
            if self.on_became_active:
                try:
                    self.on_became_active(active.alias if active else "Bluetooth")
                except Exception as e:
                    log.error("on_became_active: %s", e)

        # Volume-from-phone detection (only when BT is active)
        if is_active and self.on_volume_from_phone:
            active = new_state.streaming_device
            vol = active.volume_pct if active else None
            if vol is not None:
                prev_active = self._last_state.streaming_device
                prev_vol = prev_active.volume_pct if prev_active else None

                if self._last_pushed_volume is not None and \
                   abs(vol - self._last_pushed_volume) <= 2:
                    # Echo of our own push — consume the guard and move on
                    self._last_pushed_volume = None
                elif prev_vol is None or abs(vol - prev_vol) > 2:
                    try:
                        self.on_volume_from_phone(vol)
                    except Exception as e:
                        log.error("on_volume_from_phone: %s", e)

        self._last_state = new_state
        return new_state

    def active_device(self) -> BTDevice | None:
        return self._last_state.streaming_device

    def push_volume_to_phone(self, pct: int) -> None:
        active = self.active_device()
        if not active or not active.pcm_path:
            return
        if _write_bt_volume_by_path(active.pcm_path, pct):
            self._last_pushed_volume = pct
            log.debug("BT volume pushed → %s: %d%%", active.alias, pct)
