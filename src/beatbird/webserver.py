"""
beatbird.webserver — diagnostics / control UI on http://<host>.local:8080

Entities exposed:
  GET  /                   — HTML dashboard
  GET  /api/profile        — the active profile (sanitised)
  GET  /api/status         — live status snapshot
  GET  /api/filters        — current CamillaDSP filter list (name + gain + freq + q)
  GET  /api/logs           — Server-Sent Events stream of `journalctl -fu beatbird-bridge`
  POST /api/volume         — {"pct": 42}
  POST /api/playback       — {"cmd": "PLAY"|"PAUSE"|"PLAYPAUSE"|"NEXT"|"PREV"}
  POST /api/reload         — reload the CamillaDSP config from disk
  POST /api/filter         — {"name": "bass_shelf", "gain": 6.5} live-patch one filter
  POST /api/service        — {"name": "beatbird-bridge|camilladsp|go-librespot", "action": "restart"}
  POST /api/system         — {"action": "reboot"|"shutdown"}

The webserver does NOT import the running bridge directly. It talks to the
same underlying services (CamillaDSP over websocket, go-librespot over
HTTP, hardware over I2C) — one level of indirection means web and bridge
can be restarted independently.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from beatbird import settings_overrides, system
from beatbird.sources import bluetooth as bt
from beatbird.audio.camilladsp import CamillaDSP, db_to_pct, pct_to_db
from beatbird.config import load_profile
from beatbird.hardware import louder_hat
from beatbird.sources.spotify import SpotifyClient

log = logging.getLogger("beatbird.web")

app = FastAPI(title="BeatBird")
_profile = None
_dsp = CamillaDSP()
_spotify = SpotifyClient()

# Services the system page can restart. Hardcoded allowlist so a stray
# POST can't restart arbitrary units on the Pi.
_ALLOWED_SERVICES = {"beatbird-bridge", "camilladsp", "go-librespot", "snapclient"}

# CamillaDSP filters the web UI is allowed to patch. Keeps random POSTs
# from blowing up the pipeline by editing crossovers / limiters etc.
_TUNABLE_FILTERS = {"bass_shelf", "sub_punch", "timpani_body", "fullness"}


def _get_profile():
    global _profile
    if _profile is None:
        _profile = load_profile()
    return _profile


def _hardware():
    p = _get_profile()
    if p.soundcard.driver.startswith("louder-hat"):
        return louder_hat.from_profile(p.soundcard)
    from beatbird.hardware.base import NullHardware
    return NullHardware()


# ─── Pydantic models ─────────────────────────────────────────────────────────

class VolumeReq(BaseModel):
    pct: int


class PlaybackReq(BaseModel):
    cmd: str


class FilterReq(BaseModel):
    name: str
    gain: float


class ServiceReq(BaseModel):
    name: str
    action: str = "restart"


class SystemReq(BaseModel):
    action: str   # "reboot" | "shutdown"


class SettingsReq(BaseModel):
    palette: dict | None = None   # {"a": "#rrggbb", "g": "...", ...} — slots a/g/d/p/s/e
    idle: dict | None = None      # {"rss_url": "...", "rss_refresh_minutes": 30, "rss_weight": 0.5}


class BtDiscoverableReq(BaseModel):
    seconds: int = 60             # 5..600 server-side clamped


class BtDeviceReq(BaseModel):
    mac: str
    action: str                   # "trust" | "untrust" | "disconnect" | "forget"


# ─── Read API ────────────────────────────────────────────────────────────────

@app.get("/api/profile")
def get_profile():
    return _get_profile().model_dump()


@app.get("/api/status")
def get_status():
    db = _dsp.get_volume_db()
    vol_pct = db_to_pct(db) if db is not None else 0
    state = _spotify.get_state()
    amp = _hardware().read_status()
    return {
        "volume": vol_pct,
        "volume_db": db,
        "cpu_temp": system.cpu_temp(),
        "wifi_rssi": system.wifi_rssi(),
        "amp": amp,
        "camilladsp": system.service_active("camilladsp"),
        "spotify_service": system.service_active("go-librespot"),
        "bridge": system.service_active("beatbird-bridge"),
        "spotify": state.__dict__ if state else None,
    }


@app.get("/api/filters")
def get_filters():
    """Current Biquad/Peaking filter parameters from the running DSP.
    Returns only the tunable subset — the web slider UI keys off this."""
    cfg = _dsp.get_config()
    if not cfg:
        return {"filters": []}
    out = []
    for name, body in (cfg.get("filters") or {}).items():
        if name not in _TUNABLE_FILTERS:
            continue
        params = body.get("parameters", {}) if isinstance(body, dict) else {}
        out.append({
            "name": name,
            "type": params.get("type"),
            "freq": params.get("freq"),
            "gain": params.get("gain"),
            "q":    params.get("q"),
        })
    return {"filters": out}


# ─── Write API ───────────────────────────────────────────────────────────────

@app.post("/api/volume")
def set_volume(req: VolumeReq):
    if not 0 <= req.pct <= 100:
        raise HTTPException(400, "pct must be 0..100")
    _dsp.set_volume_db(pct_to_db(req.pct))
    return {"ok": True, "pct": req.pct}


@app.post("/api/playback")
def set_playback(req: PlaybackReq):
    cmd = req.cmd.upper()
    m = {
        "PLAY": _spotify.play, "PAUSE": _spotify.pause,
        "PLAYPAUSE": _spotify.playpause,
        "NEXT": _spotify.next, "PREV": _spotify.prev,
    }
    if cmd not in m:
        raise HTTPException(400, f"unknown cmd: {cmd}")
    m[cmd]()
    return {"ok": True, "cmd": cmd}


@app.post("/api/reload")
def reload_dsp():
    """Ask camilladsp to re-read /etc/camilladsp/config.yml. Needs sudo
    because the unit runs as root; the install/55-web-sudo.sh sudoers
    rule grants the bridge user passwordless access to this specific
    invocation."""
    try:
        subprocess.run(
            ["sudo", "systemctl", "reload", "camilladsp"],
            check=False, timeout=5, capture_output=True,
        )
    except Exception as e:
        raise HTTPException(500, f"reload failed: {e}")
    return {"ok": True}


@app.post("/api/filter")
def set_filter(req: FilterReq):
    """Live-patch a single filter's gain via CamillaDSP. Volatile —
    the change reverts on `systemctl reload camilladsp`. Persisting
    into the profile YAML is V2."""
    if req.name not in _TUNABLE_FILTERS:
        raise HTTPException(400, f"filter {req.name!r} not in tunable allowlist")
    if not -20.0 <= req.gain <= 20.0:
        raise HTTPException(400, "gain must be -20..+20 dB")
    cfg = _dsp.get_config()
    if not cfg:
        raise HTTPException(500, "DSP not reachable")
    f = (cfg.get("filters") or {}).get(req.name)
    if not f:
        raise HTTPException(404, f"filter {req.name!r} not in running config")
    params = f.get("parameters", {})
    patch = {req.name: {"parameters": {**params, "gain": req.gain}}}
    _dsp.patch_filters(patch)
    return {"ok": True, "name": req.name, "gain": req.gain}


@app.post("/api/service")
def control_service(req: ServiceReq):
    """Restart/stop/start a known systemd unit. Allowlist-gated."""
    if req.name not in _ALLOWED_SERVICES:
        raise HTTPException(400, f"service {req.name!r} not in allowlist")
    if req.action not in {"restart", "start", "stop"}:
        raise HTTPException(400, "action must be restart|start|stop")
    try:
        r = subprocess.run(
            ["sudo", "systemctl", req.action, req.name],
            check=False, timeout=15, capture_output=True, text=True,
        )
    except Exception as e:
        raise HTTPException(500, f"systemctl failed: {e}")
    if r.returncode != 0:
        # "Job canceled" on a mid-restart race is fine — the service IS
        # being acted on. Pass that through but flag non-zero exits.
        return {"ok": False, "rc": r.returncode, "stderr": r.stderr.strip()}
    return {"ok": True, "name": req.name, "action": req.action}


@app.get("/api/health")
def health():
    """Aggregated network + service health snapshot. One call answers
    the diagnostic questions that previously needed five SSH commands:
    is the Pi on WiFi? does it see the gateway? does it reach the
    internet, Spotify, MA's Snapserver? are the systemd units alive?
    Also returns the last few bridge warnings/errors for quick triage.
    All probes are time-bounded so the endpoint stays responsive even
    when something is broken."""
    p = _get_profile()
    gw = system.default_gateway()
    snap_host = (os.environ.get("BEATBIRD_SNAPCAST_SERVER", "").strip()
                 or p.sources.snapcast.server)
    return {
        "hostname":     system.hostname(),
        "ip":           system.ip_address(),
        "ssid":         system.wifi_ssid(),
        "rssi_dbm":    system.wifi_rssi(),
        "gateway":      {"ip": gw, **system.ping(gw)} if gw else {"ip": "", "ok": False},
        "internet":     system.ping("1.1.1.1"),
        "spotify_api":  system.http_probe("https://api.spotify.com/"),
        "spotify_ap":   {"ok": system.tcp_reachable("ap-gew4.spotify.com", 4070)},
        "snapserver":   {"host": snap_host, "ok": system.tcp_reachable(snap_host, 1705)} if snap_host else {"host": "", "ok": False},
        "mdns":         system.service_active("avahi-daemon"),
        "services": {
            "beatbird-bridge": system.service_active("beatbird-bridge"),
            "camilladsp":      system.service_active("camilladsp"),
            "go-librespot":    system.service_active("go-librespot"),
            "snapclient":      system.service_active("snapclient"),
        },
        "recent_warnings": system.journal_recent_errors("beatbird-bridge", 20),
    }


@app.post("/api/system")
def system_action(req: SystemReq):
    """Reboot or shutdown the host. Fires-and-forgets — by the time the
    response would land, the network is going down anyway."""
    if req.action not in {"reboot", "shutdown"}:
        raise HTTPException(400, "action must be reboot|shutdown")
    cmd = ["sudo", "systemctl", "reboot" if req.action == "reboot" else "poweroff"]
    try:
        # Spawn detached — don't block, don't capture (we'll be dead).
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        raise HTTPException(500, f"{req.action} failed: {e}")
    return {"ok": True, "action": req.action}


# ─── Settings (web UI live tunables) ─────────────────────────────────────────
# Layered model: profile YAML is the immutable git-tracked base config; the
# overrides JSON sits in /var/lib/beatbird/ and holds per-installation tweaks
# (palette, RSS URL, …) the user makes through this page. The bridge polls
# the file's mtime once every status tick and re-applies on change.

_PALETTE_SLOTS = ("a", "g", "d", "p", "s", "e")


def _hex6(s) -> str | None:
    """Normalise a #rrggbb string. Returns None for anything that isn't
    six hex digits — keeps malformed colour input out of the override JSON.
    Also used by GET /api/settings to normalise profile values (which are
    stored bare like "F0CB7B" without the leading hash)."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6:
        return None
    try:
        int(s, 16)
    except ValueError:
        return None
    return "#" + s.lower()


@app.get("/api/settings")
def get_settings():
    """Effective settings: profile defaults merged with any overrides on
    disk. The UI uses this to pre-fill the form with whatever the bridge
    is currently running."""
    p = _get_profile()
    ov = settings_overrides.load()

    base_palette = {
        "a": _hex6(p.display.accent_color),
        "g": _hex6(p.display.accent_glow),
        "d": _hex6(p.display.accent_dim),
        "p": _hex6(p.display.text_primary),
        "s": _hex6(p.display.text_secondary),
        "e": _hex6(p.display.accent_alert),
    }
    ov_palette = ov.get("palette") if isinstance(ov.get("palette"), dict) else {}
    palette = {k: _hex6(ov_palette.get(k)) or base_palette.get(k) for k in _PALETTE_SLOTS}

    base_idle = {
        "rss_url":             p.idle.rss_url,
        "rss_refresh_minutes": p.idle.rss_refresh_minutes,
        "rss_weight":          p.idle.rss_weight,
    }
    ov_idle = ov.get("idle") if isinstance(ov.get("idle"), dict) else {}
    idle = {**base_idle, **{k: v for k, v in ov_idle.items() if v is not None}}

    return {"palette": palette, "idle": idle, "overrides": ov}


@app.post("/api/settings")
def set_settings(req: SettingsReq):
    """Persist overrides to disk. PATCH semantics — only the keys present
    in the request are touched; everything else stays. To remove the
    palette override (revert to profile), POST {"palette": {}}; same for
    idle. The bridge picks changes up on its next mtime poll (≤ 5 s) —
    no restart, no signal.

    Was a footgun before: a curl-style POST that only carried `idle`
    silently wiped the user's palette override because the handler
    rebuilt the file from scratch."""
    out = settings_overrides.load()

    if req.palette is not None:
        # Empty dict = "clear the palette override".
        if not req.palette:
            out["palette"] = None
        else:
            clean: dict[str, str] = {}
            for k in _PALETTE_SLOTS:
                v = _hex6(req.palette.get(k, ""))
                if v:
                    clean[k] = v
            out["palette"] = clean or None

    if req.idle is not None:
        if not req.idle:
            out["idle"] = None
        else:
            url = (req.idle.get("rss_url") or "").strip()
            try:
                refresh = max(1, min(1440, int(req.idle.get("rss_refresh_minutes", 30))))
            except (TypeError, ValueError):
                refresh = 30
            try:
                weight = max(0.0, min(1.0, float(req.idle.get("rss_weight", 0.5))))
            except (TypeError, ValueError):
                weight = 0.5
            out["idle"] = {
                "rss_url":             url,
                "rss_refresh_minutes": refresh,
                "rss_weight":          weight,
            }

    try:
        settings_overrides.save(out)
    except Exception as e:
        raise HTTPException(500, f"settings save failed: {e}")
    return {"ok": True, "overrides": out}


# ─── Bluetooth pairing & device management ──────────────────────────────────
# The bridge owns the BluetoothSource runtime; this controller just talks
# to bluez via the same bluetoothctl helpers so the web UI and bridge see
# a consistent device list. The bridge's poll loop will pick up trust
# state changes on its next BT tick (~1.5 s).

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def _validate_mac(mac: str) -> str:
    if not _MAC_RE.match(mac or ""):
        raise HTTPException(400, f"invalid MAC: {mac!r}")
    return mac.upper()


@app.get("/api/bluetooth")
def get_bluetooth():
    """Snapshot of paired devices plus adapter state. Cheap enough to
    poll from the web UI every few seconds during a pairing session."""
    devices = bt.list_paired_devices()
    return {
        "devices": [
            {
                "mac":       d.mac,
                "alias":     d.alias,
                "paired":    d.paired,
                "trusted":   d.trusted,
                "connected": d.connected,
            }
            for d in devices
        ],
    }


@app.post("/api/bluetooth/discoverable")
def bt_discoverable(req: BtDiscoverableReq):
    """Put the adapter in pairing mode for N seconds. The phone has to
    initiate the pair from its side; we just open the window. After the
    window closes (bluez handles the timer internally), the adapter
    silently stops advertising — no further action needed here."""
    seconds = max(5, min(600, req.seconds))
    if not bt.set_discoverable(True, timeout_s=seconds):
        raise HTTPException(500, "set discoverable failed")
    return {"ok": True, "seconds": seconds}


@app.post("/api/bluetooth/device")
def bt_device_action(req: BtDeviceReq):
    """Per-device action. trust/untrust gate auto-reconnect, disconnect
    drops the active link without forgetting, forget removes pairing
    entirely."""
    mac = _validate_mac(req.mac)
    action = req.action.lower()
    if action == "trust":
        ok = bt.set_trusted(mac, True)
    elif action == "untrust":
        ok = bt.set_trusted(mac, False)
    elif action == "disconnect":
        ok = bt.disconnect_device(mac)
    elif action == "forget":
        ok = bt.forget_device(mac)
    else:
        raise HTTPException(400, f"unknown action: {action!r}")
    if not ok:
        raise HTTPException(500, f"{action} failed for {mac}")
    return {"ok": True, "mac": mac, "action": action}


# ─── Log streaming ───────────────────────────────────────────────────────────

@app.get("/api/logs")
async def stream_logs(unit: str = "beatbird-bridge", lines: int = 50):
    """Server-Sent Events stream of `journalctl -fu <unit>` so a browser
    can tail the bridge log without an SSH session."""
    if unit not in _ALLOWED_SERVICES:
        raise HTTPException(400, f"unit {unit!r} not in allowlist")

    async def gen():
        proc = await asyncio.create_subprocess_exec(
            "journalctl", "-u", unit, "-f", "-n", str(lines), "--no-pager",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            assert proc.stdout
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                yield f"data: {line.decode('utf-8', 'replace').rstrip()}\n\n"
        finally:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                proc.kill()
    return StreamingResponse(gen(), media_type="text/event-stream")


# ─── Dashboard (single HTML blob, no build step) ─────────────────────────────

_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BeatBird — {name}</title>
<style>
 body{{font:14px/1.4 system-ui,sans-serif;background:#111;color:#eee;max-width:640px;margin:1.5em auto;padding:1em}}
 h1{{font-weight:400;letter-spacing:.05em;margin:0 0 1em}}
 h2{{font-weight:400;font-size:13px;letter-spacing:.15em;text-transform:uppercase;color:#777;margin:1.2em 0 .4em}}
 .card{{background:#1c1c1c;border-radius:8px;padding:.9em 1.1em;margin:.5em 0}}
 .row{{display:flex;justify-content:space-between;margin:.25em 0}}
 .row span:first-child{{color:#888}}
 button{{background:#2a2a2a;color:#eee;border:0;border-radius:4px;padding:.45em .9em;margin:.2em .15em .2em 0;cursor:pointer;font-size:13px}}
 button:hover{{background:#3a3a3a}}
 button.warn{{background:#3a1e1e;color:#e88}}
 button.warn:hover{{background:#4a2a2a}}
 input[type=range]{{width:100%;accent-color:#888}}
 .filter-row{{display:grid;grid-template-columns:90px 1fr 60px;gap:.5em;align-items:center;margin:.4em 0}}
 .filter-row label{{color:#888;font-size:12px}}
 .filter-row .v{{color:#ccc;font-variant-numeric:tabular-nums;text-align:right;font-size:12px}}
 #logs{{background:#000;color:#9c9;font:11px/1.3 ui-monospace,monospace;padding:.7em;border-radius:4px;height:200px;overflow-y:scroll;white-space:pre-wrap}}
 code{{background:#000;padding:2px 5px;border-radius:3px}}
</style></head>
<body>
<h1>{name}</h1>

<div class="card">
 <div class="row"><span>Playback</span><span id="playback">—</span></div>
 <div class="row"><span>Track</span><span id="track">—</span></div>
 <div class="row"><span>Source</span><span id="source">—</span></div>
 <div class="row"><span>Volume</span><span id="volume">—</span></div>
 <input id="vol" type="range" min="0" max="100" value="50">
 <div style="margin-top:.5em">
  <button onclick="cmd('PREV')">⏮</button>
  <button onclick="cmd('PLAYPAUSE')">⏯</button>
  <button onclick="cmd('NEXT')">⏭</button>
 </div>
</div>

<div class="card">
 <div class="row"><span>CPU</span><span id="cpu">—</span></div>
 <div class="row"><span>WiFi</span><span id="wifi">—</span></div>
 <div class="row"><span>Bridge</span><span id="bridge">—</span></div>
 <div class="row"><span>CamillaDSP</span><span id="dsp">—</span></div>
 <div class="row"><span>Spotify svc</span><span id="sp_svc">—</span></div>
 <div class="row"><span>Amp</span><span id="amp">—</span></div>
</div>

<h2>Loudness — live tune</h2>
<div class="card" id="filters">
 <div style="color:#666;font-size:12px">Patches CamillaDSP at runtime. Changes revert on DSP reload — V2 will persist into profile.</div>
 <div id="filter-list"></div>
</div>

<h2>Logs — beatbird-bridge</h2>
<div class="card">
 <div id="logs">(connecting…)</div>
</div>

<h2>System</h2>
<div class="card">
 <div style="margin-bottom:.4em">
  <button onclick="svc('beatbird-bridge')">Restart bridge</button>
  <button onclick="svc('camilladsp')">Restart CamillaDSP</button>
  <button onclick="svc('go-librespot')">Restart Spotify</button>
  <button onclick="reloadDsp()">Reload DSP config</button>
 </div>
 <div>
  <button class="warn" onclick="sys('reboot')">Reboot Pi</button>
  <button class="warn" onclick="sys('shutdown')">Shutdown Pi</button>
 </div>
 <div style="margin-top:.6em">
  <a href="/health" style="color:var(--accent,#888);text-decoration:none">→ Health check</a>
  &nbsp;·&nbsp;
  <a href="/settings" style="color:var(--accent,#888);text-decoration:none">→ Settings (palette, RSS)</a>
  &nbsp;·&nbsp;
  <a href="/bluetooth" style="color:var(--accent,#888);text-decoration:none">→ Bluetooth</a>
 </div>
</div>

<div class="card" style="color:#666;font-size:12px">
 Profile: <code>{speaker_id}</code> · Driver: <code>{driver}</code> · Display: <code>{display}</code>
</div>

<script>
 async function refresh() {{
  const s = await fetch('/api/status').then(r=>r.json());
  document.getElementById('playback').textContent = s.spotify?.paused ? 'Paused'
   : s.spotify && !s.spotify.stopped ? 'Playing' : 'Stopped';
  document.getElementById('track').textContent =
   s.spotify?.title ? s.spotify.title + ' — ' + s.spotify.artist : '—';
  document.getElementById('source').textContent = s.spotify && !s.spotify.stopped ? 'spotify' : 'none';
  document.getElementById('volume').textContent = s.volume + '%';
  document.getElementById('vol').value = s.volume;
  document.getElementById('cpu').textContent = (s.cpu_temp||0).toFixed(1) + ' °C';
  document.getElementById('wifi').textContent = s.wifi_rssi + ' dBm';
  document.getElementById('bridge').textContent = s.bridge ? 'active' : 'stopped';
  document.getElementById('dsp').textContent = s.camilladsp ? 'active' : 'stopped';
  document.getElementById('sp_svc').textContent = s.spotify_service ? 'active' : 'stopped';
  document.getElementById('amp').textContent = Object.entries(s.amp||{{}}).map(([k,v])=>`${{k}}=${{v}}`).join(', ') || '—';
 }}

 // Per-slider debounce so dragging fires lots of `input` events without
 // hammering the backend with one request per pixel. Last value within
 // `wait` ms wins. Use `input` (not `change`) so the change is committed
 // while you're still dragging — `change` only fires on release, which
 // touch users routinely miss.
 function debounce(fn, wait=120) {{
   let t; return (...args) => {{ clearTimeout(t); t = setTimeout(() => fn(...args), wait); }};
 }}

 async function loadFilters() {{
  const r = await fetch('/api/filters').then(r=>r.json());
  const wrap = document.getElementById('filter-list');
  wrap.innerHTML = '';
  for (const f of (r.filters||[])) {{
   const row = document.createElement('div');
   row.className = 'filter-row';
   row.innerHTML = `<label>${{f.name}}</label>
     <input type="range" min="-12" max="12" step="0.5" value="${{f.gain}}" data-name="${{f.name}}">
     <span class="v">${{f.gain.toFixed(1)}} dB</span>`;
   const input = row.querySelector('input');
   const span = row.querySelector('.v');
   const send = debounce(async v => {{
     await fetch('/api/filter', {{method:'POST',headers:{{'content-type':'application/json'}},
       body:JSON.stringify({{name:f.name, gain:v}})}});
   }}, 150);
   input.addEventListener('input', () => {{
     const v = parseFloat(input.value);
     span.textContent = v.toFixed(1) + ' dB';
     send(v);
   }});
   wrap.appendChild(row);
  }}
 }}

 const sendVolume = debounce(async pct => {{
   await fetch('/api/volume', {{method:'POST',headers:{{'content-type':'application/json'}}, body:JSON.stringify({{pct}})}});
 }}, 120);
 document.getElementById('vol').addEventListener('input', e => sendVolume(+e.target.value));
 async function cmd(c) {{
  await fetch('/api/playback', {{method:'POST',headers:{{'content-type':'application/json'}}, body:JSON.stringify({{cmd:c}})}});
  setTimeout(refresh, 300);
 }}
 async function reloadDsp() {{
  await fetch('/api/reload', {{method:'POST'}});
  setTimeout(loadFilters, 500);   // gains snap back to YAML defaults
 }}
 async function svc(name) {{
  if (!confirm('Restart ' + name + '?')) return;
  await fetch('/api/service', {{method:'POST',headers:{{'content-type':'application/json'}}, body:JSON.stringify({{name, action:'restart'}})}});
 }}
 async function sys(action) {{
  if (!confirm(action + ' the Pi?')) return;
  await fetch('/api/system', {{method:'POST',headers:{{'content-type':'application/json'}}, body:JSON.stringify({{action}})}});
 }}

 // Live log stream via Server-Sent Events
 const logBox = document.getElementById('logs');
 logBox.textContent = '';
 const ev = new EventSource('/api/logs?unit=beatbird-bridge');
 ev.onmessage = e => {{
  logBox.textContent += e.data + '\\n';
  // Trim to last 500 lines so the DOM doesn't grow unbounded
  const lines = logBox.textContent.split('\\n');
  if (lines.length > 500) logBox.textContent = lines.slice(-500).join('\\n');
  logBox.scrollTop = logBox.scrollHeight;
 }};
 ev.onerror = () => {{ logBox.textContent += '(stream lost — reconnecting…)\\n'; }};

 refresh(); setInterval(refresh, 2000);
 loadFilters(); setInterval(loadFilters, 30000);
</script>
</body></html>
"""


@app.get("/", response_class=HTMLResponse)
def dashboard():
    p = _get_profile()
    return _HTML.format(
        name=p.identity.friendly_name,
        speaker_id=p.identity.speaker_id,
        driver=p.soundcard.driver,
        display=p.display.type,
    )


# ─── /health page — one-glance network + service diagnostics ─────────────────

_HEALTH_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BeatBird Health — {name}</title>
<style>
 body{{font:14px/1.45 system-ui,sans-serif;background:#111;color:#eee;max-width:720px;margin:1.2em auto;padding:0 1em 2em}}
 h1{{font-weight:400;letter-spacing:.05em;margin:0 0 .2em}}
 h1 a{{color:#888;font-size:13px;text-decoration:none;float:right}}
 h2{{font-weight:400;font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:#777;margin:1.2em 0 .4em}}
 .card{{background:#1c1c1c;border-radius:6px;padding:.8em 1em;margin:.4em 0}}
 .row{{display:grid;grid-template-columns:170px 24px 1fr;gap:.4em;padding:.15em 0;align-items:baseline}}
 .row .label{{color:#888}}
 .row .badge{{font-family:ui-monospace,monospace;font-weight:bold;text-align:center}}
 .ok{{color:#5d6}} .warn{{color:#fb6}} .bad{{color:#e66}} .neutral{{color:#999}}
 #warnings{{background:#000;color:#fb8;font:11px/1.35 ui-monospace,monospace;padding:.6em;border-radius:4px;white-space:pre-wrap;max-height:260px;overflow-y:auto}}
 .ts{{color:#666;font-size:11px;margin-top:.8em}}
</style></head>
<body>
<h1>{name} <a href="/">← Dashboard</a></h1>
<div id="root" class="neutral">Lade…</div>
<h2>Bridge — letzte Warnungen / Errors</h2>
<div class="card"><div id="warnings">(lade…)</div></div>
<div class="ts" id="ts">–</div>
<script>
 const ICON = {{true: '✓', false: '✗'}};
 const CLS  = {{true: 'ok', false: 'bad'}};

 function row(label, ok, extra='') {{
   const klass = (ok===null || ok===undefined) ? 'neutral' : CLS[Boolean(ok)];
   const badge = (ok===null || ok===undefined) ? '·' : ICON[Boolean(ok)];
   return `<div class="row"><span class="label">${{label}}</span><span class="badge ${{klass}}">${{badge}}</span><span>${{extra}}</span></div>`;
 }}

 async function refresh() {{
   try {{
     const r = await fetch('/api/health');
     const h = await r.json();
     const ip = h.ip || '?';
     const ssid = h.ssid || '?';
     const rssi = h.rssi_dbm;
     const rssiCls = rssi>=-67?'ok':rssi>=-75?'warn':rssi>=-85?'warn':'bad';
     const gw = h.gateway || {{}};
     const inet = h.internet || {{}};
     const sapi = h.spotify_api || {{}};
     const sap = h.spotify_ap || {{}};
     const snap = h.snapserver || {{}};
     const svc = h.services || {{}};

     const html = `
       <div class="card">
         <h2 style="margin-top:0">Network</h2>
         ${{row('Hostname', true, h.hostname || '?')}}
         ${{row('IP', !!h.ip, ip)}}
         ${{row('SSID', !!h.ssid, ssid)}}
         <div class="row"><span class="label">RSSI</span><span class="badge ${{rssiCls}}">${{rssi||0}}</span><span>dBm</span></div>
         ${{row('Gateway', gw.ok, (gw.ip||'-') + (gw.rtt_ms ? ' · '+gw.rtt_ms.toFixed(1)+' ms' : ''))}}
         ${{row('Internet (1.1.1.1)', inet.ok, inet.rtt_ms ? inet.rtt_ms.toFixed(1)+' ms' : '–')}}
         ${{row('mDNS / avahi', h.mdns, h.mdns ? 'aktiv' : 'aus')}}
       </div>
       <div class="card">
         <h2 style="margin-top:0">External</h2>
         ${{row('Spotify API', sapi.ok, sapi.code ? 'HTTP '+sapi.code+(sapi.rtt_ms?' · '+sapi.rtt_ms.toFixed(0)+'ms':''): '–')}}
         ${{row('Spotify AP :4070', sap.ok, sap.ok ? 'reachable' : 'refused / blocked')}}
         ${{row('Snapserver :1705', snap.ok, snap.host || '–')}}
       </div>
       <div class="card">
         <h2 style="margin-top:0">Services</h2>
         ${{Object.entries(svc).map(([n,a]) => row(n, a, a?'active':'stopped')).join('')}}
       </div>`;
     document.getElementById('root').innerHTML = html;

     const w = (h.recent_warnings || []);
     document.getElementById('warnings').textContent =
       w.length ? w.join('\\n') : '(keine Warnungen — alles ruhig)';
     document.getElementById('ts').textContent =
       'Stand: ' + new Date().toLocaleTimeString();
   }} catch (e) {{
     document.getElementById('root').innerHTML = `<div class="card bad">Fehler beim Laden: ${{e}}</div>`;
   }}
 }}
 refresh(); setInterval(refresh, 4000);
</script>
</body></html>
"""


@app.get("/health", response_class=HTMLResponse)
def health_page():
    p = _get_profile()
    return _HEALTH_HTML.format(name=p.identity.friendly_name)


# ─── /settings page — live editor for palette + idle/RSS ─────────────────────

_SETTINGS_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BeatBird Settings — {name}</title>
<style>
 body{{font:14px/1.45 system-ui,sans-serif;background:#111;color:#eee;max-width:640px;margin:1.2em auto;padding:0 1em 2em}}
 h1{{font-weight:400;letter-spacing:.05em;margin:0 0 .2em}}
 h1 a{{color:#888;font-size:13px;text-decoration:none;float:right}}
 h2{{font-weight:400;font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:#777;margin:1.4em 0 .4em}}
 .card{{background:#1c1c1c;border-radius:6px;padding:.9em 1.1em;margin:.4em 0}}
 .pal{{display:grid;grid-template-columns:90px 50px 110px 1fr;gap:.5em;align-items:center;margin:.35em 0}}
 .pal label{{color:#888;font-size:12px}}
 .pal .swatch{{width:36px;height:24px;border-radius:4px;border:1px solid #444}}
 .pal input[type=color]{{width:50px;height:30px;background:none;border:0;padding:0;cursor:pointer}}
 .pal input[type=text]{{background:#222;color:#eee;border:1px solid #333;border-radius:3px;padding:.3em .5em;font-family:ui-monospace,monospace;font-size:12px;width:90px}}
 .pal .hint{{color:#666;font-size:11px}}
 .field{{display:grid;grid-template-columns:160px 1fr;gap:.6em;align-items:center;margin:.4em 0}}
 .field label{{color:#888;font-size:12px}}
 input[type=text].url{{width:100%;background:#222;color:#eee;border:1px solid #333;border-radius:3px;padding:.4em .6em;font-family:ui-monospace,monospace;font-size:12px;box-sizing:border-box}}
 input[type=number]{{background:#222;color:#eee;border:1px solid #333;border-radius:3px;padding:.3em .5em;font-size:13px;width:80px}}
 input[type=range]{{flex:1;accent-color:#888}}
 .slider-row{{display:flex;align-items:center;gap:.5em}}
 button{{background:#2a2a2a;color:#eee;border:0;border-radius:4px;padding:.5em 1em;margin:.2em .15em .2em 0;cursor:pointer;font-size:13px}}
 button:hover{{background:#3a3a3a}}
 button.primary{{background:#284028;color:#cfc}}
 button.primary:hover{{background:#385038}}
 #status{{font-size:12px;color:#888;margin-left:.6em}}
 .legend{{color:#666;font-size:11px;margin:.3em 0 .8em}}
</style></head>
<body>
<h1>{name} — Settings <a href="/">← Dashboard</a></h1>
<div class="legend">Live editor. Änderungen werden in /var/lib/beatbird/settings-overrides.json gespeichert und vom Bridge-Daemon innerhalb von ~5 s übernommen — kein Neustart nötig.</div>

<h2>Palette</h2>
<div class="card">
 <div class="legend">Slots: <code>a</code>=accent, <code>g</code>=glow, <code>d</code>=dim, <code>p</code>=text primary, <code>s</code>=text secondary, <code>e</code>=alert. Leerlassen = Profile-Default.</div>
 <div id="palette"></div>
 <div style="margin-top:.6em">
  <button onclick="deriveFromAccent()">Aus Accent ableiten</button>
  <button onclick="clearPalette()">Auf Profile-Default zurücksetzen</button>
 </div>
</div>

<h2>Standby — RSS feed</h2>
<div class="card">
 <div class="field">
  <label>RSS URL</label>
  <input id="rss_url" type="text" class="url" placeholder="https://www.tagesschau.de/xml/rss2/">
 </div>
 <div class="field">
  <label>Refresh (min)</label>
  <input id="rss_refresh" type="number" min="1" max="1440" step="1" value="30">
 </div>
 <div class="field">
  <label>RSS-Anteil</label>
  <div class="slider-row">
   <input id="rss_weight" type="range" min="0" max="100" step="5" value="50">
   <span id="rss_weight_v" style="font-family:ui-monospace,monospace;width:3em;text-align:right">50%</span>
  </div>
 </div>
 <div class="legend">0% = nur lokale Airport-Board-Sprüche, 100% = nur RSS-Headlines. URL leerlassen schaltet RSS komplett aus.</div>
</div>

<div class="card" style="text-align:right">
 <button class="primary" onclick="save()">Speichern</button>
 <span id="status"></span>
</div>

<script>
 const SLOTS = ['a','g','d','p','s','e'];
 const SLOT_LABEL = {{a:'accent',g:'glow',d:'dim',p:'text primary',s:'text secondary',e:'alert'}};
 let current = {{palette:{{}}, idle:{{}}, overrides:{{}}}};

 function isHex6(s) {{ return typeof s==='string' && /^#[0-9a-f]{{6}}$/i.test(s); }}

 function renderPalette() {{
  const wrap = document.getElementById('palette');
  wrap.innerHTML = '';
  const ovPal = current.overrides && current.overrides.palette || {{}};
  for (const k of SLOTS) {{
   const eff = current.palette[k] || '#000000';
   const overridden = !!ovPal[k];
   const row = document.createElement('div');
   row.className = 'pal';
   row.innerHTML = `
    <label>${{k}} <span style="color:#555">${{SLOT_LABEL[k]}}</span></label>
    <input type="color" data-slot="${{k}}" value="${{eff}}">
    <input type="text" data-slot-hex="${{k}}" value="${{eff}}" maxlength="7">
    <span class="hint">${{overridden ? 'override' : 'profile default'}}</span>`;
   wrap.appendChild(row);
   const c = row.querySelector('input[type=color]');
   const t = row.querySelector('input[type=text]');
   c.addEventListener('input', () => {{ t.value = c.value; }});
   t.addEventListener('input', () => {{ if (isHex6(t.value)) c.value = t.value; }});
  }}
 }}

 function readPalette() {{
  const out = {{}};
  for (const k of SLOTS) {{
   const t = document.querySelector(`input[data-slot-hex="${{k}}"]`);
   const v = (t && t.value || '').trim().toLowerCase();
   if (isHex6(v)) out[k] = v;
  }}
  return out;
 }}

 function shade(hex, pct) {{
  // pct in [-1, +1]; -1 → black, +1 → white. Used by deriveFromAccent.
  const c = hex.startsWith('#') ? hex.slice(1) : hex;
  let r = parseInt(c.slice(0,2),16), g = parseInt(c.slice(2,4),16), b = parseInt(c.slice(4,6),16);
  if (pct >= 0) {{ r += (255-r)*pct; g += (255-g)*pct; b += (255-b)*pct; }}
  else          {{ r *= (1+pct);     g *= (1+pct);     b *= (1+pct); }}
  const h = v => Math.max(0,Math.min(255,Math.round(v))).toString(16).padStart(2,'0');
  return '#'+h(r)+h(g)+h(b);
 }}

 function deriveFromAccent() {{
  const t = document.querySelector('input[data-slot-hex="a"]');
  if (!t || !isHex6(t.value)) {{ alert('Accent (a) muss ein gültiger #rrggbb-Wert sein.'); return; }}
  const a = t.value.toLowerCase();
  const derived = {{
    a: a,
    g: shade(a, +0.35),    // glow — brighter
    d: shade(a, -0.55),    // dim  — darker
    p: '#ffffff',
    s: '#888888',
    e: '#e85050',
  }};
  current.palette = derived;
  current.overrides.palette = derived;
  renderPalette();
 }}

 function clearPalette() {{
  current.overrides.palette = {{}};
  // Re-fetch so we display profile defaults
  load().then(()=>{{}});
 }}

 async function load() {{
  const r = await fetch('/api/settings');
  current = await r.json();
  renderPalette();
  document.getElementById('rss_url').value = current.idle.rss_url || '';
  document.getElementById('rss_refresh').value = current.idle.rss_refresh_minutes || 30;
  const w = Math.round((current.idle.rss_weight ?? 0.5) * 100);
  document.getElementById('rss_weight').value = w;
  document.getElementById('rss_weight_v').textContent = w + '%';
 }}

 document.getElementById('rss_weight').addEventListener('input', e => {{
  document.getElementById('rss_weight_v').textContent = e.target.value + '%';
 }});

 async function save() {{
  const body = {{
    palette: readPalette(),
    idle: {{
      rss_url: document.getElementById('rss_url').value.trim(),
      rss_refresh_minutes: parseInt(document.getElementById('rss_refresh').value, 10),
      rss_weight: parseInt(document.getElementById('rss_weight').value, 10) / 100,
    }},
  }};
  const st = document.getElementById('status');
  st.textContent = 'speichere…';
  try {{
   const r = await fetch('/api/settings', {{method:'POST',
     headers:{{'content-type':'application/json'}},
     body: JSON.stringify(body)}});
   if (!r.ok) throw new Error('HTTP ' + r.status);
   st.textContent = 'gespeichert ✓ (Bridge übernimmt innerhalb 5 s)';
   setTimeout(load, 6000);   // reload after bridge has applied
  }} catch (e) {{
   st.textContent = 'Fehler: ' + e;
  }}
 }}

 load();
</script>
</body></html>
"""


@app.get("/settings", response_class=HTMLResponse)
def settings_page():
    p = _get_profile()
    return _SETTINGS_HTML.format(name=p.identity.friendly_name)


# ─── /bluetooth page — pairing + trusted-device management ──────────────────

_BLUETOOTH_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BeatBird Bluetooth — {name}</title>
<style>
 body{{font:14px/1.45 system-ui,sans-serif;background:#111;color:#eee;max-width:640px;margin:1.2em auto;padding:0 1em 2em}}
 h1{{font-weight:400;letter-spacing:.05em;margin:0 0 .2em}}
 h1 a{{color:#888;font-size:13px;text-decoration:none;float:right}}
 h2{{font-weight:400;font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:#777;margin:1.4em 0 .4em}}
 .card{{background:#1c1c1c;border-radius:6px;padding:.9em 1.1em;margin:.4em 0}}
 .dev{{display:grid;grid-template-columns:1fr auto;gap:.6em;padding:.6em .2em;border-top:1px solid #2a2a2a;align-items:center}}
 .dev:first-child{{border-top:0}}
 .dev .name{{font-weight:500}}
 .dev .meta{{color:#777;font-size:11px;font-family:ui-monospace,monospace;margin-top:.15em}}
 .badge{{display:inline-block;font-size:10px;letter-spacing:.05em;text-transform:uppercase;padding:.15em .5em;border-radius:3px;margin-right:.3em}}
 .badge.on{{background:#1f3a1f;color:#9d9}} .badge.off{{background:#2a2a2a;color:#777}}
 .actions{{white-space:nowrap}}
 button{{background:#2a2a2a;color:#eee;border:0;border-radius:4px;padding:.4em .8em;margin:.1em;cursor:pointer;font-size:12px}}
 button:hover{{background:#3a3a3a}}
 button.primary{{background:#284028;color:#cfc}}
 button.primary:hover{{background:#385038}}
 button.warn{{background:#3a1e1e;color:#e88}}
 button.warn:hover{{background:#4a2a2a}}
 #pair-bar{{display:flex;align-items:center;gap:.8em}}
 #pair-status{{color:#888;font-size:12px}}
 .legend{{color:#666;font-size:11px;margin:.3em 0 .8em}}
 .empty{{color:#666;padding:.6em 0;text-align:center;font-size:12px}}
</style></head>
<body>
<h1>{name} — Bluetooth <a href="/">← Dashboard</a></h1>
<div class="legend">Paired devices auto-reconnect when in range as long as they're <strong>trusted</strong>. Forget removes the pairing entirely on both sides.</div>

<h2>Pairing-Modus</h2>
<div class="card">
 <div id="pair-bar">
  <button class="primary" id="pair-btn" onclick="startPair()">Pairing starten (60 s)</button>
  <span id="pair-status">Adapter offline — klick auf Pairing starten, dann am Handy nach &quot;{name}&quot; suchen.</span>
 </div>
</div>

<h2>Gekoppelte Geräte</h2>
<div class="card" id="device-list">
 <div class="empty">(lade…)</div>
</div>

<script>
 let pairCountdown = 0;
 let pairTimer = null;

 async function load() {{
  const r = await fetch('/api/bluetooth');
  const j = await r.json();
  const wrap = document.getElementById('device-list');
  wrap.innerHTML = '';
  if (!j.devices.length) {{
   wrap.innerHTML = '<div class="empty">Noch keine gekoppelten Geräte.</div>';
   return;
  }}
  for (const d of j.devices) {{
   const row = document.createElement('div');
   row.className = 'dev';
   row.innerHTML = `
    <div>
     <div class="name">${{d.alias || '(unbenannt)'}}</div>
     <div class="meta">
      ${{d.connected ? '<span class="badge on">connected</span>' : '<span class="badge off">offline</span>'}}
      ${{d.trusted   ? '<span class="badge on">trusted</span>'   : '<span class="badge off">untrusted</span>'}}
      <span style="margin-left:.5em">${{d.mac}}</span>
     </div>
    </div>
    <div class="actions">
     <button onclick="act('${{d.mac}}','${{d.trusted ? 'untrust' : 'trust'}}')">${{d.trusted ? 'Untrust' : 'Trust'}}</button>
     ${{d.connected ? `<button onclick="act('${{d.mac}}','disconnect')">Trennen</button>` : ''}}
     <button class="warn" onclick="forget('${{d.mac}}','${{d.alias}}')">Vergessen</button>
    </div>`;
   wrap.appendChild(row);
  }}
 }}

 async function startPair() {{
  const btn = document.getElementById('pair-btn');
  btn.disabled = true;
  btn.textContent = 'Pairing aktiv…';
  try {{
   const r = await fetch('/api/bluetooth/discoverable', {{
     method:'POST',headers:{{'content-type':'application/json'}},
     body: JSON.stringify({{seconds:60}})
   }});
   if (!r.ok) throw new Error('HTTP ' + r.status);
   pairCountdown = 60;
   const status = document.getElementById('pair-status');
   const tick = () => {{
     if (pairCountdown <= 0) {{
       status.textContent = 'Pairing-Fenster geschlossen.';
       btn.disabled = false;
       btn.textContent = 'Pairing starten (60 s)';
       clearInterval(pairTimer);
       load();
       return;
     }}
     status.textContent = `Adapter sichtbar für ${{pairCountdown}} s — am Handy nach &quot;{name}&quot; suchen.`;
     pairCountdown--;
   }};
   tick();
   pairTimer = setInterval(() => {{ tick(); load(); }}, 1000);
  }} catch (e) {{
   document.getElementById('pair-status').textContent = 'Fehler: ' + e;
   btn.disabled = false;
   btn.textContent = 'Pairing starten (60 s)';
  }}
 }}

 async function act(mac, action) {{
  try {{
   const r = await fetch('/api/bluetooth/device', {{
     method:'POST',headers:{{'content-type':'application/json'}},
     body: JSON.stringify({{mac, action}})
   }});
   if (!r.ok) throw new Error('HTTP ' + r.status);
   load();
  }} catch (e) {{
   alert(action + ' fehlgeschlagen: ' + e);
  }}
 }}

 async function forget(mac, alias) {{
  if (!confirm(`&quot;${{alias || mac}}&quot; vergessen? Muss dann neu gekoppelt werden.`)) return;
  await act(mac, 'forget');
 }}

 load();
 setInterval(load, 5000);
</script>
</body></html>
"""


@app.get("/bluetooth", response_class=HTMLResponse)
def bluetooth_page():
    p = _get_profile()
    return _BLUETOOTH_HTML.format(name=p.identity.friendly_name)


def main():
    """Entrypoint for ``beatbird-web`` console script."""
    import uvicorn
    port = int(os.environ.get("BEATBIRD_WEB_PORT", "8080"))
    uvicorn.run("beatbird.webserver:app", host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
