"""
beatbird.audio.sfx — UI sound-effect playback.

Plays short WAV blips for UI feedback (boot jingle, volume tick,
play/pause, skip, BT connect, standby). Routes via the same ALSA
Loopback CamillaDSP captures from, so SFX go through the speaker's
master volume + EQ pipeline — a volume tick at 30 % master is quieter
than the same tick at 80 %, which is exactly the Libratone-style
'now you know how loud it'll be' UX.

Best-effort: failures log a warning but never raise. Each play()
spawns aplay as a detached process, so the bridge doesn't block on the
~150 ms playback latency.

Volume taps are throttled — set_volume can fire dozens of times per
second during a rotary gesture, and we don't want the ring to sound
like an old fax machine.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Optional

log = logging.getLogger("beatbird.sfx")

# Default install path; install/70-bridge.sh copies assets/sounds/ here.
SYSTEM_SOUNDS_DIR = "/usr/share/beatbird/sounds"
# Fallback for dev: assets/sounds/ next to the repo, found relative to
# this file (src/beatbird/audio/sfx.py → repo root → assets/sounds).
_HERE = os.path.dirname(os.path.abspath(__file__))
DEV_SOUNDS_DIR = os.path.abspath(
    os.path.join(_HERE, "..", "..", "..", "assets", "sounds")
)

# ALSA target. plughw wraps hw:Loopback,0 with the plug plugin which
# handles format + rate conversion — our WAVs are 16-bit mono 44.1 kHz
# but the Loopback playback side accepts only S32_LE at the configured
# sample rate. Raw hw:Loopback or dmix:Loopback wouldn't convert.
# Loopback supports multiple substreams so this can co-exist with the
# music sources writing to the same card.
DEFAULT_DEVICE = "plughw:CARD=Loopback,DEV=0"

# Minimum interval between successive volume ticks. set_volume fires
# many times per second during a rotary gesture; without this guard the
# ring sounds like a buzzer.
VOLUME_THROTTLE_S = 0.10


class SoundEffects:
    def __init__(self, enabled: bool,
                 device: str = DEFAULT_DEVICE,
                 sounds_dir: Optional[str] = None):
        self.enabled = enabled
        self.device  = device

        if sounds_dir:
            self.sounds_dir = sounds_dir
        elif os.path.isdir(SYSTEM_SOUNDS_DIR):
            self.sounds_dir = SYSTEM_SOUNDS_DIR
        else:
            self.sounds_dir = DEV_SOUNDS_DIR

        if self.enabled and not os.path.isdir(self.sounds_dir):
            log.warning("sfx: sounds dir not found at %s — disabling",
                        self.sounds_dir)
            self.enabled = False

        # Per-sound throttle so repeated triggers (rotary volume, fast
        # taps) don't queue up multiple aplay processes.
        self._last_played_at: dict[str, float] = {}
        self._throttle_s: dict[str, float] = {
            "volume": VOLUME_THROTTLE_S,
        }

        if self.enabled:
            log.info("sfx enabled (device=%s, sounds=%s)",
                     self.device, self.sounds_dir)

    def play(self, name: str) -> None:
        """Play sound ``name`` (no extension). Detached; returns
        immediately. Throttled per-sound by self._throttle_s."""
        if not self.enabled:
            return
        throttle = self._throttle_s.get(name, 0.0)
        now = time.monotonic()
        if throttle > 0.0:
            last = self._last_played_at.get(name, 0.0)
            if now - last < throttle:
                return
        self._last_played_at[name] = now

        path = os.path.join(self.sounds_dir, f"{name}.wav")
        if not os.path.exists(path):
            log.debug("sfx: missing %s", path)
            return

        try:
            subprocess.Popen(
                ["aplay", "-q", "-D", self.device, path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            log.warning("sfx: aplay not installed, disabling")
            self.enabled = False
        except Exception as e:
            log.warning("sfx: play(%s) failed: %s", name, e)
