# Lounge 3-way build — Pi 5 multi-lane (the BeatBird-specific part)

How the Libratone Lounge active 3-way (2 mid + 2 ribbon + 1 sub, three Sonocotta
TAS58xx amp boards) is wired and configured. **Goal:** all crossover / EQ / level
in CamillaDSP, the TAS chips flat — voicing tunable in software (REW + the web
UI), not baked into per-chip DSP blobs.

> The *why* behind picking multi-lane (and the Pi-can't-do-TDM findings + the
> alternative-host options like Rockchip/ADAU1452) is generic hardware research,
> kept separately in **`research/multichannel-dac-hosts.md`**. This file is just
> the BeatBird Lounge build.

## The constraint with our boards

Full all-CamillaDSP would need **3 chips, 3 distinct I²C addresses, 3 separate
data lines (SDIN)**. Our boards can't quite reach it:

- The **Plus X2** board carries two chips (mid `0x4c` + sub `0x4d`) **hard-wired
  to one SDIN** — no point on the PCB to split them → they must share a lane.
- The only spare single board is a second X1 (TAS5805M) at the **same fixed I²C
  address `0x2d`** → bus collision; not board-selectable.

So the three distinct addresses we have (`0x4c`, `0x4d`, `0x2d`) include the
`0x4c`/`0x4d` pair stuck on one SDIN → **one crossover must live in the chips.**
(A real-TDM host would dissolve this — see the research file — but that's a board
swap; this is the no-new-hardware build.)

## Chosen design — pragmatic, Beat-style (2 lanes)

Pi 5 RP1 multi-lane (separate I²S data lanes, shared BCLK/LRCLK):

```
Lane D0 (SDO0, GPIO21/pin40): X2 — mid 0x4c (stereo) + sub 0x4d (mono PBTL), share the pair
Lane D1 (SDO1, GPIO23/pin16): ribbon 0x2d (stereo)
```
Shared: BCLK GPIO18, LRCLK GPIO19, I²C GPIO2/3, GND. (Boards un-stacked +
hand-jumpered, so GPIO23 is free — the on-board IR only ties to it when stacked.)

- **mid ↔ ribbon (~3 kHz LR4): in CamillaDSP** (LP on D0, HP on D1).
- **sub ↔ mid (~120 Hz): in the X2 chips** — mid in-chip HP, sub in-chip LP +
  mono (`ti,eq-mode` HF/LF crossover). CamillaDSP feeds D0 full-range below 3 kHz;
  the chips split it. Exactly how the Beat runs its internal sub crossover —
  the sub low-pass is the one filter you never hear.
- All EQ / room correction / the audible mid↔ribbon crossover stay in CamillaDSP
  and remain web-tunable; only the sub low-pass is in-chip. The TAS5825M's 30
  biquads (datasheet §9.3.7) make the in-chip fc free to choose.

Files: `install/overlays/tas58xx-lanes-overlay.dts` (DRAFT) ·
`config/camilladsp/lounge.yml`.

## Open question to test on the bench

The DAC8x uses a custom `rpi-simple-soundcard` driver for the lane→codec mapping
with *dumb* PCM5102A. With our **I²C-controlled TAS codecs**, does a generic
`simple-audio-card` 4-ch stream map ch0/1→SDO0, ch2/3→SDO1 with each codec reading
its wired pin — or is a per-lane dai-link / the rpi framework needed? **This is
the thing to confirm:** jumper ribbon SDIN→GPIO23, load the overlay, check the
card enumerates and each lane drives its chip.

## Contribution back to Sonocotta

The hardware already supports multi-lane (jumper each SDIN to its own SDO); the
driver/overlays only cover single + dual (shared TDM bus, Pi Zero). A clean "Pi 5
multi-lane" mode would unlock 1×–4× multichannel with the TAS boards. The board
asks that would make full all-CamillaDSP trivial: a **DIN-lane-select** jumper +
a **selectable I²C address** (so two identical boards coexist). If multi-lane
lands cleanly we can PR a `tas58xx-lanes` overlay covering 1×–4×.
