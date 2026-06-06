# BeatBird — Project Status

> Last updated: 2026-06-04

## Recent — branch `prep/big-rocks` (2026-06-04, unmerged)

A batch of mergeable + prep work, all CI-green (104 tests, ruff clean), **gated
items flagged**. On `main`: the WiFi/mDNS self-heal (`996ef5d`).

- **Identity split phases 2–4** (`73c4b21`, `97f8d16`) — `Identity.model` +
  derived `resolved_speaker_id / hostname / friendly_name` (CPU-serial instance
  id); browser-rename via a `friendly_name` settings-override (BlueZ alias + HA
  device re-publish + web). Back-compat (every profile pins all three). Phase 5
  (collapse beat-1/2) gated on the live HA broker.
- **Bluetooth dbus-fast step 1** (`a285c70`) — `BluetoothBus` plumbing, dormant.
- **Loudness Hybrid step 1** (`0a52a57`) — native `Loudness` A/B config variants;
  no production config/controller touched. Gated on an audible A/B.
- **Bridge timing** (`664dc31`) — main-loop poll cadences → `profile.timing`.
- **WiFi/mDNS self-heal** (`main 996ef5d`) — NM `powersave=2`, watchdog detects
  IPv4-loss + forces DHCP, avahi IPv4-only. Apply on tmpfs speakers via
  overlayroot-chroot; static IP (`secrets/static-ip.conf`) is the hard fix.
- **Reachability — live finding (2026-06-04):** the recurring "`.local`
  unreachable" is **not** lease-loss — the speakers had valid leases. mDNS only
  returned the IPv6 link-local (`fe80::`) here; **router DNS (`<host>.fritz.box`
  / plain hostname) and the IP work fine.** avahi IPv4-only was applied to the
  Zipp (cleanup, not a silver bullet — IPv4 mDNS doesn't traverse this LAN). The
  Zipp is on **netplan**, not the `beatbird` NM keyfile, so `20-wifi.sh`'s
  power-save path doesn't apply there. Pairing QR now uses the IP (`main`
  `133648a`). Pattern + rationale: [docs/reachability.md](docs/reachability.md).
  Next: pin both Pis via a router DHCP reservation. Specifics in `secrets/`.
- **`capture_samplerate` resolved live** (`main 2d33ba2`) — no pitch bug;
  loopback runs 44100 both sides; asound dmix `rate 48000`→`44100` (honesty).
- Decisions recorded in `docs/{native-loudness,identity-split,bluetooth-dbus-fast}.md`.

## Active speakers

| Speaker | Hardware | OS | Firmware | Status |
|---|---|---|---|---|
| **Beat #1** | Pi Zero 2W · Louder Hat Plus 2X | Bookworm | fw-v0.9.14 | ✅ Production — overlayroot=tmpfs, BT + dashboard live |
| **Zipp Mini 2** | Pi Zero 2W · Louder Hat Plus 1X | Trixie | fw-v0.9.14 | ✅ Production |
| **LoungePi** | Pi 5 (1 GB) · 3× Louder Hat | Trixie 13.4 | — | 🔧 Bench — TDM design dead (Pi I²S can't); redirected to Pi 5 multi-lane (see roadmap + docs/lounge-multilane.md) |
| BeatPiMini | Pi Zero 2W · Louder Hat Plus 1X | — | — | 📐 Designed, not built (see roadmap) |

Both production speakers run `overlayroot="tmpfs:recurse=0"` (persistent
edits need `sudo overlayroot-chroot`). Firmware is OTA-updated via tagged
`fw-v*` GitHub releases (`make firmware-update`).

## Architecture (stable)

- **CamillaDSP volume = single source of truth.** All sources → ALSA
  Loopback (`beatbird_mix` dmix) → CamillaDSP captures `hw:Loopback,1`.
- **Profile YAML = one file per speaker** (`profiles/<name>.yml`),
  Pydantic-validated, drives soundcard / DSP config / systemd / MQTT.
- **Source handoff = last-writer-wins with mutual kill**; BT only takes
  over when actively streaming (not on a paused Spotify).
- **Bridge ↔ display protocol**: pipe-separated serial lines (`ST:`,
  `SYS:`, `PAL:`, `WX:`, `STBY:`, `TOAST:`, `QR:`, `IMG:`).
- **CI**: `python.yml` (ruff + pytest, 52 tests) on src/tests/profiles
  changes; `firmware.yml` (4 ESP32 envs, tag → release).

---

## Shipped (condensed — full detail in git history)

**Core reliability** — standby state machine + Spotify-Connect slot
release; AMOLED heartbeat watchdog (USB-CDC zombie recovery); librespot
health watchdog + stuck-state auto-restart; explicit PLAYPAUSE (no
server-side toggle race, ~98 % reliable); persistent last-volume in
`/var/lib/beatbird/state.json`.

**Audio** — Sonos-style volume taper (`curve_gamma`); smoothstep loudness
curve; loudness feedback-loop fix (never read patched base_gain from live
CDSP); 100 ms ALSA buffer; energy ring driven by `capture_peak` +
asymmetric attack/release envelope; full SFX suite (boot/volume/play/
skip/BT/standby) via dmix so music + SFX coexist.

**Display** — per-speaker firmware rotation (`DISPLAY_ROTATE_NATIVE` vs
`DEG=90`); `set_gap` rotation fix (the "stripes" bug); CenterStage status
chain (PI OFFLINE > MUTE > PAUSE > WIFI WEAK + toasts); weather standby
screen (Open-Meteo); split-flap idle text + scintillation; 6-color palette
protocol (`PAL:a=…|g=…|…`); energy-into-vol-ring; touch direction
per-build fixes.

**Bluetooth (full bring-up)** — pairing via web UI + on-display swipe-down;
`PAIRED — <device>` toast; bond-state persistence across overlayroot=tmpfs
(sync-via-chroot); auto-trust on connect (fixes silent reconnect reject);
rfkill unblock at boot + power-on before discoverable; auto-exit pairing
mode; QR-code pairing flow. Validated on Android + iPhone (manual connect
by design — no auto-play).

**Web UI** — rewritten on **Pico.css + htmx + Jinja2**: minimal `/`
dashboard (Now Playing + Volume + BT pairing card) + `/advanced` (system /
snapcast / loudness sliders / logs / service control). Legacy `/health`,
`/settings`, `/bluetooth` pages still on inline HTML (migration pending).

**Settings carousel** — swipe-down panel is a tileview: QR page + PAIR
BLUETOOTH page, dot indicator, swipe-up close via `LV_EVENT_GESTURE`.

**Quality** — review batch: serial-field escape (pipe/newline in titles),
`S32_LE` schema enforcement + canonicalisation, Innomaker driver removed,
`tests/` (52 tests: volume curve, loudness, config, serial escape),
GitHub Actions CI, `.gitattributes` LF-pinning.

**Beat #1 migration** (2026-05-19) — off legacy `beatbird-display`,
overlayroot re-enabled, snapclient via `beatbird_mix`, BT on.

---

## Roadmap — active

### 🔌 Playback click/pop on Beat #1 + Zipp Mini 2 — DSP headroom (NEXT: 2026-06-02)

**Symptom:** both Beat #1 (Pi Zero 2W, Louder Hat Plus 2X, dual TAS5825M
0x4C/0x4D) and Zipp Mini 2 occasionally click/pop on playback — suspected on
loud / bass-heavy passages. **Likely cause: DSP output clipping** (the loudness
bass filters add positive gain → 0 dBFS overflow). Plan below; diagnose first.

**SSH:** key `~/.ssh/beatbird`, user `devusr`. Address speakers by **router DNS
/ IP, not `.local`** — see [docs/reachability.md](docs/reachability.md). Live
per-unit hostnames/IPs are kept in gitignored `secrets/` (this repo is public).

**Live diagnose 2026-06-04 (read-only, both speakers idle):** `get_throttled =
0x0` on both → **undervoltage ruled out**. `GetProcessingLoad` 1.2 % (Zipp) /
2.1 % (Beat) → **CPU ruled out**. `GetClippedSamples = 0` on both — but that's an
idle baseline (Zipp had just rebooted); the conclusive test is a before/after
diff across a **loud, bass-heavy passage**, NOT yet run (don't blast the speakers
unattended). One isolated `Prepare playback after buffer underrun` on the Zipp at
a stream transition — not chronic. Config-gain math (below) still says the static
bass stack is the prime suspect at high volume. **Remaining:** the loud-bass
clip-diff, then the headroom Gain filter if it confirms.

**Phase 1 — DIAGNOSE ONLY, change nothing:**
1. Read `config/camilladsp/beat.yml` + `zipp-mini-2.yml`; sum the positive filter
   gains in 40–200 Hz to estimate headroom loss.
2. On each speaker:
   - CamillaDSP WS API (port 1234) `GetClippedSamples` — once idle, once during
     loud playback.
   - `journalctl -u camilladsp` + bridge log grep for `clip|xrun|buffer|underrun`.
   - `vcgencmd get_throttled` (undervoltage).
   - Beat only: is `ti,fault-monitor` active (config.txt + init script)? In dual-
     DAC it disturbs the shared I²S bus.
3. Conclude most-likely cause (clipping / xrun / fault-monitor / undervoltage)
   with the concrete numbers. No fixes yet.

**Phase 2 — FIX the clipping in CamillaDSP without changing tonality** (branch
`fix/dsp-headroom`):
- `beat.yml`: a static `headroom` Gain filter (`type: Gain, gain: -12`) as the
  FIRST filter in BOTH channel pipelines, before `bass_shelf`.
- `zipp-mini-2.yml`: same `headroom` (`gain: -6`) first in the broadband + tweeter
  pipelines.
- Optional safety net: a `brickwall` Limiter (`type: Limiter, soft_clip: true,
  clip_limit: -0.5`) at the END of each pipeline.

**Constraints (do NOT violate):**
- `headroom` + limiter must NOT be in the bridge's loudness filter list (else they
  get runtime-patched). Verify in `src/beatbird/audio/loudness.py` / profile YAML
  that only the tonality filters are listed (bass_shelf, sub_punch, timpani_body,
  fullness). [[feedback-loudness-feedback-loop]]
- `capture_samplerate: 44100` + `resampler: Synchronous` unchanged.
- `sub_protect` freqs unchanged.
- Do NOT reduce the tonality filters' `base_gain` (the bridge reads those from the
  live config) — headroom comes ONLY via the separate Gain filter.
- Show the diff. Compensate the level loss via the TAS5825M analog gain (Beat is at
  analog gain 25) — PROPOSE the value, do NOT change hardware gains automatically.

**If Beat's `ti,fault-monitor` is active: remove it.**
- Adjust overlay/address params ONLY in config.txt — NEVER edit the DTS.
- In the init script address controls by NAME, not numid (numids shifted with the
  snd-soc-tas58xx driver). [[feedback-verify-external-lib-features]]
- Show the diff + the reload steps (reboot or module reload).

**Zipp playback device:** ✅ ALREADY MIGRATED — verified 2026-06-01 that
`zipp-mini-2.yml` already targets `hw:LouderRaspberry,0` / `S32_LE` (the old
InnoMaker `plughw:sndrpihifiberry,0` / `S16_LE` premise is stale; the Louder Hat
Plus 1X conversion is done). `capture_samplerate 44100` + Synchronous already in
place. So this part = confirm only, no change.

### 🎚️ Native CamillaDSP `Loudness` filter — retire the bridge-side patch loop (BUILT, AWAITING A/B)

**Idea:** CamillaDSP 4.x ships a native `Loudness` filter (volume-coupled
equal-loudness compensation: `reference_level` + `low_boost` / `high_boost`).
It reads CDSP's own volume and scales bass/treble in real time — i.e. exactly
what BeatBird hand-rolls today in `src/beatbird/audio/loudness.py` + the bridge's
`PatchConfig` websocket loop that rewrites `bass_shelf` / `air_lift` gains on
every volume change.

**Why it's worth testing:**
- Kills the drift-bug class — the native filter is stateless w.r.t. the config,
  so the compounding base_gain creep is structurally impossible. [[feedback-loudness-feedback-loop]]
- Real-time in the DSP engine; no websocket round-trip per volume step.
- Deletes a whole fragile subsystem (`loudness.py` + the patch/suspend dance).

**The trade-off (be honest):** loses the fine-grained curve control — BeatBird's
curve has tunable `knee_low`/`knee_high` + per-filter `max_boost`; the native
filter is a fixed ISO-226 shape with just reference/low/high. Have to A/B whether
the canned curve sounds as good as the tuned one.

**Already built (prep/big-rocks):** `config/camilladsp/beat-loud.yml` IS this
variant — same static EQ as production `beat.yml`, but with CamillaDSP's native
`Loudness` filter at the front of both channels instead of the patch-controller.
Design notes in `docs/native-loudness.md`. Selectable live via the web DSP
switcher (`/advanced` → "Loud"); while active the bridge auto-suspends the
per-volume PatchConfig loudness (same mechanism as `beat-meas.yml`), so the only
volume-dependent lift is the native filter. Deployed + confirmed selectable on
the Beat (`/api/dsp-configs` lists `beat-loud`) 2026-06-05.

**Open = the A/B by ear** (Beat): switch "Produktion (Voicing)" ↔ "Loud" at a
few volumes, listen for whether the native ISO-226 curve holds the bass as well
as the tuned patch curve across the range. **If it wins → delete `loudness.py`
+ the bridge patch/suspend loop** and promote the native filter into production
`beat.yml` (+ `zipp-mini-2.yml`).

### 🔧 LoungePi — 3-DAC, all-active CamillaDSP (TDM DESIGN DEAD → Pi 5 multi-lane)

> **⛔ UPDATE 2026-06-04 — the shared-I²S-TDM design below is a DEAD END.** Bench-
> proven on both Pis: Pi 5 RP1 has no TDM at all (only slot 0 ever output, all
> chips healthy — scoped); Pi 4 bcm2835 TDM is hard-capped to 2 channels (kernel
> source, card fails probe `set_tdm_slot -22`). The hardware was never at fault.
> **New path = Pi 5 MULTI-LANE** (separate I²S data lanes, one stereo pair each).
> Constrained to a **2-lane compromise** (Plus X2's two chips share one SDIN +
> the spare X1 collides on I²C address): mid+sub on lane D0 with the sub crossover
> in-chip (Beat-style), ribbon on lane D1, mid↔ribbon crossover + all EQ in
> CamillaDSP. **Full design + Andriy/PR notes: [docs/lounge-multilane.md](docs/lounge-multilane.md).**
> New artefacts: `install/overlays/tas58xx-lanes-overlay.dts` (DRAFT — key open
> question: multi-lane→I²C-codec mapping on RP1), rewritten `config/camilladsp/lounge.yml`
> (4-ch, 2 lanes). Bench next: jumper ribbon SDIN→GPIO23, load overlay, test.
> The TDM write-up below is kept for the record.

Fully-active 3-way (8" woofer mono + 2× 4" mid L/R + 2× ribbon L/R) on a
Pi 5 with 3 Louder Hat boards on **one shared I²S line via 6-channel TDM**,
all crossover/EQ in CamillaDSP, TAS chips flat.

**Why this design:** Architecture 1 (2-ch shared, crossover in the TAS
internal EQ) was already tried on a Pi 4 and dead-ended at voicing — that's
why the Pi 5 + all-CDSP plan exists. Separate data lines (resolder) would
fragment into 3 ALSA cards CamillaDSP can't drive as one → TDM (one 8-ch
card) is the only clean topology.

**Verified on the bench (2026-05-29):** Pi 5, Trixie 13.4. Three chips on
i2c-1: `0x4c` (TAS5825M, mid), `0x4d` (TAS5825M, woofer PBTL), **`0x2d`**
(TAS5805M, ribbon — NOT the 0x2e the old docs claimed). SSH via
`~/.ssh/id_ed25519` (not the `beatbird` key).

**Driver finding:** stock Sonocotta driver is `channels_max = 2` with zero
SAP/TDM register handling. Andriy (Sonocotta) gave the two registers:
**SAP_CTRL1 (0x33)** = I²S→TDM, **SAP_CTRL2 (0x34)** = RX slot offset in
BCLKs (`0`/`64`/`128` = slot pairs 0-1 / 2-3 / 4-5). Datasheet Table 9-1
catch: 6-slot×32-bit = 192 fS isn't a valid TDM SCLK ratio → **8-slot
frame** (256 fS), use slots 0/1/2/4/5.

**Drafted + committed, NOT built/loaded** (all inert until `make install`
runs the lounge profile on LoungePi):
- `install/patches/tas58xx-tdm-slots.patch` — `ti,tdm-slot-offset`, SAP
  writes in `do_work`, `channels_max` 2→8. `git apply --check` clean.
- `install/overlays/tas58xx-triple-overlay.dts` — 8-slot, multi-codec,
  flat, ribbon @ 0x2d. dtc-clean.
- `config/camilladsp/lounge.yml` — 8-ch, stereo→TDM8 mixer, 3-way LR4
  (placeholder freqs), ribbon protection, empty REW slots.
- `install/05-tas-driver.sh`, `install/10-soundcard/louder-hat-triple.sh`,
  `profiles/lounge.yml` updated. 52 tests green.

**✅ BLOCKER RESOLVED (2026-06-01):** `SAP_CTRL1 (0x33) = 0x17`, datasheet-
confirmed. TAS5825M **and** TAS5805M document SAP_CTRL1 identically
(§7.6.1.9 / Table 7-16): D[5:4] DATA_FORMAT `01` = TDM/DSP, D[3:2] = `01`
(FS high width < 8 SCLK — the Pi's dsp_a narrow frame sync), D[1:0] = `11`
(32-bit) → `0x10|0x04|0x03 = 0x17`. The earlier best-guess D[5:4]=0b01 was
right; D[3:2]=01 was the missing piece. Andriy separately confirmed the
0x34 slot offset = 32 × slot-index (Pi always pads to 32-bit slots) and
green-lit a hardware trial. Patch updated (no more TODO/dev_warn), `git
apply` clean, hunks intact.

**✅ TDM STACK BROUGHT UP + REGISTER-VERIFIED (2026-06-01, chassis disconnected
— zero acoustic risk).** Built the driver (0x17 patch) + compiled the triple
overlay via `install/05-tas-driver.sh`, config.txt + modules via
`install/10-soundcard/louder-hat-triple.sh`, reboot. Result on the bench:
- All 3 codecs bind, **8-ch card `Louder-Raspberry-Triple` (card 3)** enumerates,
  an 8-ch S32_LE stream opens + plays.
- Per-chip slot offsets read from DT at probe: 0x4c→0, 0x4d→64, 0x2d→128.
- After a stream triggers `do_work`, **SAP_CTRL1 = 0x17 confirmed on all 3
  chips** (i2cget), SAP_CTRL2 = 0x00 / 0x40 / 0x80. dmesg: `do_work: TDM mode,
  SAP_CTRL1=0x17, slot offset=0/64/128`. EQ off (flat), fault-monitor off.
- The `Clock fault` logged after a stream STOPS is expected (Pi halts the I²S
  clock when idle — datasheet §9.3.4, chip auto-recovers on clock return), NOT
  a bug. `do_work` (and thus the SAP writes) only runs on stream start, not at
  probe — a silent `aplay -c 8` is enough to apply + verify them.
- Skipped `00-base.sh` deliberately: its hostname rename (lounge.yml id=`lounge`)
  would break `loungepi.local` SSH; 10-soundcard already enables i2c_arm/i2s.
- Bench is plain rw root (NOT overlayroot) — changes persist directly. SSH
  host is `loungepi.local` (the `lounge` SSH-config alias points at the wrong
  `lounge.local`), key `~/.ssh/id_ed25519`, NOPASSWD sudo.

**✅ AUDIO DATA PATH FIXED (2026-06-01, scope on the bench, drivers disconnected).**
First audio attempt was broken: a level-INDEPENDENT distorted square (~10 V),
pitch followed the input but amplitude didn't — the chips were reading only the
sign bits because the TDM data window was misaligned. Found the fix with the
OWON scope by sweeping SAP_CTRL2 live during a continuous tone: the Pi's TDM
frame places the data **+4 BCLKs** after the nominal slot boundary. At offset+4
the output became a clean sine whose amplitude tracks the digital level. So all
slot offsets get +4: **mid 4, woofer 68, ribbon 132** (overlay updated + dtbo
recompiled, dmesg confirms, mid output scope-verified clean + level-tracking).
This is exactly the bit-alignment Andriy couldn't verify without hardware.
Ribbons measured 4.3 Ω and survived the debugging (disconnected during it).

**✅ PER-CODEC ANALOG GAIN SOLVED (2026-06-02, commits 4ef6b30 + 33e17b9).** The
multi-codec `simple-audio-card` link only surfaces ONE codec's mixer controls
(Mid), so the woofer/ribbon AGAIN (reg 0x54) had no ALSA control and the driver
reset it to 0x00 (MAX) on every stream start — a flat-ribbon hazard. Fixed in the
driver patch: an optional per-codec `ti,analog-gain` DT property, **seeded into
`tas58xx->gain` at probe** so the driver's normal gain-apply writes it (a do_work
regmap_write alone got clobbered by that apply). Bench-verified holding during a
live stream: **Mid 0x06 (−3 dB) · Woofer 0x0b (−5.5 dB) · Ribbon 0x10 (−8 dB)**.
Race-free, no bridge polling. The ribbon is now capped at −8 dB analog before any
chassis is connected. `05-tas-driver.sh` now resets the source + applies with
`--recount` so the patch can grow without hand-fixing @@ offsets.

**Remaining (needs the chassis physically connected — user does this "wenn alles
da ist"):** channel-ID (which TDM slot drives which driver) at **minimal**
volume, **fan running**, woofer+mids first / **ribbons LAST** → CamillaDSP
crossover baseline (`config/camilladsp/lounge.yml`, placeholder freqs) → REW
per-driver measurement → final crossover + per-driver EQ. HALT rule still
applies the moment a driver is connected.

**Also pending for Lounge:** UI board (button + 3-colour LED ring)
function test + `pigpio` service for LED dimming/button (GPIO 17=button,
22/23/24=LEDs, documented in `docs/Lounge.md`). MOSFET soft-start for the
PSU inrush. REW per-driver measurement → final crossover + per-driver EQ.

### 🧩 TAS chip features under flat-TDM — what stays relevant (decision record)

LoungePi runs all 3 chips flat in TDM (EQ off, crossover + mono-sum + loudness
all in CamillaDSP). The chip DSP is deliberately bypassed; recorded so we don't
re-add it:

**Not used, on purpose — CamillaDSP owns it:** internal 15-band EQ, biquad
crossover, dynamic EQ/DPEQ, bass enhancement, spatializer, THD manager,
internal mixer/mono-sum. Re-introducing any in-chip just fights the Camilla
pipeline. → Also shrinks the driver scope: exposing raw biquad coefficients via
ALSA is now **obsolete**; only protection registers (if any) are worth a patch.

**Still relevant — only what CamillaDSP physically can't do** (needs the chip's
own sensors / sits at the amp output, after the whole pipeline):
- **Thermal foldback** — junction temp monitored continuously; internal AGL
  applies *gradual* gain reduction keyed to the 4 OTW warning bands (reg 0x73
  b0–3), auto-restored on cooldown. The "4 levels" = warning thresholds, not
  4 coarse volume steps. (TI SLAA846)
- **PVDD tracking (dynamic headroom)** — clips against the real supply rail;
  clean behaviour on 24 V sag (inrush / bass transients).
- **AGL / multi-band DRC as last-resort output limiter** — fail-safe behind the
  whole chain, esp. the ribbon (TAS5805M overcurrent, measured 4.3 Ω). Not for
  voicing — purely a protection wall.

**Telemetry readback (I²C):** PVDD is readable as an absolute value (PVDD_ADC
reg 0x5E, 8-bit). Die temperature is **not** — only the 4-level OTW band (0x73
b0–3) + OTSD shutdown flag, no °C register. Real temp logging needs an external
sensor.

**Caveats:** keep `ti,fault-monitor` OFF in TDM (I²C polling disturbs the shared
bus) — these protections run autonomously in-chip, no polling needed. Feature
availability depends on the loaded process flow (TI SLAA846), and snd-soc-tas58xx
would need to expose the SAP/AGL/thermal registers — open whether that beats
chip defaults. [[feedback-verify-external-lib-features]]

### 📐 BeatPiMini — self-built 2-way (designed, not built)

5" SB Acoustics SB13PFCR25-4 + SB13PFCR-00 passive radiator + salvaged
LT300 ribbon, Pi Zero 2W + Louder Hat Plus 1X, AMOLED + 2× SK6812 RGBW
side strips. Mono 2-way active crossover in CamillaDSP (~2.8 kHz LR4,
placeholder), 4-layer ribbon protection. Config committed
(`profiles/beatpimini.yml`, `config/camilladsp/beatpimini.yml`,
`docs/BeatPiMini-enclosure.md`).

**Order of work:** (1) ribbon impedance sweep on the bench → Fs → safe
crossover (the UCA222/PipeWire attempt was abandoned — channel-sync made
REW impedance unreliable; do it at the amp output post-build instead).
(2) box modelling (WinISD) from the SB datasheets. (3) firmware LED-strip
module (`src/leds/strip_render.cpp`, FastLED RMT, "VU bubble" off
`State::app.energy`, ≤60 % brightness cap for the 5 A buck). (4) enclosure
build (off-repo). (5) final crossover by ear.

### 🛠️ Identity split — model / instance / user-label (phases 1–4 DONE, prep/big-rocks)

> **Update 2026-06-04:** phases 1–4 implemented (see Recent, top). Decided: full
> plan — CPU-serial instance id, derived `<model>-<short-id>`, browser-editable
> friendly_name, pin existing `speaker_id`s then collapse the profiles. Remaining
> phases 5 (MQTT pin + beat.yml collapse — gated on the live HA broker) and 6
> (provisioning hostname). Full record: [docs/identity-split.md](docs/identity-split.md).


Separate the profile's `friendly_name` (currently doubles as device-class
AND per-unit name) into three layers: hardware-class (profile YAML),
hardware-instance (Pi CPU serial, auto-detected → stable MQTT/speaker_id),
user-label (`friendly_name` in settings-overrides, browser-editable).
Enables friends-friendly setup (name the speaker in the browser, no SSH/
YAML) and collapses `beat-1.yml`+`beat-2.yml` into one `beat.yml`.

6-phase rollout sketched. **Open decisions before executing:** hardware-ID
source (CPU serial vs MAC), naming pattern (suggest hybrid: hostname
`<model>-<short-id>`, friendly_name = user choice), MQTT topic migration
to avoid losing HA history.

---

## Backlog — open

### Code-review leftovers
- [x] **Web vol-mapping ignores profile gamma** (2026-06-03, commit 779a640) —
  `webserver.py` called `db_to_pct`/`pct_to_db` with library defaults (min -60,
  max 0, gamma 1.0), so on `curve_gamma>1` speakers the dashboard % differed from
  the display AND a 100% drag pushed past `max_db` (zipp-mini-2: hit 0 dB vs the
  −10 dB ceiling). New `_vol_params()` reads `profile.audio.volume` and is spread
  into all three call sites. Verified on the Zipp: 100% → −10 dB (was 0 dB).
- [x] **`_refresh_system()` can overwrite a live volume drag** (2026-06-03,
  commit 682b4d1) — the legacy polling dashboard that caused it is gone (migrated
  to template). Re-added live sync the right way: dashboard polls cheap
  `GET /api/volume` every 2.5 s so the slider follows the display knob / other
  clients, guarded by a 1.5 s last-touch window + activeElement check so a drag
  is never stomped.
- [x] **`SAFE_FIRST_BOOT_PCT = 25` magic constant** → `audio.volume.safe_first_boot_pct`
  (done earlier; bridge reads `self.vol_safe_first_boot`).
- [x] **`SpotifyClient._call` return convention** (prep/big-rocks 37061ad) —
  contract documented (None=failed / dict=2xx body / `{}`=content-less);
  `get_state()` hardened (`if not status` handles the degenerate `{}`).
  `tests/test_spotify_state.py` (8 tests: None contract + artist/album fallbacks
  + duration clamp).
- [~] **`sources/bluetooth.py` hand-rolled D-Bus parsing** → `dbus-fast`
  (prep/big-rocks a285c70) — **step 1 done**: `BluetoothBus` plumbing (lazy
  daemon-loop + sync facade), nothing wired yet. Read-paths next (gated on a
  live adapter). [docs/bluetooth-dbus-fast.md](docs/bluetooth-dbus-fast.md).
- [~] **`beat-1.yml` / `beat-2.yml` split** — unblocked by the identity split
  (phases 2–4 done, prep/big-rocks). Collapse itself = phase 5, **gated on the
  live HA broker** (pin the `speaker_id`s before merging or HA history orphans).
- [x] **Module-level timing constants in `bridge.py`** → profile-driven
  (2026-06-04, prep/big-rocks) — `config.Timing` (status/spotify/snapcast poll,
  level-poll, state-push playing/idle, spotify-health threshold) on
  `profile.timing`; bridge reads `self._*` set in `__init__`. Defaults reproduce
  the old constants exactly (no behaviour change); a Pi 5 can now poll tighter,
  a Pi Zero 2W relax. (`test_config.py::test_timing_*`.)
- [x] **dead `dma_done_count`** in firmware `main.cpp` — removed (+ its dead
  `-Wvolatile` pragma).

### Web UI / polish
- [x] **"Persist settings" button** (2026-06-03, commit bc2001e) — on
  overlayroot=tmpfs the settings-overrides (palette / idle / **loudness voicing**)
  live in tmpfs, so browser tweaks apply live but don't survive a reboot. Helper
  `/usr/local/sbin/beatbird-persist-overrides` (installed by `55-web-sudo.sh`,
  sudoers-allowed) remounts `/media/root-ro` rw and copies the live overrides
  onto it; no-op on plain rw root. Exposed as 💾 *"Einstellungen dauerhaft sichern"*
  in the Voicing card on `/advanced`, via `POST /api/persist` (returns a clean 503
  if the helper isn't provisioned yet). NOTE: the helper + sudoers rule install
  only on the next provisioning pass — already-running speakers need
  `sudo bash install/55-web-sudo.sh` once before the button works end-to-end.
- [x] **Web UI prettifying pass** (2026-06-03, commit 767599d) — amber brand
  accent (overrides Pico azure), subtle top glow + card shadow/radius, header
  bird-mark + online dot, per-source coloured badges (Spotify/BT/Snapcast),
  current-page nav highlight. Deployed + render-verified on the Zipp.
- [x] **Display-matched theme** (2026-06-03, commit 22d3327) — the web UI now
  echoes the AMOLED's Nothing-Glyph look: bundles Departure Mono (OFL, the same
  display font) as a webfont, and a `theme()` Jinja global mirrors firmware
  `theme.h` (pure-black bg, cream/linen text, champagne accent + glow + rust,
  source colours 1:1). The accent is the *effective* speaker palette (profile +
  overrides) so a colour set for the display retints the browser. Replaces the
  earlier amber brand pass.
- [x] Migrate `/settings` + `/bluetooth` to the Pico+htmx base template
  (2026-06-03, commit 22d3327) — dead inline-HTML constants deleted; both now
  inherit the display theme. `/health` still inline (diagnostics-only, not in nav).
- [x] **DSP config switcher + headroom monitor** (2026-06-03, commit 60b16fe) —
  Diagnose card hot-swaps CamillaDSP configs without a service restart (Produktion
  / **Messmodus-flat for REW** / variants), via a `dsp_config` settings-override
  the *bridge* applies (single owner — web + bridge never fight the running
  config). While a non-production config is active the bridge suspends loudness
  patching (`_dsp_flat_mode` → `_apply_loudness` choke point) so the flat config
  isn't re-EQ'd; a camilladsp restart reverts to production (runtime-only by
  design). New `zipp-mini-2-meas.yml` (crossover + sub-protect + tweeter polarity,
  no tonal EQ/loudness/limiter). Card also shows live clipped-samples + processing
  load (`GET /api/dsp-health`) so the bass-heavy click/pop is measurable. Switchable
  configs are discovered by prefix (`config/camilladsp/<name>*.yml`) — drop a YAML
  + `git pull` and it appears. Full web→bridge flow verified on the Zipp.
  TODO: `beat-meas.yml` + deploy the web commit to the Beat (was offline).
- [x] **`capture_samplerate` vs dmix rate — RESOLVED live** (2026-06-04, main
  `2d33ba2`). The `beatbird_mix` dmix slave was pinned `rate 48000`, but every
  CamillaDSP config captures `hw:Loopback,1` at `capture_samplerate: 44100`, and
  the ALSA `aloop` does NOT resample. **Live test on the Zipp** (silent /dev/zero
  into the mix, read both hw_params during playback): `pcm0p` (dmix) AND `pcm1c`
  (CamillaDSP) both read **44100**. So CamillaDSP — the persistent first-opener —
  pins the cable at 44100 and dmix's `rate 48000` was silently forced down to it.
  **No pitch bug.** Fix: dmix slave `rate 48000` → `44100` so the config states
  reality (zero runtime change) and can't clash if dmix ever opens first (e.g. a
  camilladsp restart mid-stream). CamillaDSP still resamples 44100→48000 for the
  amp. `capture_samplerate: 44100` is correct, left as-is.
- [ ] **Source-change pulse** — one-shot `source_marker` scale 1.5× for
  ~300 ms on `Dirty::SOURCE`. ~30 min.
- [ ] **Player action + particle-text, standby flip-char** (mock:
  `docs/mockups/play-scintillation.html`) — **ESP32-S3 draw-budget aware**: the mock
  shows a live ops/frame badge (green ≤380, the proven baseline is ~84) and a target-FPS
  throttle (default 30) to preview real device cadence. "More action" comes from cheap
  elements: a radial 16-band spectrum (`FX:`), a beat pulse on the vol ring + center
  halo, and an energy glow (stacked translucent circles, NOT a full-screen gradient).
  Particle count is a budgeted slider (default ~300, was 2000) — the solid LVGL text
  guarantees legibility so the particles can under-sample the glyphs cheaply. Two
  distinct text languages:
  - **Play** — title/artist are *built* from a point cloud, then stay as normal crisp
    LVGL text; only the building points disperse. Timeline on `Dirty::TITLE`: *gather*
    (old points collect into a churning cloud) → *form* (points ease onto the new
    glyphs' sampled points) → text solidifies and STAYS readable → *disperse* →
    *ambient* (the building points spread back out into a music-reactive field;
    `energy_smoothed` sets its density/brightness). Each particle caches three goals
    (cloud-home / glyph-target / ambient-home). Firmware: keep the real LVGL label for
    readability, add a `scint_layer` pool that samples glyph targets ONCE per
    `Dirty::TITLE` and runs the phase-lerp per frame.
  - **Standby** — go fully flip-char (split-flap) for ALL text, not just the idle
    line: clock, temperature, condition, date each animate via `split_flap.cpp`
    (random-glyph cycling, staggered, locking to target). Fixed chars (`:` `°` space)
    don't cycle. The clock flips on each minute tick; all lines flip in on standby
    entry. KEEPS the existing standby ambient scintillation dot field and the
    dot-vocabulary weather icons (sun/cloud/rain/snow/thunder) from
    `screen_standby.cpp` — only the text rendering changes to flip-char.
  Play transitions are alpha-low-passed (per-particle + solid text) so phase
  boundaries don't snap; the form flash uses bell easing. Mock is tunable for both
  (flap tick/cycles/stagger, scintillation count/opacity, weather; particle
  timing/spread/density, vol-ring animation off-by-default).
  - **FIRMWARE IN PROGRESS** (branch `claude/claude-md-docs-dqarY`, not yet
    compile-verified — no SDL2/PlatformIO in the web env; build the sim on the dev
    box: `cd firmware/amoled-1.43 && pio run -e sim`):
    - Standby flip-char: DONE — `split_flap.cpp` keeps fixed chars (`:`/space/UTF-8
      bytes) stable; `screen_standby.cpp` flips clock/temp/highlow/condition; scint
      + weather icons untouched.
    - Play: DONE (safe variant) — new `scint_layer` in `screen_player.cpp` with an
      energy-reactive ambient dot field + a gather→hold→disperse particle cloud on
      `Dirty::TITLE`; title/artist are now plain solid labels (flap removed from the
      player); vol-ring energy wobble damped 0.65→0.12. `PLAY_DOTS=180` @30fps —
      tune on the sim for the real S3 budget. Decision: literal glyph-forming
      ("aus der Punktwolke") deferred — needs font-bitmap sampling, to be added and
      tuned on the simulator later.
- [ ] **Settings carousel page 3+** — source switcher / brightness preset /
  EQ preset / "forget all phones" / rename. Gesture + tileview already
  there; just add tiles.

### CamillaDSP optimisation ideas (surfaced 2026-06-03)
- [~] **Native `Loudness` filter** — DECIDED **Hybrid** (static voicing stays as
  EQ filters, native filter owns the volume-dependent boost). **Step 1 done**
  (prep/big-rocks 0a52a57): A/B config variants `beat-loud.yml` +
  `zipp-mini-2-loud.yml` (production EQ + a native `Loudness` filter), switchable
  via the existing DSP-config switcher — **no production config / controller
  touched**. Step 2 (mode flag + controller rework + web mapping) gated on the
  audible A/B. Findings + procedure: [docs/native-loudness.md](docs/native-loudness.md).
  Note: native low-shelf <70 Hz vs the Zipp's 80–120 Hz comp → Beat is the
  cleaner native fit.
- [ ] **Gain-staging / headroom** — the bass path stacks ~+14 dB; even with the
  soft-clip limiter, sustained bass slams it (the click/pop suspect). A small
  global pre-attenuation would keep the limiter off most of the time. Use the new
  `/api/dsp-health` clip monitor to measure before/after.
- [x] **Limiter truth** (2026-06-04, commit 3157040) — fixed the wrong
  "attack 5 ms / release 80 ms" comments in zipp-mini-2.yml + beat.yml; the
  CamillaDSP `Limiter` is an instantaneous soft-clipper. (Moving to `Compressor`
  w/ makeup for real attack/release control is still an option, not done.)

### Sound design ideas (non-blocking)
- [ ] Tilt-EQ filter with MQTT-switchable warm/neutral/bright presets.
- [ ] Adaptive compression above ~70 % volume (driver protection).
- [ ] Subtle M/S treble widening for small speakers.
- [ ] Per-source EQ bias (+2 dB air on BT-A2DP only).
- [ ] Night Mode flag (HA toggle → more loudness + lower max_db).

### Bigger / someday
- [ ] **Household setup epic** — superseded by / merges with the identity
  split (hostapd captive-portal onboarding, factory-reset flag).
- [ ] Genre-EQ presets via PatchConfig.
- [x] Rotate file logging (`/var/log/beatbird/bridge.log`) (2026-06-05) — new
  stdlib-only `beatbird.logging_setup` (size-rotated `RotatingFileHandler`, opt-in
  via `BEATBIRD_LOG_FILE`, tunable `BEATBIRD_LOG_MAX_BYTES` / `_BACKUP_COUNT`).
  Default behaviour unchanged (stdout → journald); an unwritable path degrades
  to stdout-only instead of crashing at boot. 7 tests. **To actually enable on a
  speaker:** set `BEATBIRD_LOG_FILE=/var/log/beatbird/bridge.log` in
  `/etc/beatbird/env` AND add `ReadWritePaths=/var/log/beatbird` (+ a tmpfiles/dir
  create) to the bridge unit in `install/70-bridge.sh` — that install wiring is
  left for a bench pass since it can't be verified off-device.
- [ ] Cover-art background — parked across all speakers (ESP32-S3 too slow
  for the 466×466 JPEG composite; needs smaller/pre-decoded/partial-redraw).
- [ ] Spectrum reanimation — needs `/etc/asound.conf` dsnoop + `[fft]`
  extra; marginal upgrade over the LV: ring, only if motivated.

### Parked
- [ ] **Power button** — code + firmware (`PLAY_SHUTDOWN_WARN/SHUTDOWN`
  states, long-press → poweroff) shipped but disabled: GPIO3 (Pi wake pin)
  conflicts with the Louder Hat I²C SCL. Re-enable after rewiring to a free
  pin (GPIO 17/22/27) when a housing is next open.
