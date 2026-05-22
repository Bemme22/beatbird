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
import subprocess

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from beatbird import system
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
   input.addEventListener('input', () => {{ span.textContent = parseFloat(input.value).toFixed(1) + ' dB'; }});
   input.addEventListener('change', async () => {{
     await fetch('/api/filter', {{method:'POST',headers:{{'content-type':'application/json'}},
       body:JSON.stringify({{name:f.name, gain:parseFloat(input.value)}})}});
   }});
   wrap.appendChild(row);
  }}
 }}

 document.getElementById('vol').addEventListener('change', async e => {{
  await fetch('/api/volume', {{method:'POST',headers:{{'content-type':'application/json'}}, body:JSON.stringify({{pct: +e.target.value}})}});
  refresh();
 }});
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


def main():
    """Entrypoint for ``beatbird-web`` console script."""
    import uvicorn
    port = int(os.environ.get("BEATBIRD_WEB_PORT", "8080"))
    uvicorn.run("beatbird.webserver:app", host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
