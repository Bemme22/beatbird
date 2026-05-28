# BeatPiMini — Enclosure design parameters

Reference sheet for the offline WinISD / VituixCAD modelling step before
any wood is cut. The CDSP config (`config/camilladsp/beatpimini.yml`)
assumes the box is built; this doc collects everything needed to design
that box.

Source datasheets (PDF, public):

- **SB13PFCR25-4** mid-bass — [sbacoustics.com/.../SB13PFCR25-4](https://sbacoustics.com/wp-content/uploads/2019/06/SB13PFCR25-4.pdf)
- **SB13PFCR-00** passive radiator — [sbacoustics.com/.../SB13PFCR-00](https://sbacoustics.com/wp-content/uploads/2019/06/SB13PFCR-00.pdf)

## Thiele-Small parameters to copy into WinISD

From the SB13PFCR25-4 datasheet, "Electrical specifications" + "Mechanical
specifications" tables. Use the printed values verbatim; manufacturing
spread is usually ±10 % which WinISD lets you sweep.

| Param | Symbol | Where on datasheet | Typical for this driver |
|---|---|---|---|
| DC resistance | Re | Electrical | ~3.4 Ω |
| Voice coil inductance | Le | Electrical (at 1 kHz) | ~0.4 mH |
| Free-air resonance | Fs | T/S table | ~52 Hz |
| Total Q | Qts | T/S table | ~0.37 |
| Mechanical Q | Qms | T/S table | — |
| Electrical Q | Qes | T/S table | — |
| Equivalent air volume | Vas | T/S table | ~9.5 L |
| Moving mass | Mms | Mechanical | — |
| Suspension compliance | Cms | Mechanical | — |
| Effective cone area | Sd | Mechanical | ~88 cm² |
| Max linear excursion | Xmax | Mechanical | ±5 mm |
| Sensitivity (1 W / 1 m) | SPL | top of datasheet | ~85.5 dB |

*Numbers in the "Typical" column are from memory and may be off. Grab the
real values from the PDF — don't trust this table for actual modelling.*

## Passive radiator (SB13PFCR-00)

The PR has no motor, no Re/Le/Qes. What matters for WinISD:

| Param | Symbol | Typical |
|---|---|---|
| Free-air resonance | Fs (PR) | ~25 Hz nominal, **tuneable with added mass** |
| Moving mass | Mms | needs to be entered, can add lead foil to lower Fs |
| Equivalent air volume | Vas | — |
| Suspension compliance | Cms | — |
| Effective cone area | Sd | matches the woofer (~88 cm²) — same chassis |

The PR Fs (and thus the box tuning frequency) is **adjustable in software**
by adding mass on the PR — WinISD will tell you how much mass for a given
target tuning.

## WinISD workflow

1. **New project**, driver = SB13PFCR25-4 (typed in manually if WinISD's
   library doesn't have it)
2. **Add the PR** as a passive radiator. Enter the PR's Mms + Cms.
3. **Box volume** — start at the SB recommendation (typically 6-10 L for
   a 5" + PR), iterate.
4. **PR tuning frequency** — aim for **F_PR ≈ Fs_driver / 1.4** for a
   relatively flat response. For Fs=52 Hz that's PR tuned to ~37 Hz.
   Add PR mass until WinISD shows this.
5. **Check the SPL plot**:
   - F3 should land around **45-50 Hz** for a 5" + PR in this size class
   - No big hump or notch in the passband
   - Group delay below 20 ms at the F3 corner
6. **Check the excursion plot**:
   - Cone Xmax never exceeds rated (±5 mm) at expected SPL (90 dB at 1 m
     for normal listening, 95 dB peak)
   - PR excursion within its mechanical limits (typically ±10 mm)
7. **Save** the box volume + PR mass values. That's what the CAD model
   needs to match.

## Coupling cap for the ribbon (separate from WinISD)

Per the ribbon-protection chain in the CDSP config + STATUS.md notes:

- Series cap: **3.3-4.7 µF film** (polypropylene preferred)
- Voltage rating: 100 V or higher (overkill for a 4 Ω speaker amp at
  ~30 V supply, but film caps in this value range are cheap and small)
- Position: in line with the ribbon's positive lead, between the amp
  output and the ribbon's "+" terminal

Calculation: 3.3 µF in series with 4.3 Ω forms a 1st-order HP at
~11 kHz electrically. That's *above* the crossover frequency, so it's
acting only as a DC blocker, not as part of the crossover. The actual
crossover work happens in CDSP (LR4 @ 3 kHz, 24 dB/oct). If we picked
22 µF instead, the cap-induced HP would be at 1.7 kHz — still well
clear of the 3 kHz crossover. Either value is fine; 3.3 µF is the
"smaller, cheaper, equally protective" choice.

## What's *not* in this doc

- Ribbon T/S — we don't have a datasheet for the salvaged LT300 ribbon,
  and our impedance sweep with the UCA222 didn't yield usable numbers
  (channel-sync issues with PipeWire). The CDSP crossover at 3 kHz is
  conservative enough that we don't need precise ribbon Fs; in-situ
  measurement at the amp output after the build is the better data.
- Detailed enclosure CAD — the trapezoidal "Libratone Beat-inspired"
  outline is up to the build, not the repo.
