# Multichannel I²S/TDM from an SBC to multiple DACs — host & topology research

> Generic hardware research, **not primarily a BeatBird topic** — kept here as a
> reference for "how do I get N independent audio channels out of a single-board
> computer to several I²S amplifier/DAC chips". BeatBird's Lounge build *applies*
> a conclusion from this (see `docs/lounge-multilane.md`), but the findings below
> are about the hosts/chips in general.

The core problem: a 3-way active stereo speaker (or any >2-channel active setup)
needs several independent audio channels delivered to several DAC/amp chips. The
two physical ways to do that over I²S are **TDM slots** (many channels on one
data line, time-multiplexed) or **multiple data lanes** (one stereo pair per
wire, shared bit/word clock). Which is available depends entirely on the host's
I²S controller.

## Raspberry Pi — both generations fall short of multi-channel TDM

Bench-proven (2026-06-03) and confirmed in the kernel/vendor sources:

| Host | I²S controller | TDM? | Verdict |
|------|----------------|------|---------|
| **Pi 4 (bcm2835)** | bcm2835-i2s | TDM exists but `bcm2835_i2s_set_dai_tdm_slot` is **hard-capped to exactly 2 channels** (`hweight(mask)!=2 → -EINVAL`, mainline kernel) | a 3-codec TDM card fails probe with `set_tdm_slot … -22` |
| **Pi 5 (RP1)** | RP1 I²S | **no TDM at all** — WS is fixed 50:50, no slot positioning | requesting `dsp_a` yields a malformed frame where only slot 0 is coherent (scoped: only one chip ever output) |

So on the Pi the chip-side SAP/TDM register config is irrelevant — **neither Pi
emits a >2-channel TDM frame**. "Pi4 → 8 ch / Pi5 → 32 ch via TDM" does not hold.

Sources: RPi *Using the I²S peripherals on Raspberry Pi SBCs* white paper (RP1 has
no TDM); `raspberrypi/linux` `sound/soc/bcm/bcm2835-i2s.c` (2-channel cap).

### Pi 5 escape hatch: multiple data LANES (not slots)

RP1 I²S0 is a clock producer with **up to 4 independent data lanes** (SDO0–3 on
GPIO 21/23/25/27) sharing one BCLK/LRCLK — one stereo pair per lane, so up to 8
channels. This is how the HiFiBerry DAC8x does 8 ch, and it's the only
multi-channel route on a Pi 5. Each DAC's data-in is wired to its own SDO; the
ALSA channel-pairs map ch0/1→SDO0, ch2/3→SDO1, … positionally. Works, but every
DAC must sit on its own lane — DACs that physically share one data line can't be
addressed independently this way.

## Boards that DO multi-channel TDM properly: Rockchip (RK3566 / RK3568 / RK3588)

The Rockchip **I2S_TDM** controller is a real TDM controller (multi-slot on one
data line), unlike bcm2835/RP1. It's in the WHOLE RK35xx line — including the
**cheap** parts — so you do NOT need the pricey RK3588.

| SoC | I²S/TDM | Example boards | Price |
|-----|---------|----------------|-------|
| **RK3566** (budget pick) | I2S_TDM, multi-slot 8-ch | **Radxa Zero 3W** (Pi-Zero-2-W form factor, 40-pin header!), Orange Pi 3B, Geniatech XPI-3566-Zero | **~€15–35** |
| RK3568 | I2S_TDM tested stable **8-ch up to 384 kHz** | Radxa CM3, Rock 3 | ~€40–60 |
| RK3588 | 4 I²S; I2S0/1 do 8-ch | Radxa Rock 5A/5B, Orange Pi 5(+), Banana Pi M7 | €60–120 (overkill) |

The **Radxa Zero 3W (~€20–30, RK3566)** is the sweet spot: Pi-Zero-2-W form
factor + 40-pin header, so the existing TAS HAT boards mount + roughly line up
(verify the I²S/I²C pin functions against the RK3566 pinmux — may need a jumper
or two), and the whole stack (CamillaDSP + Python control + USB-serial display)
runs on its Armbian/Debian.

→ On a Rockchip host the *original* "3 DAC chips on one shared I²S line, each
reading its own TDM slots" design works directly — including a dual-chip board
(both chips just read different slots on the shared line, which is exactly what
TDM is for), as long as the chips have distinct I²C addresses. **No rewiring, no
in-chip-crossover compromise, full CamillaDSP — with the existing TAS boards.**

**Cost = effort, not money.** The cheap board is ~€25; the real work is porting
the Pi-specific codec driver + device-tree overlay to the Rockchip I2S_TDM node
(new DT node, pinctrl, GPIO refs, rebuild against the Armbian kernel). The codec
driver core is usually SoC-agnostic; the DT/overlay + provisioning is the lift,
and audio/DT on non-Pi SBCs is fiddlier with less community support — budget it
realistically (not an afternoon).

## "HAT in between" with a TDM-capable processor (ADAU1452 / SigmaDSP)

The **ADAU1452** SigmaDSP supports I²S **and** TDM I/O and can convert between
them (e.g. take I²S in, emit TDM8; or TDM in, 4× I²S out). Boards like **PiDSP**
put it on a Pi-compatible HAT with 4× I²S/TDM in+out.

Two ways to use it, both with downsides:
1. **Pure I²S→TDM serializer.** The host still has to *produce* the channels —
   on a Pi that means the Pi-5 multi-lane output feeding the ADAU1452, which then
   re-serializes the lanes to a single TDM stream for the amps. Adds a board +
   SigmaStudio config; you don't gain much over just wiring the lanes to the amps
   directly.
2. **Do the crossover in the ADAU1452 (SigmaStudio).** Then the host DSP
   (CamillaDSP) is bypassed for crossover — voicing moves into SigmaStudio, a
   second toolchain. Defeats an "all in CamillaDSP" goal.

→ Technically possible, but adds cost + complexity + (often) a second DSP
toolchain. Rarely the clean answer.

Sources: [diyAudio — 8-channel DSP + CamillaPi interface options](https://www.diyaudio.com/community/threads/discussing-about-8channel-dsp-using-camilla-dsp-raspberry-pi-interface-options.395392/page-5)
· [Radxa — 8-channel I²S on RK35xx](https://forum.radxa.com/t/8-channel-audio-input-on-rock-pi-s/4034)
· [Banana Pi BPI-M7 (RK3588)](https://www.cnx-software.com/2024/01/30/banana-pi-bpi-m7-thin-rockchip-rk3588-sbc-dual-2-5gbe-m-2-nvme-storage-hdmi-2-1/)
· [PiDSP / ADAU1452 (Hackaday)](https://hackaday.io/project/21119-pidsp)
· [ADAU1452 datasheet](https://www.analog.com/media/en/technical-documentation/data-sheets/adau1452.pdf)

## Comparison

| Option | New HW | Effort | Crossover home | Multi-chip-on-one-line OK? |
|---|---|---|---|---|
| Pi 5 multi-lane | none | low (overlay) | CamillaDSP (mostly) | ✗ each chip needs its own lane |
| **Rockchip RK3566 + real TDM** | **~€25 board** | medium (driver/DT port) | CamillaDSP (fully) | ✅ yes (shared line, TDM slots) |
| ADAU1452 TDM HAT | DSP board | medium–high | SigmaStudio (or split) | ✅ (but DSP moves off CamillaDSP) |

**Bottom line:** for full software DSP with existing TAS-style amp boards and
chips that share data lines, a **cheap Rockchip RK3566 host (e.g. Radxa Zero 3W,
~€25) with real TDM** is the cleanest path — the original shared-line design just
works, the cost is porting effort not money. A Pi 5 with multi-lane is the
no-new-hardware route but constrains you to one DAC chip per lane (forcing one
in-chip crossover with our boards). An ADAU1452 HAT is a niche middle option that
usually isn't worth it.
