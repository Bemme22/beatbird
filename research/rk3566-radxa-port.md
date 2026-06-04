# Porting the Lounge 3-DAC build to a Radxa Zero 3W (RK3566, real TDM)

Build notes for when the boards arrive. Goal: the **original** design — 3 TAS amp
chips on **one shared I²S line**, each reading its own TDM slots, **all crossover
in CamillaDSP**, chips flat — which the Pi can't do but the RK3566's `I2S_TDM`
controller can. See `research/multichannel-dac-hosts.md` for the why.

Two boards ordered (~€60 total): one to experiment on, one spare/2nd speaker.

## ⚠️ FIRST, DE-RISK: is 8-ch TDM actually on the 40-pin header?

The RK3566 has several I²S blocks but **only I2S1 is the full 8-channel TDM**
instance; I2S2 / I2S3 are 2-channel. Community I²S examples on the Zero 3 use a
**2-channel** instance (e.g. a single MAX98357A). **The make-or-break question:
does the Zero 3W route an 8-ch-TDM-capable I²S to the 40-pin header, or only a
2-ch one?** If only 2-ch reaches the header, the shared-line TDM plan can't run
there — we'd be back to a 2-channel + in-chip-crossover setup (no better than the
Pi).

**Schematic pre-check (2026-06-04, text-extracted from the V1.12 PDF) —
encouraging:** the RK3566 pinmux exposes the full **I2S1** instance —
`I2S1_SCLK_TX`, `I2S1_LRCK_TX`, `I2S1_MCLK`, and **`I2S1_SDO0/1/2/3`** (four
data-out lanes → 8-ch TDM *and* multi-lane both possible). Crucially, I2S1's
alt-functions sit on GPIOs that also carry classic 40-pin signals (UART3, PWM11,
SPI), so they're plausibly header-routed (not all locked to the PMIC/WiFi/eMMC).
So the SoC side looks right; what's still unconfirmed is the exact **header-pin
→ I2S1 mapping** (which physical 40-pin pins, and whether Radxa's overlay wires
I2S1 vs the 2-ch I2S2/3).

Confirm on the board (BEFORE wiring):
1. Radxa GPIO pinout table / `gpio readall` — which header pins map to
   `I2S1_*` (SCLK_TX/LRCK_TX/SDO0…) and the I²C pins.
2. `rsetup` overlay list — is there an `i2s1`/8-ch/TDM overlay for the header,
   or only a 2-ch `i2s` one? (May need a custom overlay either way.)
3. The mainline `rockchip,rk3566` DT `i2s1_8ch` node as the binding reference.

If I2S1 turns out NOT to reach the header → fall back to multi-lane via
`I2S1_SDO0..3` if those pins ARE exposed (same idea as the Pi 5, but the chips
could still share via TDM if even one SDO + the clocks reach the header). Only a
total absence of header I²S would make the Zero 3W wrong for this.

## If 8-ch TDM is available — the port

### 1. OS + overlay tooling
- Flash Radxa's Debian/Armbian image for the Zero 3W (microSD).
- Enable I²S + I²C via `rsetup` (Radxa's overlay manager) — the default DT does
  not expose I²S for audio. Radxa overlays are compiled/installed through
  rsetup, NOT the Pi's `dtoverlay=` in config.txt.

### 2. Device tree (the real work)
Re-author our overlay for the **Rockchip `i2s_tdm`** node (not `brcm,bcm2835`):
- target the `&i2s1_8ch` controller; set TDM mode, `rockchip,trcm-sync-tx-only`
  / the TDM slot config (8 slots × 32-bit) as the RK I2S_TDM driver expects
  (different bindings from the Pi's `dai-tdm-slot-*`).
- the three `ti,tas58xx` codec nodes (0x4c/0x4d/0x2d) on the board's I²C bus,
  flat (`ti,eq-mode = 0`), each with its TDM slot offset.
- a `simple-audio-card` (or `rockchip,rk817`-style) linking the i2s1 cpu DAI to
  the 3 codecs.
- **Pinmux:** map the RK3566 GPIO pins (I²S SCLK/LRCK/SDO, I²C SDA/SCL) to the
  40-pin header positions. These likely DON'T match the Pi's pin functions —
  expect to move a jumper or two so the TAS HAT's I²S/I²C pins line up.

### 3. Driver
The Sonocotta `tas58xx` codec driver is mostly SoC-agnostic (it's a codec, not a
controller) — rebuild it against the **Armbian/Radxa kernel headers**
(`install/05-tas-driver.sh` logic, but the Rockchip kernel). The TDM patch
(`install/patches/tas58xx-tdm-slots.patch`) should still apply. The +4 BCLK
frame fudge we needed on the Pi was an RP1 artefact — on a real TDM frame the
slot offsets should be the clean N×32 (0 / 64 / 128), re-verify with a scope.

### 4. CamillaDSP config
This path uses the **original 8-channel TDM** `lounge.yml` (stereo→TDM8 mixer,
3-way LR4, mid/woofer/ribbon on slots 0/1 · 2 · 4/5) — NOT the current 2-lane
4-ch version. That config is in git history (pre-commit 67dc285); restore + adapt
it (playback device = the RK card name, 8 channels). Full crossover in CamillaDSP,
no in-chip compromise.

### 5. The rest of the BeatBird stack
- CamillaDSP, the Python bridge + FastAPI web, go-librespot, the USB-serial
  display protocol: all SoC-agnostic, run on Armbian. The **install scripts**
  assume Raspberry Pi OS (apt packages, `/boot/firmware/`, Pi-specific overlays,
  overlayroot) — they need a Rockchip/Armbian path. Budget this: provisioning is
  Pi-shaped today.
- WiFi 6 + BT 5.4 onboard → streaming + A2DP work; check `bluez-alsa` on Armbian
  ([[bt-pi-setup-gotchas]] gotchas may differ).

## Honest effort estimate

The board is €25; the **work is the device-tree + the Armbian provisioning**, and
audio/DT on Rockchip is fiddlier with less hand-holding than Raspberry Pi. This is
a multi-session bring-up, gated on the §0 verification. If §0 fails (no 8-ch on
the header), the Zero 3W is the wrong board and we stop before sinking time in.
