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
    # eq_editing: True while the web EQ editor is open. The bridge suspends its
    #            per-volume loudness patching so manual freq/gain/q edits to the
    #            production filters aren't overwritten underneath the user.
    return {"palette": None, "idle": None, "loudness": None,
            "dsp_config": None, "friendly_name": None, "eq_editing": None}


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
