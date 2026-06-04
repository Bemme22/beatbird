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

**Kernel/DT verification (2026-06-04, no board needed) — TDM is real, the board
just doesn't pre-wire it:**
- ✅ **TDM confirmed at the SoC + driver level.** Mainline `rk356x-base.dtsi`:
  `i2s1_8ch` is `compatible = "rockchip,rk3568-i2s-tdm"` — a genuine TDM
  controller, with `i2s1m0_sdo0/1/2/3` (4 data-out lanes) + `sdi0..3` and
  `sclktx/lrcktx` in the M0 pinmux group. So 8-ch TDM **and** multi-lane are both
  supported by the RK3566 + the mainline `rockchip-i2s-tdm` driver. The hard
  capability question is YES.
- ⚠️ **The Radxa Zero 3 board DT leaves it OFF by default.** `rk3566-radxa-zero-3.dtsi`:
  only `&i2s0_8ch` is enabled (it's the HDMI audio path); **`&i2s1_8ch` is not
  referenced**, and only `&i2c0` (the PMIC) is on — the header I²C buses are off
  too. Both get enabled via **overlays** (the Radxa rsetup mechanism), not a
  Pi-style `config.txt` line.
- ⏳ **No ready-made "i2s1 on the header" overlay found** in `radxa-pkg/radxa-overlays`
  (CM3/CM4 audio overlays exist, but not an obvious Zero-3 i2s1-TDM one). So we
  most likely **write a custom overlay**: enable `&i2s1_8ch` + its `i2s1m0_*`
  pinctrl + a header I²C bus + the TDM/codec nodes. Doable — the board DT already
  names the 40-pin header GPIOs, and the `i2s1m0` pin group is defined in the
  pinctrl dtsi, so the header-pin mapping is derivable.

### ⛔ SHOWSTOPPER (2026-06-04, kernel/DT, no board): i2s1 is NOT on the Zero 3W header

Cross-referenced the i2s1 pin groups (`rk3568-pinctrl.dtsi`) against the Zero 3W's
header GPIO line-names (`rk3566-radxa-zero-3.dtsi`). The 8-ch TDM controller i2s1
needs its clocks **SCLK_TX + LRCK_TX** on the header to drive external codecs — and
for **every** mux variant they aren't:

| i2s1 mux | SCLK_TX | LRCK_TX | on the 40-pin header? |
|---|---|---|---|
| M0 | GPIO1_A3 | GPIO1_A5 | ✗ (GPIO1 exposes only A0=pin3, A1=pin5, A4=pin37) |
| M1 | GPIO3_C7 | GPIO3_D0 | ✗ (header GPIO3 stops at C4) |
| M2 | GPIO2_D1 | GPIO2_D2 | ✗ (header has **no** GPIO2 pins at all) |

A couple of i2s1 *data* lanes graze the header (M2 SDO2=GPIO3_C1=pin22,
SDO3=GPIO3_C2=pin32) but **without the bit/word clock on the header there is no
external I²S via i2s1.** The header (mostly GPIO3+GPIO4) only carries the 2-channel
i2s instances — i.e. no better than the Pi.

**Conclusion: the Radxa Zero 3W is the WRONG board for the shared-line TDM design.
The RK3566 *SoC* does 8-ch TDM, but this *board* doesn't break i2s1 out.** The
kernel/DT check caught it before wiring — exactly why we checked.

**Options now:**
1. **Different board that exposes i2s1** — verify the header GPIO map the same way
   BEFORE buying. Candidates to check: Orange Pi 3B (RK3566, larger header), a
   Radxa CM3 + carrier (the "8-ch tested" result was on the CM3, which breaks out
   far more I/O than a Zero). The check is: do the `i2s1*_sclktx/lrcktx` GPIOs
   land on that board's header?
2. **Keep the Pi-5 multi-lane compromise** (free, works, one in-chip crossover).
3. The two Zero 3W aren't wasted — they're capable general SBCs (Pi replacement
   for a 2-channel speaker, or other projects).

The rest of the porting plan below still applies to *whatever* RK35xx board does
expose i2s1.

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
