"""
beatbird.webserver — minimal diagnostics / control UI on http://<host>.local:8080

Entities exposed:
  GET  /              — HTML dashboard (tiny, zero-dep)
  GET  /api/profile   — the active profile (sanitised)
  GET  /api/status    — live status snapshot
  POST /api/volume    — {"pct": 42}
  POST /api/playback  — {"cmd": "PLAY"|"PAUSE"|"NEXT"|"PREV"}
  POST /api/reload    — reload the CamillaDSP config from disk

The webserver does NOT import the running bridge directly. It talks to the
same underlying services (CamillaDSP over websocket, go-librespot over
HTTP, hardware over I2C) — one level of indirection means web and bridge
can be restarted independently.
"""

from __future__ import annotations

import logging
import os
import subprocess

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
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


# ─── API ─────────────────────────────────────────────────────────────────────

class VolumeReq(BaseModel):
    pct: int


class PlaybackReq(BaseModel):
    cmd: str


@app.get("/api/profile")
def get_profile():
    p = _get_profile()
    # Redact anything sensitive (none by design — secrets live elsewhere).
    return p.model_dump()


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
        "spotify": state.__dict__ if state else None,
    }


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
    """Ask camilladsp to re-read /etc/camilladsp/config.yml."""
    try:
        subprocess.run(
            ["systemctl", "reload", "camilladsp"],
            check=False, timeout=5, capture_output=True,
        )
    except Exception as e:
        raise HTTPException(500, f"reload failed: {e}")
    return {"ok": True}


# ─── Dashboard (single HTML blob, no build step) ─────────────────────────────

_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BeatBird — {name}</title>
<style>
 body{{font:14px/1.4 system-ui,sans-serif;background:#111;color:#eee;max-width:560px;margin:2em auto;padding:1em}}
 h1{{font-weight:400;letter-spacing:.05em}}
 .card{{background:#1c1c1c;border-radius:8px;padding:1em 1.2em;margin:.6em 0}}
 .row{{display:flex;justify-content:space-between;margin:.25em 0}}
 .row span:first-child{{color:#888}}
 button{{background:#2a2a2a;color:#eee;border:0;border-radius:4px;padding:.4em .8em;margin:.2em;cursor:pointer}}
 button:hover{{background:#3a3a3a}}
 input[type=range]{{width:100%}}
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
 <div style="margin-top:.6em">
  <button onclick="cmd('PREV')">⏮</button>
  <button onclick="cmd('PLAYPAUSE')">⏯</button>
  <button onclick="cmd('NEXT')">⏭</button>
 </div>
</div>

<div class="card">
 <div class="row"><span>CPU</span><span id="cpu">—</span></div>
 <div class="row"><span>WiFi</span><span id="wifi">—</span></div>
 <div class="row"><span>CamillaDSP</span><span id="dsp">—</span></div>
 <div class="row"><span>Spotify svc</span><span id="sp_svc">—</span></div>
 <div class="row"><span>Amp</span><span id="amp">—</span></div>
 <button onclick="reloadDsp()">Reload DSP config</button>
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
  document.getElementById('dsp').textContent = s.camilladsp ? 'active' : 'stopped';
  document.getElementById('sp_svc').textContent = s.spotify_service ? 'active' : 'stopped';
  document.getElementById('amp').textContent = Object.entries(s.amp||{{}}).map(([k,v])=>`${{k}}=${{v}}`).join(', ') || '—';
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
 }}
 refresh(); setInterval(refresh, 2000);
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
