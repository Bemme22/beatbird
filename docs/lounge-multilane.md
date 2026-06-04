# Lounge 3-way: from TDM dead-end to Pi 5 multi-lane

Design record for the Libratone Lounge active 3-way (2 mid + 2 ribbon + 1 sub)
driven by three Sonocotta TAS58xx amp boards. Also the basis for a possible
contribution back to the `sonocotta/tas5805m-driver-for-raspbian` project.

## Goal

All crossover / EQ / level / delay in CamillaDSP, the TAS chips flat — so the
voicing is tunable in software (REW + the BeatBird web UI) instead of baked into
per-chip DSP blobs.

## What does NOT work (proven on the bench, 2026-06-03)

Running 3 codecs as separate TDM slots on **one** shared I2S data line — the
obvious "6 channels out of one frame" idea — is impossible on Raspberry Pi:

| Host | I2S | Result |
|------|-----|--------|
| **Pi 5 (RP1)** | no TDM at all — WS is fixed 50:50, no slot positioning | `dsp_a` yields a malformed frame; only slot 0 is coherent. Scoped: only the Mid (slot 0) ever output; all chips + PBTL stage proven healthy. |
| **Pi 4 (bcm2835)** | TDM exists but `set_tdm_slot` is **hard-capped to exactly 2 channels** (`hweight(mask)!=2 → -EINVAL`, mainline kernel) | 3-codec card fails probe: `set_tdm_slot … -22`. |

So the chip-level SAP/TDM register config (`SAP_CTRL1` → TDM, `SAP_CTRL2` → slot
offset) is correct, but **the Pi I2S controller never emits a >2-channel TDM
frame** either way. The "Pi4 → 8 ch / Pi5 → 32 ch via TDM" hope does not hold.

Sources: RPi *Using the I2S peripherals* white paper (RP1 has no TDM);
`raspberrypi/linux` `sound/soc/bcm/bcm2835-i2s.c` (2-channel cap).

## What DOES work: Pi 5 multi-lane (lanes, not slots)

The RP1 I2S0 is a clock producer with **up to 4 independent data lanes** sharing
one BCLK/LRCLK — one stereo pair per lane. (This is how the HiFiBerry DAC8x does
8 ch.) SDO lane → GPIO (docs-confirmed):

| Lane | GPIO | Header pin |
|------|------|-----------|
| SDO0 / D0 | 21 | 40 |
| SDO1 / D1 | 23 | 16 |
| SDO2 / D2 | 25 | 22 |
| SDO3 / D3 | 27 | 13 |

Each amp board's SDIN gets jumpered to a different SDO; BCLK (18) / LRCLK (19) /
I2C (2,3) / GND shared. (When a board is *stacked*, its on-board IR receiver
ties to GPIO23 — un-stacked + un-wired, D1 is free.)

## The constraint that forced a compromise

Full all-CamillaDSP would need **3 chips, 3 distinct I2C addresses, 3 separate
SDINs**. Our boards can't reach that:

- The **Plus X2** board carries two chips (mid `0x4c` + sub `0x4d`) **hard-wired
  to one SDIN** — no point on the PCB to split them. So they must share a lane.
- The only spare single board is a second X1 (TAS5805M) at the **same fixed I2C
  address `0x2d`** as the first → bus collision; the address isn't board-
  selectable. So we can't add a 3rd independent-address single chip.

Net: the three distinct addresses we have (`0x4c`, `0x4d`, `0x2d`) include the
`0x4c`/`0x4d` pair stuck on one SDIN. → **one crossover must live in the chips.**

## Chosen design (the pragmatic, Beat-style compromise)

Two lanes:

```
Lane D0 (GPIO21): X2  — mid 0x4c (stereo) + sub 0x4d (mono PBTL), share the pair
Lane D1 (GPIO23): ribbon 0x2d (stereo)
```

- **mid ↔ ribbon (~3 kHz LR4): in CamillaDSP** (LP on D0, HP on D1).
- **sub ↔ mid (~120 Hz): in the X2 chips** — mid does an in-chip ~120 Hz
  high-pass (`ti,eq-mode` HF crossover), sub an in-chip ~120 Hz low-pass + mono
  (`ti,eq-mode` LF crossover). CamillaDSP feeds D0 full-range below 3 kHz; the
  chips split it. Exactly how the Beat runs its internal sub crossover —
  audibly transparent (the sub low-pass is the one filter you never hear).
- All EQ / room correction / the audible mid↔ribbon crossover stay in
  CamillaDSP and remain web-tunable. Only the sub low-pass is in-chip.

The TAS5825M has 2×15 = 30 programmable biquads (datasheet §9.3.7), so the
in-chip crossover frequency is free to choose via a coefficient blob / the
driver's `eq-mode` crossover profile.

Files: `install/overlays/tas58xx-lanes-overlay.dts` (DRAFT) ·
`config/camilladsp/lounge.yml`.

## Open questions / a possible PR to Sonocotta

The hardware already supports multi-lane (jumper each SDIN to its own SDO). The
**software** side is the gap — the driver/overlays only cover single + dual
(shared TDM bus, Pi Zero). A clean "Pi 5 multi-lane" mode would unlock 1×–4×
multichannel with the TAS boards. Open items:

1. **Multi-lane + I2C codecs.** The DAC8x uses a custom `rpi-simple-soundcard`
   driver for the lane→codec mapping with *dumb* PCM5102A. With I2C-controlled
   TAS codecs, does a generic `simple-audio-card` 4-ch stream map ch0/1→SDO0,
   ch2/3→SDO1 with each codec reading its wired pin — or is a per-lane dai-link
   / the rpi framework needed? **This is the key thing to test.**
2. **In-chip crossover frequency** via `eq-mode` 2/3 — fixed profile vs a custom
   biquad blob for an arbitrary fc.
3. **Board ask:** a **DIN-lane-select** jumper (so a board picks its SDO without
   re-wiring) and a **selectable I2C address** (so two identical boards can
   coexist) would make full all-CamillaDSP multichannel a solder-bridge away —
   no two chips forced onto one lane, no address clash.

If multi-lane lands cleanly we can PR a `tas58xx-lanes` overlay covering 1×–4×.
