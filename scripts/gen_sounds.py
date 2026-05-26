#!/usr/bin/env python3
"""
scripts/gen_sounds.py — generate the BeatBird UI sound effect set.

Outputs eight ~150-1500 ms 16-bit mono 44.1 kHz WAV files to
``assets/sounds/``. Stdlib only (wave + math + struct) so the script
runs on any dev machine that has Python; the Pi never needs to do this
— the WAVs are committed to the repo.

Sound vocabulary: short bell-tones with exponential decay, no plateau
sustain. Multiple partials at inharmonic-ish ratios give body without
the 'kitchen-appliance blip' character pure sines have. Premium-
speaker hardware needs material to chew on — pure sines at moderate
freqs leave the bass driver idle.

Run from the repo root::

    python scripts/gen_sounds.py
"""
from __future__ import annotations

import math
import os
import struct
import wave
from pathlib import Path
from typing import List, Sequence

SR = 44100   # sample rate (Hz)


# ─── Primitives ─────────────────────────────────────────────────────────────

def _sine_n(freq: float, n: int, phase: float = 0.0) -> List[float]:
    two_pi_over_sr = 2.0 * math.pi / SR
    return [math.sin(two_pi_over_sr * freq * i + phase) for i in range(n)]


def silence(dur_s: float) -> List[float]:
    return [0.0] * int(SR * dur_s)


def concat(*parts: Sequence[float]) -> List[float]:
    out: List[float] = []
    for p in parts:
        out.extend(p)
    return out


def mix(*tracks: Sequence[float]) -> List[float]:
    """Add multiple equal-length lists sample-wise. Shorter lists are
    treated as silence past their end."""
    n = max(len(t) for t in tracks)
    out = [0.0] * n
    for tr in tracks:
        for i in range(len(tr)):
            out[i] += tr[i]
    return out


def save_wav(path: str, samples: Sequence[float], gain: float = 0.3) -> None:
    """Quantise to 16-bit PCM with an extra ``gain`` multiplier and
    write a mono WAV. ``gain`` is the headroom-aware final scale."""
    n = len(samples)
    pcm_iter = (max(-32767, min(32767, int(s * gain * 32767)))
                for s in samples)
    pcm = struct.pack(f"<{n}h", *pcm_iter)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)


# ─── Bell synthesis ─────────────────────────────────────────────────────────
# A bell tone = a short smooth attack + multiple partials at slightly-
# inharmonic ratios, each with its own exponential decay. Lower
# partials ring longer; higher ones add the initial 'strike' colour.
# This is the core voice for everything here.

def _attack(samples: List[float], attack_s: float = 0.012) -> List[float]:
    """Smooth raised-cosine ramp on the first ``attack_s`` so a struck
    bell doesn't click. Returns a new list."""
    n = len(samples)
    a_n = min(int(attack_s * SR), n)
    out = list(samples)
    for i in range(a_n):
        # raised-cosine 0→1 ramp = (1 - cos(pi * i / a_n)) / 2
        out[i] *= (1.0 - math.cos(math.pi * i / a_n)) * 0.5
    return out


def _exp_decay(samples: List[float], tau_s: float) -> List[float]:
    """Multiply samples by exp(-t / tau). After tau_s, amplitude is at
    1/e (~37%); after 4·tau_s it's effectively silent."""
    n = len(samples)
    rate = 1.0 / (tau_s * SR)
    return [s * math.exp(-i * rate) for i, s in enumerate(samples)]


def bell(freq: float, dur_s: float, brightness: float = 0.7,
         body: float = 0.4) -> List[float]:
    """Bell-like voice at ``freq``. ``brightness`` controls the upper-
    partial mix (0..1, higher = more bell-strike colour), ``body``
    controls the sub-octave amount (0..1, higher = warmer).

    Partials chosen for warmth, not strict tubular-bell physics —
    a clean musical intuition rather than acoustic accuracy.
    """
    n = int(SR * dur_s)
    # (ratio, amplitude, tau_seconds) per partial
    partials = [
        # fundamental — body of the tone
        (1.00, 1.00,        dur_s * 0.55),
        # sub-octave — weight + warmth
        (0.50, body,        dur_s * 0.80),
        # 2nd harmonic — gives the strike attack its colour
        (2.00, 0.45 * brightness, dur_s * 0.35),
        # slightly-inharmonic upper partial — subtle 'bell' shimmer
        (2.76, 0.18 * brightness, dur_s * 0.22),
        # 3rd harmonic — high glint, decays fast
        (3.00, 0.12 * brightness, dur_s * 0.18),
    ]
    layers = []
    for ratio, amp, tau in partials:
        raw = _sine_n(freq * ratio, n)
        raw = _exp_decay(raw, tau)
        layers.append([s * amp for s in raw])
    summed = mix(*layers)
    return _attack(summed, attack_s=0.015)


# ─── Sound recipes ──────────────────────────────────────────────────────────
# Restraint over information density: most sounds are a single struck
# bell tone; only the welcome and BT-confirmation use a short
# 2-3 note arpeggio. No chirps, no rising sweeps. Lets the speaker's
# bass driver get involved without sliding into ringtone territory.

NOTE = {  # frequencies for the relevant notes
    "C3": 130.81, "D3": 146.83, "E3": 164.81, "G3": 196.00, "A3": 220.00,
    "C4": 261.63, "D4": 293.66, "E4": 329.63, "F4": 349.23, "G4": 392.00,
    "A4": 440.00, "C5": 523.25, "E5": 659.25, "G5": 783.99,
}


def boot(outdir: str) -> None:
    """Welcome — perfect fifth struck together, long ring. Two notes
    instead of a four-note climb so it reads as 'a chord, this thing
    is alive' rather than 'announce yourself fanfare'."""
    n = int(SR * 1.3)
    layer_c = bell(NOTE["C4"], 1.3, brightness=0.6, body=0.5)
    layer_g = bell(NOTE["G4"], 1.3, brightness=0.7, body=0.4)
    out = mix(layer_c, [0.85 * s for s in layer_g])
    save_wav(f"{outdir}/boot.wav", out, gain=0.36)


def volume(outdir: str) -> None:
    """Single soft ping at A3. Shortest sound in the set — 180 ms — but
    with bell decay so it feels musical rather than electronic. Bass
    is in the sub-octave."""
    s = bell(NOTE["A3"], 0.18, brightness=0.4, body=0.6)
    save_wav(f"{outdir}/volume.wav", s, gain=0.28)


def play(outdir: str) -> None:
    """PLAY — major-third struck together (G3 + B3 ~ a happy 'go'
    chord). Quick attack, medium decay."""
    layer_a = bell(NOTE["G3"], 0.5, brightness=0.6, body=0.5)
    layer_b = bell(247.0, 0.5, brightness=0.6, body=0.5)   # B3
    out = mix(layer_a, [0.80 * s for s in layer_b])
    save_wav(f"{outdir}/play.wav", out, gain=0.30)


def pause(outdir: str) -> None:
    """PAUSE — same fundamental as PLAY but with the third dropped a
    semitone (G3 + Bb3 ~ minor third, more 'restful'). Shorter decay
    suggests stopping."""
    layer_a = bell(NOTE["G3"], 0.40, brightness=0.5, body=0.5)
    layer_b = bell(233.08, 0.40, brightness=0.5, body=0.5)  # Bb3
    out = mix(layer_a, [0.80 * s for s in layer_b])
    save_wav(f"{outdir}/pause.wav", out, gain=0.30)


def skip_next(outdir: str) -> None:
    """NEXT — single bell ping at G4 (one fifth above pause), short
    decay. No two-tone — felt mechanical."""
    s = bell(NOTE["G4"], 0.25, brightness=0.7, body=0.3)
    save_wav(f"{outdir}/skip_next.wav", s, gain=0.30)


def skip_prev(outdir: str) -> None:
    """PREV — single bell ping at D4 (one fifth below NEXT)."""
    s = bell(NOTE["D4"], 0.25, brightness=0.7, body=0.3)
    save_wav(f"{outdir}/skip_prev.wav", s, gain=0.30)


def bt_connected(outdir: str) -> None:
    """BT pairing complete — a clean ascending arpeggio C4 / E4 / G4,
    each ringing into the next. ~200 ms per note with 50 ms overlap."""
    parts_dur = [(NOTE["C4"], 0.55), (NOTE["E4"], 0.55), (NOTE["G4"], 0.80)]
    notes = []
    for f, d in parts_dur:
        notes.append(bell(f, d, brightness=0.7, body=0.4))
    # Layer them with a 0.18 s offset so they overlap and ring together.
    offset_s = 0.18
    offset_n = int(SR * offset_s)
    total_n = offset_n * (len(notes) - 1) + len(notes[-1])
    out = [0.0] * total_n
    for i, n in enumerate(notes):
        start = i * offset_n
        amp = 1.0 if i == len(notes) - 1 else 0.85
        for j, s in enumerate(n):
            out[start + j] += s * amp
    save_wav(f"{outdir}/bt_connected.wav", out, gain=0.32)


def standby(outdir: str) -> None:
    """Goodnight — two-note descent E3 → C3, the lower one held longer.
    Bass-heavy by design; the speaker is going to sleep."""
    e3 = bell(NOTE["E3"], 0.45, brightness=0.4, body=0.7)
    c3 = bell(NOTE["C3"], 0.95, brightness=0.4, body=0.8)
    # Slight overlap so the descent feels smooth.
    offset_n = int(SR * 0.30)
    total_n = offset_n + len(c3)
    out = [0.0] * total_n
    for j, s in enumerate(e3):
        if j < total_n:
            out[j] += s
    for j, s in enumerate(c3):
        out[offset_n + j] += 0.95 * s
    save_wav(f"{outdir}/standby.wav", out, gain=0.30)


# ─── Driver ─────────────────────────────────────────────────────────────────

def main() -> None:
    outdir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "sounds",
    )
    boot(outdir)
    volume(outdir)
    play(outdir)
    pause(outdir)
    skip_next(outdir)
    skip_prev(outdir)
    bt_connected(outdir)
    standby(outdir)
    print(f"wrote 8 sounds to {outdir}")


if __name__ == "__main__":
    main()
