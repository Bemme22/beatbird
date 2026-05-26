#!/usr/bin/env python3
"""
scripts/gen_sounds.py — generate the BeatBird UI sound effect set.

Outputs 7 of the 8 sound slots as ``assets/sounds/*.wav``. The 8th
(``volume.wav``) is the mixkit Long Pop sample committed separately —
this script does NOT overwrite it.

Character: short percussive pops in the style of the volume tick.
Fast attack, exponential decay, fundamental + sub-octave. Each
"voice" is a single pop tone; melodies and chords are built by
sequencing or layering pops at different pitches.

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

def _sine_n(freq: float, n: int) -> List[float]:
    two_pi_over_sr = 2.0 * math.pi / SR
    return [math.sin(two_pi_over_sr * freq * i) for i in range(n)]


def mix(*tracks: Sequence[float]) -> List[float]:
    n = max(len(t) for t in tracks)
    out = [0.0] * n
    for tr in tracks:
        for i in range(len(tr)):
            out[i] += tr[i]
    return out


def save_wav(path: str, samples: Sequence[float], gain: float = 0.18) -> None:
    n = len(samples)
    pcm_iter = (max(-32767, min(32767, int(s * gain * 32767))) for s in samples)
    pcm = struct.pack(f"<{n}h", *pcm_iter)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)


# ─── Pop voice ──────────────────────────────────────────────────────────────
# Approximates the mouth-pop character: very short attack, immediate
# exponential decay, fundamental + sub-octave for body. Total length
# ~80-120 ms — short enough to feel percussive, long enough to read as
# tonal pitch.

def pop(freq: float, dur_s: float = 0.10, body: float = 0.55) -> List[float]:
    n = int(SR * dur_s)
    fund = _sine_n(freq,       n)
    sub  = _sine_n(freq * 0.5, n)
    raw = [a + body * b for a, b in zip(fund, sub)]

    # Tiny smooth attack (~2 ms raised-cosine) so the leading sample
    # doesn't click on the DAC.
    a_n = int(0.002 * SR)
    for i in range(a_n):
        raw[i] *= (1.0 - math.cos(math.pi * i / a_n)) * 0.5

    # Exponential decay over the rest. tau is set so the tail is at
    # ~3 % by end of duration — gives a clean cut without a hard edge.
    decay_n = n - a_n
    tau = dur_s * 0.30
    rate = 1.0 / (tau * SR)
    for i in range(decay_n):
        raw[a_n + i] *= math.exp(-i * rate)

    return raw


def sequence(notes_with_offsets: List[tuple]) -> List[float]:
    """Layer pops at given (freq, dur, start_offset_s) positions.
    Overlapping notes are summed; great for arpeggios where each pop
    rings into the next."""
    rendered = [(pop(f, d), int(SR * off)) for f, d, off in notes_with_offsets]
    total_n = max(off + len(s) for s, off in rendered)
    out = [0.0] * total_n
    for s, off in rendered:
        for i, v in enumerate(s):
            out[off + i] += v
    return out


# ─── Sound recipes ──────────────────────────────────────────────────────────

NOTE = {
    "C3": 130.81, "D3": 146.83, "E3": 164.81, "G3": 196.00, "A3": 220.00,
    "B3": 246.94, "C4": 261.63, "D4": 293.66, "E4": 329.63, "G4": 392.00,
    "A4": 440.00, "C5": 523.25, "E5": 659.25, "G5": 783.99,
}


def boot(outdir: str) -> None:
    """Welcome — a four-pop ascending arpeggio C4 / E4 / G4 / C5,
    each ~140 ms with 90 ms spacing so they bleed slightly into each
    other and build the chord while reading as a melody."""
    notes = [
        (NOTE["C4"], 0.16, 0.00),
        (NOTE["E4"], 0.16, 0.09),
        (NOTE["G4"], 0.16, 0.18),
        (NOTE["C5"], 0.28, 0.27),   # top note held a touch longer
    ]
    save_wav(f"{outdir}/boot.wav", sequence(notes), gain=0.15)


def play(outdir: str) -> None:
    """PLAY — major-third dyad (G3 + B3) struck together, ~120 ms."""
    notes = [
        (NOTE["G3"], 0.14, 0.00),
        (NOTE["B3"], 0.14, 0.00),
    ]
    save_wav(f"{outdir}/play.wav", sequence(notes), gain=0.13)


def pause(outdir: str) -> None:
    """PAUSE — minor-third dyad (G3 + Bb3), slightly shorter than PLAY
    so it reads as 'resting'."""
    notes = [
        (NOTE["G3"], 0.12, 0.00),
        (233.08,     0.12, 0.00),  # Bb3
    ]
    save_wav(f"{outdir}/pause.wav", sequence(notes), gain=0.13)


def skip_next(outdir: str) -> None:
    """NEXT — single bright pop at G4."""
    s = pop(NOTE["G4"], 0.10)
    save_wav(f"{outdir}/skip_next.wav", s, gain=0.14)


def skip_prev(outdir: str) -> None:
    """PREV — single pop at D4 (fifth below NEXT)."""
    s = pop(NOTE["D4"], 0.10)
    save_wav(f"{outdir}/skip_prev.wav", s, gain=0.14)


def bt_connected(outdir: str) -> None:
    """BT pairing — ascending arpeggio C4 / E4 / G4 with overlap,
    similar shape to boot but shorter + only three notes."""
    notes = [
        (NOTE["C4"], 0.14, 0.00),
        (NOTE["E4"], 0.14, 0.10),
        (NOTE["G4"], 0.22, 0.20),
    ]
    save_wav(f"{outdir}/bt_connected.wav", sequence(notes), gain=0.14)


def standby(outdir: str) -> None:
    """Goodnight — two descending pops E3 → C3, the lower one longer
    and warmer. The bass driver gets material."""
    notes = [
        (NOTE["E3"], 0.18, 0.00),
        (NOTE["C3"], 0.32, 0.14),
    ]
    save_wav(f"{outdir}/standby.wav", sequence(notes), gain=0.14)


# ─── Driver ─────────────────────────────────────────────────────────────────

def main() -> None:
    outdir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "sounds",
    )
    # NOTE: volume.wav is NOT regenerated — it's the mixkit Long Pop
    # sample (trimmed) which the user picked as the SFX reference. Any
    # change to volume.wav must happen outside this script.
    boot(outdir)
    play(outdir)
    pause(outdir)
    skip_next(outdir)
    skip_prev(outdir)
    bt_connected(outdir)
    standby(outdir)
    print(f"wrote 7 sounds (volume.wav left untouched) to {outdir}")


if __name__ == "__main__":
    main()
