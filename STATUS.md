# BeatBird — Project Status

> Last updated: 2026-09-23 (previous: 2026-09-16, 2026-06-04). Re-derived from
> `git log`, the CI history and the devices themselves. If this drifts again,
> prefer `git log` over this file.

## Active speakers

| Speaker | Hardware | Firmware board | Status |
|---|---|---|---|
| **Beat #1** | Pi Zero 2W · Louder Hat Plus 2X | AMOLED 1.43 | ✅ Production — native CamillaDSP Loudness + signal-adaptive Compressor in `beat.yml` (A/B'd by ear 2026-06-06) |
| **Zipp Mini 2** | Pi Zero 2W · Louder Hat Plus 1X | AMOLED 1.43 | ✅ Production — still on the hand-rolled loudness patch-loop + soft-clip Limiter only (no Compressor yet, see Roadmap) |
| **RobinPi** | Pi Zero 2W · Louder Hat Plus 1X (PBTL/mono) | AMOLED 1.75 (CO5300) | ✅ Production (kitchen) — Voicing v4 (PR mass-loaded, f_b 40 Hz; in `main` since PR #15), native CamillaDSP loudness, PBTL amp-init fix, Snapcast target for Music Assistant |
| LoungePi | Pi 5 (1 GB) · 3× Louder Hat | — | ⏸️ **Not planned** (2026-09-23) — no Lounge speaker exists or is scheduled; profile + `docs/lounge-multilane.md` stay as reference, see Parked |
| BeatPiMini | Pi Zero 2W · Louder Hat Plus 1X | — | 📐 Designed, not built — no movement since June (`docs/BeatPiMini-enclosure.md`) |

Firmware releases are OTA'd via tagged `fw-v*` GitHub releases; latest tag is
`fw-v0.9.15`. (This file can't confirm what's actually flashed on each
device — check `make status` / the web dashboard on the speaker itself.)

## Architecture (stable)

- **CamillaDSP volume = single source of truth.** All sources → ALSA
  Loopback (`beatbird_mix` dmix) → CamillaDSP captures `hw:Loopback,1`.
- **Profile YAML = one file per speaker** (`profiles/<name>.yml`),
  Pydantic-validated, drives soundcard / DSP config / systemd / MQTT / firmware env.
- **Bridge ↔ display protocol**: pipe-separated serial lines (`ST:`, `SYS:`,
  `PAL:`, `WX:`, `DATE:`, `STBY:`, `TOAST:`, `QR:`, `HT:`, `LED:`, `FACE:`).
  `FACE:` (merged 2026-09-16, PR #5) is the newest addition — HA publishes
  glanceable *decisions* (not raw sensor values) as retained MQTT JSON on
  `beatbird/hints/<id>`; the bridge caches/expires them and renders up to 4 as
  a standby rotation. Full grammar: `docs/protocol.md`.
- **CI**: `python.yml` (ruff + pytest, 267 tests) on src/tests/profiles
  changes; `firmware.yml` builds **3** ESP32 envs (`zipp-mini-2`, `beat-1`,
  `robinpi`; `beat-2`/`zipp-2` stay CI-excluded placeholders), tag → release.
  ⚠️ `firmware.yml` runs on PRs **only for `firmware/**` changes** — a red
  `main` stays invisible from Python PRs (happened 16.–23.09.: LVGL 9.6 via
  `^9.2.2` overflowed robinpi's DRAM). Firmware libs are now **exact-pinned**
  (lvgl 9.5.0, XPowersLib 0.3.3, SensorLib 0.4.1) — at 88 % DRAM, a `^` range
  is a time bomb.
- **Device drift**: `beatbird-update` pulls the repo only — it does NOT
  re-render `/etc` configs or refresh `/usr/local/sbin` scripts. Every such
  fix still needs a manual rollout (overlayroot: into the base + reboot).

---

## Shipped 2026-09-16 → 2026-09-23

- **Snapcast volume = CamillaDSP only** (PR #12) — `snapclient --mixer none`,
  bridge syncs the per-client slider both ways (was two multiplying stages;
  display ring jumped between them).
- **WiFi watchdog escalates** (PR #13) — reconnect by device → radio reset
  (USB re-authorize / module reload) → reboot with journal tail saved to
  `/boot/firmware/beatbird-lastfail.log`; hardware watchdog
  `RuntimeWatchdogSec=15`. The old stage-1 `nmcli … beatbird` never matched
  any device's connection name.
- **Firmware CI green again** (PR #14) — exact library pins.
- **Repo ↔ device sync** (PR #15) — RobinPi Voicing v3/v4 + PBTL amp-init fix
  (existed only on the dev PC), device-only profile values, Beat MQTT enabled.
- All three speakers on the same `main`; Beat's and Zipp's working trees clean.

## Shipped 2026-06-04 → 2026-09-16 (condensed)

Full detail in `git log`; commit messages here are written for exactly this
purpose (verbose, decision-carrying). Grouped by theme:

- **Identity split, phases 2–4** — `resolved_speaker_id/hostname/friendly_name`
  derived from CPU serial; browser-rename now also renames the BlueZ alias
  *and* the Spotify Connect device name. **Phase 5 (collapse `beat-1.yml` +
  `beat-2.yml`) is still NOT done** — see Roadmap.
- **Loudness/DSP overhaul (Beat only)** — retired the bridge-side loudness
  patch-loop in favour of CamillaDSP's native `Loudness` filter, then added a
  signal-adaptive `Compressor` (A/B'd by ear, "comp klingt am besten, da kommt
  Fülle mit", 2026-06-06) ahead of the limiter. This is the resolution of the
  old "click/pop on Beat #1 + Zipp Mini 2" roadmap item — **for Beat only**;
  Zipp Mini 2 was never given the same treatment (see Roadmap).
- **Display language rework ("Warm Funktional")** — Inter grotesk replaces
  Departure Mono (firmware *and* web UI theme); round-safe arcs; time-of-day
  standby (auto-dim, night clock, phase greetings); cross-fade idle line
  (dropped split-flap there); real weather icon font; `DATE:` protocol line.
- **Album-cover background: tried again, abandoned again.** Halftone-grid
  cover rendering was built, tuned (outer-fade, anti-judder, clip-skip), then
  explicitly reverted 2026-06-06 ("back to the clean static UI") — same
  ESP32-S3 draw-budget ceiling as before. Stays parked.
- **Standby faces** (PR #5, merged 2026-09-16) — `FACE:` protocol, HA-side
  decisions rendered in the clock's own geometry, tap-to-acknowledge. See
  Architecture above.
- **Reliability** — false-standby-kills-Spotify-session fix; zram compressed
  swap (default on, sized against actual RAM) to stop Zero 2W OOM; boot-clock
  wait before go-librespot (TLS-vs-boot-clock race); forced BT
  non-discoverable at startup; `beatbird-update` self-update tool
  (overlayroot-aware, chowns the base repo correctly).
- **RobinPi bring-up** — PBTL/mono support for Louder Hat Plus 1X, AMOLED 1.75
  (CO5300) board support, profile-driven status LED strip (`display.status_led`
  → `LED:` line), standalone panel-diagnostic firmware env, measured
  Voicing v1 (2026-09-06), Snapcast enabled for a kitchen Music Assistant
  target (PR #7).
- **Bluetooth GetPCMs spam fix** (PR #3) — skip the D-Bus poll when no device
  is connected.
- **Sub-crossover bypass fix** (2026-09-16, this session) — `'0.1 Equalizer'
  Off` was disabling the TAS5825M's crossover along with the (unused) EQ, on
  any Louder Hat Plus 2X sub channel. Fixed to follow the crossover setting.
  **Beat #1's `beat.yml` REW filters were tuned against the bypassed
  (full-range sub) state — needs a remeasurement post-deploy, not just a
  redeploy.**
- **Web UI** — Warm Funktional theme carried over from firmware; mini-Camilla
  graphical EQ editor (per-band, with response curve).

---

## Roadmap — active

### 🎚️ Zipp Mini 2 headroom — the Beat fix was never mirrored
The 2026-06 "click/pop" diagnosis applied to *both* Beat #1 and Zipp Mini 2,
but only Beat got the native-Loudness + Compressor treatment. `zipp-mini-2.yml`
still only has the original soft-clip `broadband_limiter` (instantaneous,
no attack/release) and the hand-rolled bridge loudness patch-loop. If the
symptom is still reported on the Zipp, the fix is already prototyped
(`zipp-mini-2-loud.yml` exists as the native-Loudness A/B variant) — just
needs the same A/B-by-ear pass Beat got, then a Compressor added the same way.

### ⏸️ LoungePi — not planned (moved out of active, 2026-09-23)
Design is settled (`docs/lounge-multilane.md`): 2-lane compromise, mid+sub on
lane D0 (in-chip crossover, Beat-style), ribbon on lane D1 (crossover + all EQ
in CamillaDSP). Register-level bring-up (TDM SAP_CTRL1/2, per-codec analog
gain) was bench-verified with chassis *disconnected*. **Nothing has moved
since 2026-06-04** — still blocked on physically wiring the chassis
("wenn alles da ist"). Sequence when it happens: channel-ID at minimal volume
→ CamillaDSP crossover baseline → REW per-driver measurement → final
crossover/EQ. HALT rule applies the moment a driver is connected.

### 🛠️ Identity split — phase 5 (collapse beat-1/beat-2)
Phases 2–4 done (see Shipped). Phase 5 — pin existing `speaker_id`s then merge
`beat-1.yml`+`beat-2.yml` into one `beat.yml` — is **gated on the live HA
broker** (must pin IDs first or HA history orphans). Still not started; the
two profiles remain fully duplicated as of this writing.

### 📐 BeatPiMini — designed, not built
No commits since June. Order of work unchanged: (1) ribbon impedance sweep on
the bench, (2) WinISD box modelling, (3) LED-strip firmware module, (4)
enclosure build (off-repo), (5) crossover by ear. `profiles/beatpimini.yml` +
`config/camilladsp/beatpimini.yml` + `docs/BeatPiMini-enclosure.md` are
committed placeholders.

### 🔌 Bluetooth dbus-fast migration — still just step 1
`BluetoothBus` plumbing (`a285c70`, June) is dormant — grep confirms nothing
in `sources/bluetooth.py` actually calls it yet. Read-paths migration is
still gated on testing against a live adapter.

---

## Backlog — open

- [ ] **Settings carousel page 3+** — still just QR + Pair-Bluetooth tiles;
  no evidence of source-switcher/brightness/EQ-preset/rename tiles being added.
- [ ] **Source-change pulse** — one-shot scale animation on `Dirty::SOURCE`.
- [ ] Sound design ideas (untouched): tilt-EQ presets, adaptive compression
  above ~70% volume, M/S treble widening, per-source EQ bias, Night Mode.
- [ ] **Household setup epic** — hostapd captive-portal onboarding,
  factory-reset flag. Superseded-by-merges-with the identity split; still open.
- [ ] Genre-EQ presets via PatchConfig.
- [ ] Spectrum reanimation (needs `/etc/asound.conf` dsnoop + `[fft]` extra) —
  marginal upgrade over the LED ring, low priority.

## Parked / confirmed abandoned

- **Cover-art background** — tried a second time (halftone grid, June) and
  reverted again. Treat as a settled "no" until the ESP32-S3 gets more draw
  budget or the approach changes fundamentally (pre-decoded/partial-redraw),
  not as a live backlog item.
- **Power button** — GPIO3 (Pi wake pin) conflicts with the Louder Hat I²C
  SCL. Re-enable after rewiring to a free pin (GPIO 17/22/27) when a housing
  is next open.
