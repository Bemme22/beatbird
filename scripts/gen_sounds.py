#!/usr/bin/env python3
"""
scripts/gen_sounds.py — generate the BeatBird UI sound effect set.

Outputs eight 16-bit mono 44.1 kHz WAV files to ``assets/sounds/``.

Design philosophy: ambient, melodic, never startling. Every sound is a
short pad — fundamental + sub-octave + perfect fifth, all swelling in
with a raised-cosine attack (no percussive strike) and fading out with
an exponential-ish tail. No inharmonic partials, no chirps, no sweeps.
The interaction is the foreground; the sound just colours the moment.

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


def concat(*parts: Sequence[float]) -> List[float]:
    out: List[float] = []
    for p in parts:
        out.extend(p)
    return out


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


# ─── Envelopes ──────────────────────────────────────────────────────────────

def swell_env(n: int, attack_frac: float = 0.25) -> List[float]:
    """Raised-cosine swell up (no plateau), then raised-cosine fade out.
    No sharp onset — the sound 'arrives' rather than 'hits'.

    ``attack_frac`` is the fraction of total samples spent on the
    swell-in; the rest is the fade-out. 0.25 = 25 % rise / 75 % fall =
    musical 'attack-release' shape without a sustain plateau.
    """
    a_n = max(1, int(n * attack_frac))
    out: List[float] = [0.0] * n
    # Attack: 0 → 1 raised cosine
    for i in range(a_n):
        out[i] = (1.0 - math.cos(math.pi * i / a_n)) * 0.5
    # Release: 1 → 0 raised cosine
    r_n = n - a_n
    for i in range(r_n):
        # i goes 0..r_n-1 ; we want out[a_n + i] to go from 1 down to 0
        out[a_n + i] = (1.0 + math.cos(math.pi * i / r_n)) * 0.5
    return out


def apply_env(samples: List[float], env: List[float]) -> List[float]:
    return [s * e for s, e in zip(samples, env)]


# ─── Pad voice ──────────────────────────────────────────────────────────────

def pad(freq: float, dur_s: float, attack_frac: float = 0.25,
        sub: float = 0.45, fifth: float = 0.30) -> List[float]:
    """Soft pad voice at ``freq``: fundamental + sub-octave + perfect
    fifth above, all sharing one smooth swell-fade envelope. No
    inharmonic partials, no per-partial decay differences — the three
    sines stay in fixed amplitude relation throughout the note so
    there's no warbling or beat-frequency interference.

    ``sub`` and ``fifth`` are the relative amplitudes of the sub and
    fifth voices vs. the fundamental at 1.0.
    """
    n = int(SR * dur_s)
    fund = _sine_n(freq,        n)
    sub_v = _sine_n(freq * 0.5, n)
    fifth_v = _sine_n(freq * 1.5, n)
    mixed = [a + sub * b + fifth * c
             for a, b, c in zip(fund, sub_v, fifth_v)]
    env = swell_env(n, attack_frac=attack_frac)
    return apply_env(mixed, env)


# ─── Sound recipes ──────────────────────────────────────────────────────────

NOTE = {
    "C3": 130.81, "D3": 146.83, "E3": 164.81, "F3": 174.61, "G3": 196.00,
    "A3": 220.00, "B3": 246.94, "C4": 261.63, "D4": 293.66, "E4": 329.63,
    "F4": 349.23, "G4": 392.00, "A4": 440.00, "C5": 523.25,
}


def boot(outdir: str) -> None:
    """Welcome — a slow C-major triad (C4 + E4 + G4) swelling in
    together, ~1.6 s total. Reads as 'the system is here' without
    announcing itself."""
    dur = 1.6
    layers = [
        pad(NOTE["C4"], dur, attack_frac=0.30, sub=0.50, fifth=0.20),
        pad(NOTE["E4"], dur, attack_frac=0.30, sub=0.35, fifth=0.20),
        pad(NOTE["G4"], dur, attack_frac=0.30, sub=0.30, fifth=0.20),
    ]
    out = mix(layers[0],
              [0.75 * s for s in layers[1]],
              [0.55 * s for s in layers[2]])
    save_wav(f"{outdir}/boot.wav", out, gain=0.20)


def volume(outdir: str) -> None:
    """Volume tick — single soft pad at A3, 220 ms. Short enough that
    a rotary gesture's many ticks don't pile up, soft enough not to
    startle the user."""
    s = pad(NOTE["A3"], 0.22, attack_frac=0.35, sub=0.40, fifth=0.25)
    save_wav(f"{outdir}/volume.wav", s, gain=0.16)


def play(outdir: str) -> None:
    """PLAY — major-third pad chord (G3 + B3) swelling in then fading."""
    g = pad(NOTE["G3"], 0.55, attack_frac=0.20, sub=0.45, fifth=0.25)
    b = pad(NOTE["B3"], 0.55, attack_frac=0.20, sub=0.35, fifth=0.25)
    out = mix(g, [0.80 * s for s in b])
    save_wav(f"{outdir}/play.wav", out, gain=0.18)


def pause(outdir: str) -> None:
    """PAUSE — minor-third pad (G3 + Bb3), mellower than PLAY. Same
    fundamental, lowered third, so it sits in the same tonal space
    but reads as 'resting' rather than 'going'."""
    g = pad(NOTE["G3"], 0.55, attack_frac=0.25, sub=0.45, fifth=0.25)
    bb = pad(233.08, 0.55, attack_frac=0.25, sub=0.40, fifth=0.25)  # Bb3
    out = mix(g, [0.80 * s for s in bb])
    save_wav(f"{outdir}/pause.wav", out, gain=0.18)


def skip_next(outdir: str) -> None:
    """NEXT — single soft pad at G4, 280 ms. No two-tone — felt too
    'beeper'."""
    s = pad(NOTE["G4"], 0.28, attack_frac=0.25, sub=0.35, fifth=0.20)
    save_wav(f"{outdir}/skip_next.wav", s, gain=0.17)


def skip_prev(outdir: str) -> None:
    """PREV — single soft pad at D4, 280 ms."""
    s = pad(NOTE["D4"], 0.28, attack_frac=0.25, sub=0.40, fifth=0.20)
    save_wav(f"{outdir}/skip_prev.wav", s, gain=0.17)


def bt_connected(outdir: str) -> None:
    """BT pairing complete — ascending arpeggio C4 / E4 / G4, each note
    overlapping into the next so it reads as a chord building up."""
    notes_dur = [(NOTE["C4"], 0.70), (NOTE["E4"], 0.70), (NOTE["G4"], 1.00)]
    rendered = [pad(f, d, attack_frac=0.25, sub=0.40, fifth=0.20)
                for f, d in notes_dur]
    offset_s = 0.22
    offset_n = int(SR * offset_s)
    total_n = offset_n * (len(rendered) - 1) + len(rendered[-1])
    out = [0.0] * total_n
    for i, n in enumerate(rendered):
        start = i * offset_n
        amp = 1.0 if i == len(rendered) - 1 else 0.80
        for j, s in enumerate(n):
            out[start + j] += s * amp
    save_wav(f"{outdir}/bt_connected.wav", out, gain=0.20)


def standby(outdir: str) -> None:
    """Goodnight — two notes descending E3 → C3, the lower one held
    longer with extra sub-octave for warmth. Slow, generous fade —
    the speaker is yawning."""
    e3 = pad(NOTE["E3"], 0.70, attack_frac=0.20, sub=0.60, fifth=0.20)
    c3 = pad(NOTE["C3"], 1.20, attack_frac=0.20, sub=0.75, fifth=0.15)
    offset_n = int(SR * 0.40)
    total_n = offset_n + len(c3)
    out = [0.0] * total_n
    for j, s in enumerate(e3):
        if j < total_n:
            out[j] += s
    for j, s in enumerate(c3):
        out[offset_n + j] += 0.90 * s
    save_wav(f"{outdir}/standby.wav", out, gain=0.20)


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
