"""
settings_overrides.py — runtime-tunable settings layered on top of the profile.

Web UI writes the file, bridge polls its mtime and applies changes live.
Per-profile YAML stays the immutable base configuration (git-tracked,
shared across speakers); this JSON file holds the per-installation tweaks
(palette, RSS feed URL, …) a user makes via the browser settings page.

Path: /var/lib/beatbird/settings-overrides.json (writable per the systemd
unit's ReadWritePaths). Loaded by the bridge on startup and again whenever
its mtime changes.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import Optional

log = logging.getLogger(__name__)

OVERRIDES_PATH = "/var/lib/beatbird/settings-overrides.json"


# ─── Schema ─────────────────────────────────────────────────────────────────
# Keep this tiny — only fields the web UI exposes. Anything else stays in
# the profile YAML. All fields optional; missing = no override.

def empty() -> dict:
    # loudness: {"curve": "smoothstep", "knee_low": 10, "knee_high": 75,
    #            "filters": {"bass_shelf": {"base_gain": 3, "max_boost": 8}, …}}
    # dsp_config: name of a non-production CamillaDSP config to hot-swap to
    #            (e.g. "<speaker>-meas" for REW). None = the profile's
    #            production config. While non-None the bridge suspends loudness
    #            patching so the flat/variant config isn't re-EQ'd underneath.
    # friendly_name: user-label (identity-split phase 4) — a browser rename that
    #            wins over the profile's resolved friendly_name. None = use the
    #            profile/derived name. Drives the BlueZ alias, web title + the
    #            HA device name.
    # eq_editing: LEGACY, ignored since 2026-09-16. The EQ-editor session moved
    #            out of this file to eq-session.json (see the section at the
    #            bottom) because it is transient state that must expire and must
    #            NOT be persisted. Deliberately still listed here: an old value
    #            may sit in a speaker's file — or, worse, in its read-only base
    #            via beatbird-persist-overrides — and nothing must start
    #            honouring it again.
    return {"palette": None, "idle": None, "loudness": None,
            "dsp_config": None, "friendly_name": None, "eq_editing": None}


# ─── Palette: what the firmware fills in for the slots nobody set ───────────
# Mirror of theme.h `Color::*_DEFAULT` and of the two slots
# `Theme::set_accent()` derives from the accent. Lives here (dependency-free)
# rather than in webserver.py so CI can test it without the FastAPI stack.

PALETTE_SLOTS = ("a", "g", "d", "p", "s", "e")

FW_TEXT_DEFAULTS = {"p": "#f4efe0", "s": "#a89e89", "e": "#c73e2c"}


def _rgb(hexstr: str) -> tuple[int, int, int]:
    h = hexstr.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def derive_glow(accent: str) -> str:
    """The accent at full chroma: one gain on all three channels until the
    largest reaches 255 — HSV V→1, hue and saturation untouched. Mirrors
    `Theme::brighten_saturating()`, integer truncation included.

    ⚠️ Never lerp this slot towards white. Its only consumer is the LED
    strip's PLAY state, which renders it at ~full level, and a pastel at full
    level is white light — that was RobinPi's white VU meter (05.09.2026)."""
    r, g, b = _rgb(accent)
    mx = max(r, g, b)
    if not mx:
        return accent
    return "#%02x%02x%02x" % tuple((v * 255 + mx // 2) // mx for v in (r, g, b))


def derive_dim(accent: str) -> str:
    """~25 % of the accent on black — mirrors the firmware's `>> 2`."""
    r, g, b = _rgb(accent)
    return "#%02x%02x%02x" % (r >> 2, g >> 2, b >> 2)


def fill_derived_palette(palette: dict) -> tuple[dict, list[str]]:
    """Complete a merged palette the way the ESP32 does, and report which
    slots that filled in.

    An unset slot is NOT black: with a legacy `PAL:<hex>` line the firmware
    derives glow + dim from the accent and keeps its compile-time constants
    for text/alert. The settings page has to show those, or it draws #000000
    for every slot the profile leaves out — and a plain save then persists
    BLACK as an override (invisible text, dark LED bar)."""
    out = dict(palette)
    derived: list[str] = []
    if out.get("a"):
        for slot, fn in (("g", derive_glow), ("d", derive_dim)):
            if not out.get(slot):
                out[slot] = fn(out["a"])
                derived.append(slot)
    for slot, colour in FW_TEXT_DEFAULTS.items():
        if not out.get(slot):
            out[slot] = colour
            derived.append(slot)
    return out, derived


def merge_palette(current: dict | None, incoming: dict) -> dict | None:
    """Layer `incoming` slots onto the stored palette override, PER SLOT.

    The settings API promises PATCH semantics, and honoured them at the top
    level while *replacing* the set inside `palette`. A request carrying only
    the slots a user had just changed therefore dropped every other override.
    RobinPi ended up with `g` derived from one accent and `d` from another and
    no `a` at all (06.09.2026); the browser had been papering over it by
    re-sending known overrides, which only works while its copy is current and
    never for a hand-written request.

    Rules: a slot that is absent stays as it was, a slot with a valid colour
    replaces it, and a slot present but empty clears it. Clearing the whole
    override is `{}` at the call site, not this function's job. Returns None
    when nothing is left, so the caller stores "no override" rather than an
    empty dict.

    Values are NOT validated here — the caller normalises them first; this
    function only decides what survives."""
    out = dict(current) if isinstance(current, dict) else {}
    for slot in PALETTE_SLOTS:
        if slot not in incoming:
            continue
        value = incoming.get(slot)
        if value:
            out[slot] = value
        else:
            out.pop(slot, None)
    return out or None


def effective_friendly_name(overrides: dict | None, resolved_default: str) -> str:
    """The speaker's shown name (identity-split phase 4): the ``friendly_name``
    override slot (a browser rename) wins; otherwise the profile's resolved
    default. Pure so both the bridge and the webserver — and a CI test — can
    layer the override the same way without importing the other's deps."""
    name = ""
    if isinstance(overrides, dict):
        name = (overrides.get("friendly_name") or "").strip()
    return name or resolved_default


def load(path: str = OVERRIDES_PATH) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return empty()
        return data
    except FileNotFoundError:
        return empty()
    except Exception as e:
        log.warning("settings-overrides load failed: %s", e)
        return empty()


def save(data: dict, path: str = OVERRIDES_PATH) -> None:
    """Atomic write via tempfile + os.replace so a partial write never
    appears as a half-baked override on the next bridge poll."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="settings-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def mtime(path: str = OVERRIDES_PATH) -> Optional[float]:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


# ─── EQ editor session — transient, expiring, NOT an override ───────────────
#
# While the web EQ editor is open the bridge must stop patching the loudness
# filters per volume change, or it overwrites the freq/gain/q the user is
# editing. That "is the editor open" fact used to live in the overrides file
# as `eq_editing: true`, which was wrong in three separate ways:
#
#   * it only ever cleared on the browser's pagehide/beforeunload. A killed
#     tab, a locked phone or a dropped connection left it set, and the bridge
#     then suspended loudness FOREVER with nothing in the UI to say so.
#   * `beatbird-persist-overrides` copies that file verbatim into the
#     read-only base, so hitting "dauerhaft sichern" while it was stuck burned
#     the suspension in across reboots.
#   * every write to the overrides file makes the bridge re-run
#     _apply_overrides, which logs unconditionally — so a heartbeat through
#     that path would have become a steady log spammer, the same self-inflicted
#     pattern as the wifi telemetry.
#
# So it lives here instead: its own tiny file, carrying an absolute expiry, in
# a directory that is tmpfs on the overlayroot speakers (gone on reboot for
# free). The browser refreshes it while the editor is open; when the browser
# stops, it expires on its own. Nothing to leak, nothing to persist.
EQ_SESSION_PATH = "/var/lib/beatbird/eq-session.json"

# How long one heartbeat buys. The browser refreshes well inside this, so the
# only thing the value really sets is "how long a suspension outlives a dead
# browser" — long enough to survive a phone throttling background timers,
# short enough that nobody notices the loudness was off.
EQ_SESSION_TTL_S = 300.0

# Upper bound applied when READING. These Pis have no RTC, so the wall clock
# jumps at the first NTP sync; a backwards jump would otherwise strand an
# expiry far in the future and recreate exactly the stuck-forever bug this
# replaces. An expiry further out than this cannot be honest — ignore it.
EQ_SESSION_MAX_TTL_S = 900.0


def eq_session_open(ttl_s: float = EQ_SESSION_TTL_S,
                    path: str = EQ_SESSION_PATH,
                    now: Optional[float] = None) -> float:
    """Start or refresh the editing session. Idempotent — the heartbeat calls
    exactly this. Returns the new absolute expiry."""
    now = time.time() if now is None else now
    expires_at = now + ttl_s
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="eq-session-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"expires_at": expires_at}, f)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise
    return expires_at


def eq_session_close(path: str = EQ_SESSION_PATH) -> None:
    """End the session now. Best-effort: expiry is the real guarantee, this
    is only the fast path for a browser that got to say goodbye."""
    try:
        os.unlink(path)
    except OSError:
        pass


def eq_session_expiry(path: str = EQ_SESSION_PATH,
                      now: Optional[float] = None) -> Optional[float]:
    """Absolute expiry of a live session, or None if there isn't one.
    Returns None for a file that is missing, unreadable, malformed, already
    expired, or claiming an implausibly distant expiry."""
    try:
        with open(path) as f:
            data = json.load(f)
        expires_at = float(data["expires_at"])
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError) as e:
        log.warning("eq-session unreadable (%s) — treating as closed", e)
        return None
    now = time.time() if now is None else now
    if expires_at <= now:
        return None
    if expires_at - now > EQ_SESSION_MAX_TTL_S:
        log.warning("eq-session expiry %.0fs out (clock jump?) — ignoring",
                    expires_at - now)
        return None
    return expires_at


def eq_session_active(path: str = EQ_SESSION_PATH,
                      now: Optional[float] = None) -> bool:
    return eq_session_expiry(path, now) is not None
