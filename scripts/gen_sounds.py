#!/usr/bin/env python3
"""
scripts/gen_sounds.py — generate the BeatBird UI sound effect set.

Outputs eight ~120-1500 ms 16-bit mono 44.1 kHz WAV files to
``assets/sounds/``. Stdlib only (wave + math + struct) so the script
runs on any dev machine that has Python; the Pi never needs to do this
— the WAVs are committed to the repo.

Each sound is a tiny tone composition with an ADSR envelope. The
design vocabulary mirrors the Libratone originals — short, mechanical-
synthetic, restrained — rather than the multi-second corporate jingles
common in commercial speakers.

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

def sine(freq: float, dur_s: float) -> List[float]:
    n = int(SR * dur_s)
    two_pi_over_sr = 2.0 * math.pi / SR
    return [math.sin(two_pi_over_sr * freq * i) for i in range(n)]


def warm(freq: float, dur_s: float) -> List[float]:
    """Fundamental + sub-octave + 2nd harmonic — a small additive stack
    that gives the otherwise-thin sine tones some body. Mixed in low so
    the perceived pitch is still the fundamental, but the tone has
    weight a single sine doesn't on real speakers."""
    fund = sine(freq,        dur_s)
    sub  = sine(freq * 0.5,  dur_s)
    harm = sine(freq * 2.0,  dur_s)
    return [0.78 * a + 0.35 * b + 0.10 * c
            for a, b, c in zip(fund, sub, harm)]


def chirp(f0: float, f1: float, dur_s: float) -> List[float]:
    """Linear frequency sweep from f0 to f1 over dur_s. Phase is
    integrated correctly so the waveform is continuous (no clicks)."""
    n = int(SR * dur_s)
    two_pi = 2.0 * math.pi
    out = [0.0] * n
    for i in range(n):
        t = i / SR
        # phase(t) = 2π ∫ f(s) ds from 0 to t,  f(s)=f0+(f1-f0)·s/dur
        phi = two_pi * (f0 * t + (f1 - f0) * t * t / (2.0 * dur_s))
        out[i] = math.sin(phi)
    return out


def adsr(samples: Sequence[float], attack: float = 0.01, decay: float = 0.05,
         sustain: float = 0.7, release: float = 0.1) -> List[float]:
    """Apply an attack/decay/sustain/release envelope in-place semantics.
    Times are in seconds; sustain is the level (0..1) held between
    decay and release."""
    n = len(samples)
    a_n = int(attack * SR)
    d_n = int(decay  * SR)
    r_n = int(release * SR)
    if a_n + d_n + r_n > n:
        # Tone too short for the requested envelope — shrink everything
        # proportionally so we don't index past the end.
        scale = n / max(1, a_n + d_n + r_n)
        a_n = int(a_n * scale)
        d_n = int(d_n * scale)
        r_n = int(r_n * scale)
    out = list(samples)
    # Attack: 0 → 1
    for i in range(a_n):
        out[i] *= i / max(1, a_n)
    # Decay: 1 → sustain
    for i in range(d_n):
        idx = a_n + i
        out[idx] *= 1.0 - (1.0 - sustain) * (i / max(1, d_n))
    # Sustain: hold
    for i in range(a_n + d_n, n - r_n):
        out[i] *= sustain
    # Release: sustain → 0
    for i in range(r_n):
        idx = n - r_n + i
        out[idx] *= sustain * (1.0 - i / max(1, r_n))
    return out


def concat(*parts: Sequence[float]) -> List[float]:
    out: List[float] = []
    for p in parts:
        out.extend(p)
    return out


def save_wav(path: str, samples: Sequence[float], gain: float = 0.3) -> None:
    """Quantise to 16-bit PCM with an extra ``gain`` multiplier and
    write a mono WAV. ``gain`` is the headroom-aware final scale (the
    envelope already limits to [-1, 1]); a gain of 0.3 ≈ -10 dBFS."""
    pcm_iter = (max(-32767, min(32767, int(s * gain * 32767))) for s in samples)
    pcm = struct.pack(f"<{len(samples)}h", *pcm_iter)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)


# ─── Sound recipes ──────────────────────────────────────────────────────────

def warm_chirp(f0: float, f1: float, dur_s: float) -> List[float]:
    """Chirp with the same fundamental + sub + 2nd-harmonic stack as
    warm(), sweeping from f0 to f1 over dur_s."""
    fund = chirp(f0,       f1,       dur_s)
    sub  = chirp(f0 * 0.5, f1 * 0.5, dur_s)
    harm = chirp(f0 * 2.0, f1 * 2.0, dur_s)
    return [0.78 * a + 0.35 * b + 0.10 * c
            for a, b, c in zip(fund, sub, harm)]


def boot(outdir: str) -> None:
    """Welcome jingle — C4 / E4 / G4 / C5 with a bell-ish release on the
    last note. Down an octave from the first cut so the speaker's bass
    response actually gets exercised; pure sines at 500-1000 Hz sounded
    laptop-tinny on good hardware."""
    parts = []
    for freq, dur in [
        (261.63, 0.18),   # C4
        (329.63, 0.18),   # E4
        (392.00, 0.18),   # G4
        (523.25, 0.55),   # C5 — held longer, bell-like
    ]:
        s = warm(freq, dur)
        s = adsr(s, attack=0.005, decay=0.05, sustain=0.7,
                 release=dur * 0.5)
        parts.append(s)
    save_wav(f"{outdir}/boot.wav", concat(*parts), gain=0.32)


def volume(outdir: str) -> None:
    """Volume tick — 80 ms blip around 500 Hz. Warm enough to read on
    good speakers, not so heavy that rapid ticks during a rotary
    gesture become a buzz."""
    s = warm(500, 0.08)
    s = adsr(s, attack=0.002, decay=0.015, sustain=0.5, release=0.05)
    save_wav(f"{outdir}/volume.wav", s, gain=0.22)


def play(outdir: str) -> None:
    """PLAY — rising chirp 220 → 440 Hz."""
    s = warm_chirp(220, 440, 0.22)
    s = adsr(s, attack=0.005, decay=0.04, sustain=0.7, release=0.12)
    save_wav(f"{outdir}/play.wav", s, gain=0.28)


def pause(outdir: str) -> None:
    """PAUSE — falling chirp 440 → 220 Hz, symmetrical with play."""
    s = warm_chirp(440, 220, 0.22)
    s = adsr(s, attack=0.005, decay=0.04, sustain=0.7, release=0.12)
    save_wav(f"{outdir}/pause.wav", s, gain=0.28)


def skip_next(outdir: str) -> None:
    """Two-tone tick rising — quick + crisp, like a tape advance."""
    s1 = adsr(warm(300, 0.06), 0.001, 0.01, 0.5, 0.04)
    s2 = adsr(warm(450, 0.10), 0.001, 0.02, 0.6, 0.06)
    save_wav(f"{outdir}/skip_next.wav", concat(s1, s2), gain=0.28)


def skip_prev(outdir: str) -> None:
    s1 = adsr(warm(450, 0.06), 0.001, 0.01, 0.5, 0.04)
    s2 = adsr(warm(300, 0.10), 0.001, 0.02, 0.6, 0.06)
    save_wav(f"{outdir}/skip_prev.wav", concat(s1, s2), gain=0.28)


def bt_connected(outdir: str) -> None:
    """BT pairing complete — E4 / A4 / E5 ascending, bell decay on top."""
    parts = []
    for freq, dur in [
        (329.63, 0.14),   # E4
        (440.00, 0.14),   # A4
        (659.25, 0.45),   # E5 — sustained tail
    ]:
        s = warm(freq, dur)
        s = adsr(s, attack=0.005, decay=0.04, sustain=0.6,
                 release=dur * 0.5)
        parts.append(s)
    save_wav(f"{outdir}/bt_connected.wav", concat(*parts), gain=0.32)


def standby(outdir: str) -> None:
    """Going to sleep — slow descending chirp 300 → 110 Hz."""
    s = warm_chirp(300, 110, 0.45)
    s = adsr(s, attack=0.01, decay=0.10, sustain=0.5, release=0.25)
    save_wav(f"{outdir}/standby.wav", s, gain=0.24)


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
