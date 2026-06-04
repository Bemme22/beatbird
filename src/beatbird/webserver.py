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

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from beatbird import settings_overrides, system
from beatbird.sources import bluetooth as bt
from beatbird.audio.camilladsp import CamillaDSP, db_to_pct, pct_to_db
from beatbird.audio import loudness
from beatbird.audio import dsp_configs
from beatbird.config import load_profile
from beatbird.hardware import louder_hat
from beatbird.sources.spotify import SpotifyClient

log = logging.getLogger("beatbird.web")

app = FastAPI(title="BeatBird")

# Static (pico.css + htmx) + Jinja templates live alongside the bridge
# code under src/beatbird/web/ so the editable install picks them up
# without extra path tricks. Relative to webserver.py for portability.
_WEB_DIR = Path(__file__).parent / "web"
app.mount("/static", StaticFiles(directory=_WEB_DIR / "static"), name="static")
templates = Jinja2Templates(directory=_WEB_DIR / "templates")
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


def _display_name() -> str:
    """The speaker's shown name: a browser rename (settings-override) wins,
    else the profile's resolved friendly_name. Read fresh from disk each call
    so a rename shows up without restarting the (profile-caching) webserver."""
    return settings_overrides.effective_friendly_name(
        settings_overrides.load(), _get_profile().resolved_friendly_name)


def _hardware():
    p = _get_profile()
    if p.soundcard.driver.startswith("louder-hat"):
        return louder_hat.from_profile(p.soundcard)
    from beatbird.hardware.base import NullHardware
    return NullHardware()


def _vol_params():
    """(min_db, max_db, gamma) from the active profile's volume curve.

    The bridge + display map the 0..100 slider through these; the web UI must
    use the SAME params or its % drifts from the display and a drag can push
    past the profile's max_db. Spread into pct_to_db()/db_to_pct()."""
    v = _get_profile().audio.volume
    return v.min_db, v.max_db, v.curve_gamma


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
    friendly_name: str | None = None  # user rename; "" clears back to the profile/derived name


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
    vol_pct = db_to_pct(db, *_vol_params()) if db is not None else 0
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
    _dsp.set_volume_db(pct_to_db(req.pct, *_vol_params()))
    return {"ok": True, "pct": req.pct}


@app.get("/api/volume")
def get_volume():
    """Just the current volume %. Cheap (one cached-WS read) so the dashboard
    can poll it to track the display knob / other clients without hitting the
    full /api/status (which also reads hardware I2C + 3 service probes)."""
    db = _dsp.get_volume_db()
    return {"pct": db_to_pct(db, *_vol_params()) if db is not None else 0}


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

    identity = {
        # The custom name the user has set (empty = none), plus the
        # profile/derived default so the form can show it as a placeholder.
        "friendly_name": (ov.get("friendly_name") or ""),
        "default": p.resolved_friendly_name,
    }

    return {"palette": palette, "idle": idle, "identity": identity, "overrides": ov}


# ─── Web theme — mirror the speaker's display palette into CSS ────────────────
# theme.h is the firmware source of truth (Nothing-Glyph aesthetic on the round
# AMOLED). The web UI echoes it so the browser feels like an extension of the
# display: pure-black bg, Departure Mono, cream/linen text, champagne accent.
# The accent (and any pushed palette slots) come live from profile + overrides
# so a colour the user sets for the speaker also retints the web UI.

# Compile-time fallbacks copied verbatim from firmware theme.h Color::*_DEFAULT.
_THEME_FALLBACK = {
    "a": "F0CB7B",  # accent — champagne gold
    "g": "FFE6B3",  # glow   — brighter champagne
    "d": "3C321E",  # dim    — ~25% accent on black
    "p": "F4EFE0",  # text primary — cream
    "s": "A89E89",  # text secondary — linen
    "e": "C73E2C",  # alert  — rust
}


def _shade(hex6: str, pct: float) -> str:
    """Lighten (pct>0, toward white) or darken (pct<0, toward black) a bare
    rrggbb string. Mirrors the settings page's deriveFromAccent() so a glow/dim
    we synthesise here matches what the speaker derives."""
    r, g, b = int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16)
    if pct >= 0:
        r, g, b = r + (255 - r) * pct, g + (255 - g) * pct, b + (255 - b) * pct
    else:
        r, g, b = r * (1 + pct), g * (1 + pct), b * (1 + pct)
    return "{:02X}{:02X}{:02X}".format(
        *(max(0, min(255, round(v))) for v in (r, g, b)))


def _theme_ctx() -> dict:
    """Effective display palette → CSS-ready hex tokens. Registered as the
    Jinja global ``theme()`` so base.html can set its CSS variables from it."""
    p = _get_profile()
    ov = settings_overrides.load()
    ov_pal = ov.get("palette") if isinstance(ov.get("palette"), dict) else {}

    prof = {
        "a": p.display.accent_color, "g": p.display.accent_glow,
        "d": p.display.accent_dim,   "p": p.display.text_primary,
        "s": p.display.text_secondary, "e": p.display.accent_alert,
    }

    def slot(k: str) -> str | None:
        v = _hex6(ov_pal.get(k)) or _hex6(prof.get(k))   # "#rrggbb" or None
        return v[1:].upper() if v else None

    accent = slot("a") or _THEME_FALLBACK["a"]
    glow   = slot("g") or _shade(accent, 0.30)
    dim    = slot("d") or _shade(accent, -0.70)
    textp  = slot("p") or _THEME_FALLBACK["p"]
    texts  = slot("s") or _THEME_FALLBACK["s"]
    alert  = slot("e") or _THEME_FALLBACK["e"]

    r, g, b = int(accent[0:2], 16), int(accent[2:4], 16), int(accent[4:6], 16)
    # Relative luminance → dark ink on a light accent, cream on a dark one,
    # so button text stays legible whatever palette the user picks.
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    inverse = "1A1205" if lum > 140 else "F4EFE0"

    return {
        "accent": "#" + accent, "glow": "#" + glow, "dim": "#" + dim,
        "text": "#" + textp, "text2": "#" + texts, "alert": "#" + alert,
        "inverse": "#" + inverse, "accent_rgb": f"{r},{g},{b}",
    }


# Expose to every template (used by base.html). Recomputed per render so a
# live palette override retints the UI on the next page load.
templates.env.globals["theme"] = _theme_ctx


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

    if req.friendly_name is not None:
        # Trim + cap; empty string clears the override back to the profile name.
        name = req.friendly_name.strip()[:48]
        out["friendly_name"] = name or None

    try:
        settings_overrides.save(out)
    except Exception as e:
        raise HTTPException(500, f"settings save failed: {e}")
    return {"ok": True, "overrides": out}


# ─── Loudness voicing (Bass laut / Bass leise / Kurve) ───────────────────────

class LoudnessReq(BaseModel):
    curve: str | None = None
    knee_low: int | None = None
    knee_high: int | None = None
    # {"bass_shelf": {"base_gain": 3, "max_boost": 8}, …}
    filters: dict | None = None
    reset: bool | None = None


@app.get("/api/loudness")
def get_loudness():
    """Effective loudness voicing — DEFAULT_BASE + profile (which filters +
    max_boost) + overrides, i.e. exactly what the bridge runs. Per filter:
    base_gain = 'Bass laut' (gain at high volume), quiet = base+max_boost =
    'Bass leise' (gain at the lowest volume)."""
    p = _get_profile()
    if not p.audio.loudness.enabled or not p.audio.loudness.filters:
        return {"enabled": False, "filters": [], "curve": "smoothstep",
                "knee_low": loudness.DEFAULT_KNEE_LOW,
                "knee_high": loudness.DEFAULT_KNEE_HIGH}
    filters, curve, knee_low, knee_high = loudness.build_loudness(
        p, settings_overrides.load())
    return {
        "enabled": True, "curve": curve,
        "knee_low": knee_low, "knee_high": knee_high,
        "filters": [{
            "name": f.name, "freq": f.freq,
            "base_gain": round(f.base_gain, 1),
            "max_boost": round(f.max_boost, 1),
            "quiet": round(f.base_gain + f.max_boost, 1),
        } for f in filters],
    }


@app.post("/api/loudness")
def set_loudness(req: LoudnessReq):
    """Persist the loudness voicing override. PATCH semantics — only the
    filters present are touched. The bridge applies it live on its next
    mtime poll (≤ 5 s); it survives reboot. POST {"reset": true} clears it."""
    out = settings_overrides.load()
    if req.reset:
        out["loudness"] = None
        try:
            settings_overrides.save(out)
        except Exception as e:
            raise HTTPException(500, f"loudness save failed: {e}")
        return {"ok": True, "overrides": None}

    p = _get_profile()
    allowed = {f.name for f in p.audio.loudness.filters}
    cur = out.get("loudness") or {}
    cur_filters = dict(cur.get("filters") or {})
    if req.filters:
        for name, vals in req.filters.items():
            if name not in allowed or not isinstance(vals, dict):
                continue
            entry = dict(cur_filters.get(name) or {})
            for key in ("base_gain", "max_boost"):
                if key in vals:
                    try:
                        entry[key] = max(-20.0, min(20.0, float(vals[key])))
                    except (TypeError, ValueError):
                        pass
            cur_filters[name] = entry

    curve = req.curve or cur.get("curve") or p.audio.loudness.curve
    if curve not in ("smoothstep", "legacy"):
        curve = "smoothstep"
    knee_low = int(req.knee_low if req.knee_low is not None
                   else cur.get("knee_low", loudness.DEFAULT_KNEE_LOW))
    knee_high = int(req.knee_high if req.knee_high is not None
                    else cur.get("knee_high", loudness.DEFAULT_KNEE_HIGH))
    knee_low = max(0, min(100, knee_low))
    knee_high = max(knee_low + 1, min(100, knee_high))

    out["loudness"] = {"curve": curve, "knee_low": knee_low,
                       "knee_high": knee_high, "filters": cur_filters}
    try:
        settings_overrides.save(out)
    except Exception as e:
        raise HTTPException(500, f"loudness save failed: {e}")
    return {"ok": True, "overrides": out["loudness"]}


@app.get("/ui/advanced/loudness", response_class=HTMLResponse)
def ui_advanced_loudness(request: Request):
    return templates.TemplateResponse(request, "_advanced_loudness.html",
                                      {"l": get_loudness()})


@app.post("/api/persist")
def persist_overrides():
    """Copy the live settings-overrides (palette / idle / loudness voicing) onto
    the persistent disk so browser tweaks survive a reboot on overlayroot=tmpfs.
    No-op on a plain rw root. Calls the sudoers-allowed helper installed by
    install/55-web-sudo.sh."""
    if not os.path.exists("/usr/local/sbin/beatbird-persist-overrides"):
        raise HTTPException(
            503,
            "Persist-Helper noch nicht installiert — install/55-web-sudo.sh "
            "ausführen (geschieht beim nächsten Provisioning).",
        )
    try:
        r = subprocess.run(
            ["sudo", "/usr/local/sbin/beatbird-persist-overrides"],
            check=False, timeout=25, capture_output=True, text=True,
        )
    except Exception as e:
        raise HTTPException(500, f"persist failed: {e}")
    if r.returncode != 0:
        raise HTTPException(500, f"persist failed: {(r.stderr or r.stdout).strip()}")
    return {"ok": True, "msg": (r.stdout.strip() or "persisted")}


# ─── DSP config switcher (measurement / variant hot-swap) ────────────────────

class DspConfigReq(BaseModel):
    name: str   # a stem from this speaker's switchable set, or the production name


def _dsp_label(name: str, production: str) -> str:
    """Friendly label for a config stem-name."""
    if name == production:
        return "Produktion (Voicing)"
    suffix = name[len(production):].lstrip("-_") if name.startswith(production) else name
    if suffix in ("meas", "measure", "measurement"):
        return "Messmodus (flat, REW)"
    return suffix.replace("-", " ").replace("_", " ").title() or name


@app.get("/api/dsp-configs")
def get_dsp_configs():
    """The CamillaDSP configs this speaker can hot-swap between + which is
    active. Active = the dsp_config override, else the production config."""
    production = _get_profile().audio.camilladsp_config
    active = settings_overrides.load().get("dsp_config") or production
    names = dsp_configs.list_configs(production)
    return {
        "production": production,
        "active": active,
        "flat_active": active != production,
        "configs": [
            {"name": n, "label": _dsp_label(n, production),
             "is_production": n == production, "active": n == active}
            for n in names
        ],
    }


@app.post("/api/dsp-config")
def set_dsp_config(req: DspConfigReq):
    """Request a DSP config switch. Writes the dsp_config override; the bridge
    hot-swaps CamillaDSP on its next poll (≤5 s) and suspends loudness while a
    non-production config is active. Selecting the production config clears the
    override."""
    production = _get_profile().audio.camilladsp_config
    if req.name != production and not dsp_configs.is_valid(production, req.name):
        raise HTTPException(400, f"unknown DSP config {req.name!r} for this speaker")
    out = settings_overrides.load()
    out["dsp_config"] = None if req.name == production else req.name
    try:
        settings_overrides.save(out)
    except Exception as e:
        raise HTTPException(500, f"dsp_config save failed: {e}")
    return {"ok": True, "active": req.name, "flat_active": req.name != production}


@app.get("/api/dsp-health")
def get_dsp_health():
    """Live headroom telemetry: clipped-sample count + processing load. A
    rising clipped count during a bass-heavy track is the click/pop smoking
    gun. Cheap (cached-WS reads)."""
    return {
        "clipped": _dsp.get_clipped_samples(),
        "load": _dsp.get_processing_load(),
    }


@app.get("/ui/advanced/dsp", response_class=HTMLResponse)
def ui_advanced_dsp(request: Request):
    return templates.TemplateResponse(request, "_advanced_dsp.html",
                                      {"dsp": get_dsp_configs()})


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


# ─── /api/diag — extra diagnostics for the /advanced page ───────────────────
#
# Surfaces stuff that today only shows up in the bridge journal: firmware
# version, BT codec of the active link, snapcast stream details, disk +
# memory. The dashboard ignores this endpoint; only /advanced consumes it.

def _read_fw_version() -> str:
    try:
        with open("/var/lib/beatbird/firmware-version") as f:
            return f.read().strip() or "?"
    except OSError:
        return "?"


def _read_uptime_seconds() -> int:
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except (OSError, ValueError):
        return 0


def _read_meminfo() -> dict:
    out: dict = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1)
                    out[k.strip()] = v.strip()
    except OSError:
        return {}
    return out


def _disk_free_root() -> dict:
    """`/` is overlay tmpfs on the speakers — what shrinks is RAM-backed.
    We report both the usable / view and /media/root-rw (the tmpfs upper
    layer) so users can see what's actually free."""
    import shutil
    out: dict = {}
    try:
        u = shutil.disk_usage("/")
        out["root_total_mb"] = u.total // (1024 * 1024)
        out["root_used_mb"]  = u.used  // (1024 * 1024)
        out["root_free_mb"]  = u.free  // (1024 * 1024)
    except OSError:
        pass
    try:
        u = shutil.disk_usage("/media/root-rw")
        out["upper_total_mb"] = u.total // (1024 * 1024)
        out["upper_used_mb"]  = u.used  // (1024 * 1024)
        out["upper_free_mb"]  = u.free  // (1024 * 1024)
    except OSError:
        pass
    return out


@app.get("/api/diag")
def diag():
    mi = _read_meminfo()
    mem_total = mi.get("MemTotal", "")
    mem_avail = mi.get("MemAvailable", "")
    # bt active codec via the bridge's BT source state if available
    bt_codec = None
    bt_alias = None
    try:
        active = bt._list_connected_devices()  # type: ignore
        if active:
            bt_alias = active[0].alias
    except Exception:
        pass
    # snapcast — bridge's poll already keeps state; expose via the
    # bridge's state file or by re-querying the server. Cheap re-query:
    snap = {}
    try:
        snap_host = os.environ.get("BEATBIRD_SNAPCAST_SERVER", "").strip()
        if snap_host:
            from beatbird.sources.snapcast import SnapcastClient, get_local_wlan_mac
            mac = get_local_wlan_mac()
            if mac:
                cli = SnapcastClient(host=snap_host, my_mac=mac)
                st = cli.get_state()
                if st:
                    snap = {
                        "server":     snap_host,
                        "group":      st.get("group_name") or "—",
                        "playing":    bool(st.get("playing")),
                        "stream":     st.get("stream") or "—",
                        "title":      st.get("title") or "",
                        "artist":     st.get("artist") or "",
                        "volume_pct": st.get("volume_pct"),
                    }
    except Exception as e:
        snap = {"error": str(e)}
    return {
        "firmware_version": _read_fw_version(),
        "uptime_s":         _read_uptime_seconds(),
        "mem_total":        mem_total,
        "mem_available":    mem_avail,
        "disk":             _disk_free_root(),
        "bt": {
            "connected_alias": bt_alias,
            # Codec isn't directly exposed by bluez-alsa without a deeper
            # dbus call; placeholder for now — journal shows it but
            # surfacing means parsing org.bluealsa.PCM1 properties.
            "codec":           None,
        },
        "snapcast": snap,
    }


# ─── Dashboard ───────────────────────────────────────────────────────────────
#
# / is the user-facing minimal dashboard (Now Playing, Volume, Bluetooth).
# /advanced is the technical view (logs, loudness, services, system).
# Both render Jinja2 templates from web/templates/, talk to htmx-friendly
# /ui/* partial endpoints. The legacy /api/* endpoints stay untouched so
# external integrations and the old inline JS pages keep working.

def _bt_context() -> dict:
    """Snapshot of paired/connected BT state in the shape the templates
    expect. Cheap (single bluetoothctl invocation + one is_discoverable
    check)."""
    paired = bt.list_paired_devices()
    connected = next((d for d in paired if d.connected), None)
    return {
        "paired":       paired,
        "connected":    connected,
        "discoverable": bt.is_discoverable(),
        # Time-left during a pairing window is not directly exposed by
        # bluez — bluetoothctl just returns the boolean. Showing a fixed
        # "wait" is good enough; the bridge closes the window after
        # actual pair success anyway.
        "discoverable_seconds_left": None,
    }


def _status_for_template() -> dict:
    """Same shape get_status returns but post-processed for the template
    layer: playback as a string ("Playing"/"Paused"/"Stopped"), source as
    a tag, title/artist flattened from the nested spotify state."""
    raw = get_status()
    sp = raw.get("spotify") or {}
    if sp and not sp.get("stopped"):
        playback = "Paused" if sp.get("paused") else "Playing"
        source = "spotify"
        title = sp.get("title") or ""
        artist = sp.get("artist") or ""
    else:
        playback = "Stopped"
        source = "none"
        title = ""
        artist = ""
    # BT-active overrides Spotify-stopped: if a phone is streaming the
    # bridge sees source=bluetooth in its own state. The web layer can't
    # query the bridge directly, so we just check whether any device is
    # currently connected — close enough for the dashboard pill.
    try:
        if any(d.connected for d in bt.list_paired_devices()):
            source = "bluetooth"
    except Exception:
        pass
    return {
        **raw,
        "playback": playback,
        "source":   source,
        "title":    title,
        "artist":   artist,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {
        "name":    _display_name(),
        "status":  _status_for_template(),
        "bt":      _bt_context(),
    })


@app.get("/advanced", response_class=HTMLResponse)
def advanced(request: Request):
    return templates.TemplateResponse(request, "advanced.html", {
        "name":    _display_name(),
        "status":  _status_for_template(),
        "diag":    diag(),
    })


# ─── /ui/* — htmx partial responses (HTML, not JSON) ────────────────────────

@app.get("/ui/now-playing", response_class=HTMLResponse)
def ui_now_playing(request: Request):
    return templates.TemplateResponse(request, "_now_playing.html", {
        "status":  _status_for_template(),
    })


@app.get("/ui/bluetooth", response_class=HTMLResponse)
def ui_bluetooth(request: Request):
    return templates.TemplateResponse(request, "_bluetooth.html", {
        "name":    _display_name(),
        "bt":      _bt_context(),
    })


@app.post("/ui/cmd", response_class=HTMLResponse)
def ui_cmd(request: Request, c: str):
    """Single-endpoint playback command (PLAY/PAUSE/PLAYPAUSE/NEXT/PREV).
    Re-renders the Now Playing partial so the dashboard reflects the new
    state immediately. State may briefly lag (Spotify takes 100-300 ms
    to settle) — htmx's 2-second poll will correct any miss."""
    cmd = c.upper()
    if cmd not in ("PLAY", "PAUSE", "PLAYPAUSE", "NEXT", "PREV", "STOP"):
        raise HTTPException(400, f"bad cmd {cmd!r}")
    # The legacy /api/playback handler uses a Pydantic body; emulate by
    # going straight to the spotify client. Same behaviour, no body
    # parsing detour.
    try:
        if cmd == "PLAY":           _spotify.play()
        elif cmd == "PAUSE":        _spotify.pause()
        elif cmd == "PLAYPAUSE":
            st = _spotify.get_state()
            if st and not st.stopped and not st.paused:
                _spotify.pause()
            else:
                _spotify.play()
        elif cmd == "NEXT":         _spotify.next()
        elif cmd == "PREV":         _spotify.prev()
        elif cmd == "STOP":         _spotify.close_session()
    except Exception as e:
        log.warning("ui_cmd %s failed: %s", cmd, e)
    return templates.TemplateResponse(request, "_now_playing.html", {
        "status":  _status_for_template(),
    })


@app.post("/ui/vol")
async def ui_vol(request: Request):
    """htmx form-encoded slider POST. Body: pct=42."""
    form = await request.form()
    try:
        pct = int(float(form.get("pct") or 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "bad pct")
    pct = max(0, min(100, pct))
    _dsp.set_volume_db(pct_to_db(pct, *_vol_params()))
    return HTMLResponse("")  # hx-swap="none" — slider handles its own UI


@app.post("/ui/bluetooth/pair", response_class=HTMLResponse)
def ui_bluetooth_pair(request: Request):
    try:
        bt.set_discoverable(True, timeout_s=60)
    except Exception as e:
        log.error("ui_bluetooth_pair: %s", e)
    return templates.TemplateResponse(request, "_bluetooth.html", {
        "name":    _display_name(),
        "bt":      _bt_context(),
    })


@app.post("/ui/bluetooth/forget", response_class=HTMLResponse)
def ui_bluetooth_forget(request: Request, mac: str):
    mac = _validate_mac(mac)
    try:
        bt.forget_device(mac)
    except Exception as e:
        log.error("ui_bluetooth_forget %s: %s", mac, e)
    return templates.TemplateResponse(request, "_bluetooth.html", {
        "name":    _display_name(),
        "bt":      _bt_context(),
    })


# ─── /ui/advanced/* ─────────────────────────────────────────────────────────

@app.get("/ui/advanced/system", response_class=HTMLResponse)
def ui_advanced_system(request: Request):
    return templates.TemplateResponse(request, "_advanced_system.html", {
        "status":  _status_for_template(),
        "diag":    diag(),
    })


@app.get("/ui/advanced/snapcast", response_class=HTMLResponse)
def ui_advanced_snapcast(request: Request):
    return templates.TemplateResponse(request, "_advanced_snapcast.html", {
        "diag":    diag(),
    })


@app.get("/ui/advanced/filters", response_class=HTMLResponse)
def ui_advanced_filters(request: Request):
    return templates.TemplateResponse(request, "_advanced_filters.html", {
        "filters": get_filters().get("filters", []),
    })


@app.post("/ui/advanced/service/{name}")
def ui_advanced_service(name: str):
    if name not in _ALLOWED_SERVICES:
        raise HTTPException(400, f"unit {name!r} not allowed")
    try:
        subprocess.run(["sudo", "-n", "/usr/bin/systemctl", "restart", name],
                       capture_output=True, timeout=10)
    except Exception as e:
        log.error("service restart %s: %s", name, e)
    return HTMLResponse("")


@app.post("/ui/advanced/system/{action}")
def ui_advanced_system_action(action: str):
    if action not in ("reboot", "shutdown"):
        raise HTTPException(400, f"action {action!r} not allowed")
    try:
        if action == "reboot":
            subprocess.Popen(["sudo", "-n", "/usr/bin/systemctl", "reboot"])
        else:
            subprocess.Popen(["sudo", "-n", "/sbin/poweroff"])
    except Exception as e:
        log.error("system %s: %s", action, e)
    return HTMLResponse("")


# ─── Legacy inline HTML blob — replaced by templates/dashboard.html.
# Kept here only because removing it would require a parallel surgery
# on the duplicated `@app.get("/")` route below. The route was already
# replaced by the Jinja-based dashboard() above; this old definition is
# now unreachable (FastAPI uses the first match) but harmless. Marking
# both for deletion in a follow-up to keep this commit focused.


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
    return _HEALTH_HTML.format(name=_display_name())



@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", {
        "name": _display_name(),
    })



@app.get("/bluetooth", response_class=HTMLResponse)
def bluetooth_page(request: Request):
    return templates.TemplateResponse(request, "bluetooth.html", {
        "name": _display_name(),
    })


def main():
    """Entrypoint for ``beatbird-web`` console script."""
    import uvicorn
    port = int(os.environ.get("BEATBIRD_WEB_PORT", "8080"))
    uvicorn.run("beatbird.webserver:app", host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
