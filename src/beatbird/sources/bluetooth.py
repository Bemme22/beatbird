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
    # AVRCP-driven metadata. Empty strings = phone didn't expose them (BT
    # AVRCP is best-effort; not every player publishes every field).
    title: str = ""
    artist: str = ""
    status: str = ""                # "playing" | "paused" | "stopped" | ""
    position_ms: int = 0
    duration_ms: int = 0
    trusted: bool = False           # bluez auto-reconnect requires this
    paired: bool = False


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


# ─── Paired-device management ──────────────────────────────────────────────
# Used by the web UI's pairing page: list all known devices (paired or
# connected) plus their Trusted flag so the user can see who's allowed to
# auto-reconnect. The forget/unpair flow goes through bluetoothctl too.

_BTCTL_INFO_FIELD = re.compile(r"^\s*(\w[\w ]*):\s*(.+)\s*$")


def _btctl_info(mac: str) -> dict[str, str]:
    """Parse `bluetoothctl info <mac>` into a dict. Keys are property
    names (Alias, Paired, Trusted, Connected, ...); values are strings
    ('yes'/'no' for booleans, free text otherwise)."""
    out = _btctl(f"info {mac}")
    fields: dict[str, str] = {}
    for line in out.splitlines():
        m = _BTCTL_INFO_FIELD.match(line)
        if m:
            fields[m.group(1).strip()] = m.group(2).strip()
    return fields


def list_paired_devices() -> list[BTDevice]:
    """Return every device known to bluez, with paired/trusted/connected
    flags populated. Sorted with connected devices first, then paired,
    then by alias. Cheap enough for the web UI (single bluetoothctl
    invocation, no AVRCP polling)."""
    devs: list[BTDevice] = []
    out = _btctl("devices Paired")
    for line in out.splitlines():
        m = _DEV_LINE.match(line.strip())
        if not m:
            continue
        mac = m.group(1).upper()
        alias = m.group(2).strip()
        info = _btctl_info(mac)
        devs.append(BTDevice(
            mac=mac,
            alias=info.get("Alias", alias),
            paired=info.get("Paired", "no") == "yes",
            trusted=info.get("Trusted", "no") == "yes",
            connected=info.get("Connected", "no") == "yes",
        ))
    devs.sort(key=lambda d: (not d.connected, not d.paired, d.alias.lower()))
    return devs


def set_trusted(mac: str, trusted: bool = True) -> bool:
    """Set the Trusted flag on a paired device. Trusted devices can
    auto-reconnect to the Pi without a user prompt — the install's
    main.conf already has [Policy] AutoEnable=true, so as soon as a
    paired phone enters BT range it'll connect on its own.

    Verify by re-reading the property after the command rather than
    parsing the command's noisy ANSI-escape output."""
    verb = "trust" if trusted else "untrust"
    _btctl(f"{verb} {mac}")
    info = _btctl_info(mac)
    ok = (info.get("Trusted", "no") == "yes") == trusted
    log.info("BT: set_trusted(%s, %s) → %s", mac, trusted,
             "ok" if ok else "verify-failed")
    return ok


def disconnect_device(mac: str) -> bool:
    """Drop a single connected device. Paired + trusted state survive,
    so the phone can reconnect on its own as soon as it's in range
    again (unless it was also untrusted). Verified by re-reading the
    Connected property instead of parsing bluetoothctl's output."""
    _btctl(f"disconnect {mac}")
    info = _btctl_info(mac)
    ok = info.get("Connected", "no") == "no"
    log.info("BT: disconnect(%s) → %s", mac, "ok" if ok else "verify-failed")
    return ok


def forget_device(mac: str) -> bool:
    """Unpair + remove a device. Both ends are dropped from each other's
    pairing tables, so the device must re-pair to reconnect. Success is
    'the device no longer appears in bluetoothctl info' (Device not
    available), which is the same thing bluetoothctl returns from
    `remove` when the device was already gone — idempotent."""
    _btctl(f"remove {mac}")
    info = _btctl_info(mac)
    ok = not info   # empty dict = device no longer known
    log.info("BT: forget(%s) → %s", mac, "ok" if ok else "verify-failed")
    return ok


def is_discoverable() -> bool:
    """Snapshot of the adapter's Discoverable property. Cheap (one
    bluetoothctl invocation) — the bridge polls this on its 5 s status
    tick to drive the firmware's PAIRING overlay."""
    out = _btctl("show")
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Discoverable:"):
            return line.split(":", 1)[1].strip() == "yes"
    return False


def set_discoverable(on: bool, timeout_s: int = 60) -> bool:
    """Toggle the adapter's Discoverable state. Use a short timeout so
    the Pi isn't a permanently-visible pairing target — the install
    formerly set DiscoverableTimeout=0 (always on) but that's a soft
    attack surface. The web UI is the legit entry point for pairing
    sessions now.

    Verifies by reading `bluetoothctl show` after the command instead
    of parsing the noisy interactive output (ANSI escapes, async
    new_settings notifications); the simple `'succeeded' in out` check
    we had before missed the actual confirmation pattern."""
    if on:
        # discoverable-timeout takes effect before `discoverable on` — set
        # the inactivity window first so the adapter auto-stops advertising.
        _btctl(
            f"discoverable-timeout {timeout_s}",
            "pairable on",
            "discoverable on",
        )
    else:
        _btctl("discoverable off")
    ok = is_discoverable() == on
    log.info("BT: discoverable=%s (timeout %ds) → %s", on, timeout_s,
             "ok" if ok else "verify-failed")
    return ok


# ─── AVRCP metadata (org.bluez MediaPlayer1) ───────────────────────────────
# Phones expose Now-Playing info via the AVRCP MediaPlayer1 D-Bus interface
# once a MediaTransport is set up. Properties of interest:
#   - Track : a{sv} with Title, Artist, Album, Duration, etc.
#   - Status: s — "playing" | "paused" | "stopped"
#   - Position: u — current position in ms (only updates on phone push)
#
# AVRCP is best-effort: some sender apps publish nothing, others publish
# stale data. We treat all fields as opportunistic and never fail a poll
# because the phone is being shy.

_PLAYER_CACHE: dict[str, str] = {}    # mac → /org/bluez/...player path


def _find_player_path(mac: str) -> str | None:
    """Locate the MediaPlayer1 D-Bus path for a device. Cached because the
    busctl tree walk is the dominant cost in BT poll (~30 ms on a Pi Zero)."""
    cached = _PLAYER_CACHE.get(mac)
    if cached:
        return cached
    mac_us = mac.replace(":", "_")
    try:
        r = subprocess.run(
            ["busctl", "--system", "tree", "org.bluez", "--list"],
            capture_output=True, text=True, timeout=2,
        )
        for line in r.stdout.splitlines():
            line = line.strip()
            if mac_us in line and "/player" in line:
                _PLAYER_CACHE[mac] = line
                return line
    except Exception as e:
        log.debug("find_player_path %s: %s", mac, e)
    return None


def _invalidate_player_cache(mac: str | None = None) -> None:
    """Drop cached player path(s). Called when a device disconnects so a
    later reconnect re-discovers the (potentially new) player path."""
    if mac is None:
        _PLAYER_CACHE.clear()
    else:
        _PLAYER_CACHE.pop(mac, None)


def _parse_track_dict(out: str) -> dict[str, str | int]:
    """Parse busctl's a{sv} output for the Track property.

    Format: a{sv} <count> "key" <type> <value> "key" <type> <value> ...
    Types we care about: s (string, used for Title/Artist/Album),
                          x/u/q (ints, used for Duration/TrackNumber).
    """
    result: dict[str, str | int] = {}
    m = re.match(r"a\{sv\}\s+(\d+)\s+", out.strip())
    if not m:
        return result
    body = out.strip()[m.end():]
    propcount = int(m.group(1))
    pos = 0
    for _ in range(propcount):
        key_m = re.match(r'\s*"([^"]+)"\s+(\S+)\s+', body[pos:])
        if not key_m:
            break
        key = key_m.group(1)
        sig = key_m.group(2)
        pos += key_m.end()
        if sig == "s":
            v_m = re.match(r'"((?:[^"\\]|\\.)*)"\s*', body[pos:])
            if v_m:
                # Unescape backslash-escapes that busctl emits (\\ → \, \" → ")
                result[key] = v_m.group(1).replace('\\"', '"').replace("\\\\", "\\")
                pos += v_m.end()
        elif sig in ("u", "x", "q", "y", "t", "i", "n"):
            v_m = re.match(r"(-?\d+)\s*", body[pos:])
            if v_m:
                result[key] = int(v_m.group(1))
                pos += v_m.end()
        else:
            # Unknown signature — skip to next key
            nxt = body.find('"', pos)
            if nxt < 0:
                break
            pos = nxt
    return result


def _get_avrcp_track(mac: str) -> dict[str, str | int]:
    """Return the AVRCP Track dict for a device, or {} if the property is
    unavailable. Keys (when present): Title, Artist, Album, Duration (ms),
    TrackNumber, NumberOfTracks, Genre."""
    path = _find_player_path(mac)
    if not path:
        return {}
    try:
        r = subprocess.run(
            ["busctl", "--system", "get-property", "org.bluez", path,
             "org.bluez.MediaPlayer1", "Track"],
            capture_output=True, text=True, timeout=2,
        )
        return _parse_track_dict(r.stdout)
    except Exception as e:
        log.debug("AVRCP Track %s: %s", mac, e)
        return {}


def _get_avrcp_str_prop(mac: str, prop: str) -> str:
    """Read a single string property from MediaPlayer1 (Status, etc.)."""
    path = _find_player_path(mac)
    if not path:
        return ""
    try:
        r = subprocess.run(
            ["busctl", "--system", "get-property", "org.bluez", path,
             "org.bluez.MediaPlayer1", prop],
            capture_output=True, text=True, timeout=2,
        )
        m = re.search(r'"([^"]*)"', r.stdout)
        return m.group(1) if m else ""
    except Exception as e:
        log.debug("AVRCP %s %s: %s", prop, mac, e)
        return ""


def _get_avrcp_uint_prop(mac: str, prop: str) -> int:
    """Read a single uint property from MediaPlayer1 (Position)."""
    path = _find_player_path(mac)
    if not path:
        return 0
    try:
        r = subprocess.run(
            ["busctl", "--system", "get-property", "org.bluez", path,
             "org.bluez.MediaPlayer1", prop],
            capture_output=True, text=True, timeout=2,
        )
        m = re.search(r"\bu\s+(\d+)", r.stdout)
        return int(m.group(1)) if m else 0
    except Exception as e:
        log.debug("AVRCP %s %s: %s", prop, mac, e)
        return 0


# ─── Main source tracker ────────────────────────────────────────────────────

class BluetoothSource:
    # Echo guard window. If the phone hasn't reflected our pushed value
    # within this many seconds, drop the guard — some sender apps don't
    # actually honor BlueZ volume writes and the stale guard would block
    # legitimate phone-side changes forever.
    ECHO_GUARD_S = 5.0

    def __init__(self, on_became_active=None, on_volume_from_phone=None,
                 get_bridge_volume=None):
        self.on_became_active = on_became_active
        self.on_volume_from_phone = on_volume_from_phone
        # Called once per device when it first becomes active, so we can
        # push the bridge's (CamillaDSP-persisted) volume to the phone
        # instead of letting the phone's startup slider position cascade
        # back and clobber the speaker volume. Mirrors the Spotify
        # _spotify_initial_sync_done guard.
        self.get_bridge_volume = get_bridge_volume
        self._last_state = BTState()
        self._last_poll = 0.0
        self._last_pushed_volume: int | None = None
        self._last_pushed_at: float = 0.0
        self._initial_sync_done: dict[str, bool] = {}   # mac → bool

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
            # AVRCP metadata — only when actually streaming. When the
            # transport is idle the phone often hasn't published a Track
            # yet, and polling 4 busctl calls per non-active device on
            # every tick would just burn CPU.
            if d.streaming:
                track = _get_avrcp_track(d.mac)
                d.title    = str(track.get("Title", "") or "")
                d.artist   = str(track.get("Artist", "") or "")
                dur = track.get("Duration", 0)
                d.duration_ms = int(dur) if isinstance(dur, int) else 0
                d.status      = _get_avrcp_str_prop(d.mac, "Status")
                d.position_ms = _get_avrcp_uint_prop(d.mac, "Position")

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

        # Volume sync. Two flows:
        #  1. First observation per device → push bridge → phone. This
        #     mirrors the Spotify initial-sync guard: the phone often
        #     advertises a slider position from its own state cache (or
        #     the default 100%) that would otherwise overwrite CDSP's
        #     persisted volume.
        #  2. Subsequent changes → phone → bridge, with an echo guard so
        #     our own pushes don't ping-pong.
        if is_active and self.on_volume_from_phone:
            active = new_state.streaming_device
            mac = active.mac if active else ""
            vol = active.volume_pct if active else None
            if vol is not None and mac:
                if not self._initial_sync_done.get(mac):
                    self._initial_sync_done[mac] = True
                    if self.get_bridge_volume:
                        try:
                            bridge_vol = int(self.get_bridge_volume())
                        except Exception:
                            bridge_vol = None
                        if bridge_vol is not None and abs(bridge_vol - vol) > 3 \
                                and active.pcm_path:
                            log.info(
                                "BT initial sync %s: pushing bridge %d%% → phone (was %d%%)",
                                active.alias, bridge_vol, vol,
                            )
                            if _write_bt_volume_by_path(active.pcm_path, bridge_vol):
                                self._last_pushed_volume = bridge_vol
                                self._last_pushed_at = now
                else:
                    prev_active = self._last_state.streaming_device
                    prev_vol = prev_active.volume_pct if prev_active else None

                    echo_fresh = (
                        self._last_pushed_volume is not None
                        and now - self._last_pushed_at < self.ECHO_GUARD_S
                    )
                    if echo_fresh and abs(vol - self._last_pushed_volume) <= 2:
                        # Echo of our own push — consume the guard
                        self._last_pushed_volume = None
                    elif prev_vol is None or abs(vol - prev_vol) > 2:
                        try:
                            self.on_volume_from_phone(vol)
                        except Exception as e:
                            log.error("on_volume_from_phone: %s", e)

        # Cache invalidation on disconnect: drop the initial-sync flag
        # so the next reconnect goes through bridge → phone push again.
        prev_macs = {d.mac for d in self._last_state.devices}
        new_macs  = {d.mac for d in new_state.devices}
        for gone in prev_macs - new_macs:
            self._initial_sync_done.pop(gone, None)
            _invalidate_player_cache(gone)

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
            self._last_pushed_at = time.monotonic()
            log.debug("BT volume pushed → %s: %d%%", active.alias, pct)
