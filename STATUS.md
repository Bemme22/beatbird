# BeatBird — Project Status

> Last updated: 2026-05-19

## Active speakers

| Speaker | Repo | Status | OS |
|---|---|---|---|
| Beat #1 | beatbird (new, migrated 2026-05-19) | 🔧 Functional, overlay still disabled (polish pending) | Bookworm |
| Zipp Mini 2 | beatbird (new, v2.1.0) | ✅ Production (sound, display, standby, play/pause stable) | Trixie |

## Install fixes committed (v2.1.1)

All five workarounds from Zipp Mini 2 first boot are now in the repo:

1. ✅ `install/30-camilladsp.sh`: version 4.0.0→4.1.2, arch suffix `aarch64` (not `aarch64-linux-gnu`)
2. ✅ `install/40-go-librespot.sh`: removed `--version` check (binary doesn't support it)
3. ✅ `install/_lib.sh`: new `ensure_module_loaded` helper — adds module to both `dtoverlay` and `/etc/modules` (Trixie compat)
4. ✅ `install/70-bridge.sh`: creates `/var/lib/beatbird` (required by systemd `ReadWritePaths`)
5. ✅ `config/systemd/go-librespot.service.tpl`: no CLI flags, reads `~/.config/go-librespot/` automatically

## Lounge — UI reverse-engineering complete

- Original 7-pin UI board documented: button + 3-colour LED ring (R/Y/W)
- Transistors Q101–Q103 on-board → Pi only drives GPIO bases
- GPIO mapping finalised (17=button, 22/23/24=LEDs)
- Perfboard layout: 7 solder bridges, no active components
- Full documentation in `docs/Lounge.md`
- Blocker: custom DT overlay for 3-DAC stack (Sonocotta)

## Architecture

- CamillaDSP volume = single source of truth
- All sources → hw:Loopback,0 → CamillaDSP reads hw:Loopback,1
- Profile YAML = one file per speaker
- Source handoff = mutual-kill
- BT volume via BlueALSA Manager1.GetPCMs (uint16: (L<<8)|R, 0..127)

## Session 2026-05-19 — shipped

- ✅ Bug 1: Connect-screen hang when ESP32 power-cycles mid-session
  (`[boot]` marker + bridge re-sends `PAL:` on receipt; firmware also clears
  `connected_to_pi` on any inbound bridge line as a fallback)
- ✅ Bug 2: Volume blasts to MAX at box boot
  (stale-DSP-state snap to 25% in `bridge.start()`, first-Spotify-sync
  pushes DSP→Spotify instead of letting `initial=65535` cascade into DSP)
- ✅ Bug 3: Connect-screen text too small
  (BEATBIRD wordmark 22→33 px, "waiting for pi" subline 11→22 px; standby
  clock left at 44 px per user request)
- ✅ Volume tuning, Zipp Mini 2 only (opt-in per profile):
  - `pct_to_db` accepts `gamma` curve param; profile sets `curve_gamma: 2.0`
    → Sonos-style audio taper, lower half of slider is finely resolved
  - `offset_curve` has `legacy` and `smoothstep` variants; profile sets
    `curve: smoothstep` → cubic plateau 0..10% UI, decay through 75%
  - Bass-shelf max_boost 3→6 dB, timpani_body 1.5→3 dB
  - `broadband_limiter` (soft-clip @ -1 dB) appended to pipeline as
    safety net for the extra boost
  - Loudness `apply()` now also runs at bridge start, not just on first
    volume change

## Session 2026-05-19 (handoff-driven bugfixes) — shipped

- ✅ **Bug 1+2: Standby state machine** (`bridge.py`) — after 5 min of
  non-PLAYING, bridge enters standby: pushes `ST:standby` (display switches
  to clock face) and calls `POST /player/close` on go-librespot to free
  the Spotify Connect slot. Exits on next PLAYING playback or display tap.
  Closes the nightly auto-play vector (someone else's phone grabbing the
  speaker via Connect) AND the "display never sleeps" complaint in one go.
- ✅ **Bug 3: AMOLED heartbeat watchdog** (`display/amoled.py`) — ESP32
  sends `[hb]` every 10 s; bridge tracks `_last_hb_received` and force-
  closes/reopens the serial port if no heartbeat for >60 s. Fixes the
  USB-CDC zombie symptom (write returns OK but bytes never reach the ESP).
- ✅ **Bug 4: PLAYPAUSE wrong-direction race** (`bridge.py`) — was sending
  server-side `/player/playpause` toggle, which resolved the wrong
  direction ~20 % of the time when librespot's view of state lagged a
  Spotify-Connect roundtrip. Now: fetches fresh state synchronously, then
  calls explicit `/player/pause` or `/player/resume` based on that, plus
  optimistic local-state echo to the display. ~98 % reliable per real-
  world testing.
- ✅ **librespot health watchdog** (`bridge.py`) — `_poll_spotify` counts
  consecutive `None` returns from `get_state()`; after 15 in a row
  (~30 s), runs `systemctl restart go-librespot`. Catches the case where
  the process is alive but its HTTP API is wedged (systemd's
  `Restart=always` only kicks in on crash).
- ✅ **Energy ring fix** (`audio/camilladsp.py`, `display/amoled.py`) —
  two bugs: (a) `GetSignalLevels` was sent as `{"GetSignalLevels": null}`
  but CamillaDSP 4.x needs the bare string `"GetSignalLevels"` for no-arg
  commands, so the bridge returned `0` for signal level forever; (b)
  even with that fixed, the firmware's `spectrum_bands > 0` branch (FX
  field) took priority over LV: and rendered 12 dead dots because
  `SpectrumAnalyzer` silently returned `[0]*16`. Added a guard in
  `push_state` to skip the FX: field when all bands are zero.
- ✅ **ALSA buffer 100 ms persistent** (`config/go-librespot/config.yml.tpl`)
  — added `audio_buffer_time: 100000` + `audio_period_count: 4` to default
  template. Default was 500 ms, which was the main contributor to pause-
  drain lag. Verified on Zipp Mini 2: buffer_size = 4410 frames @ 44.1 k =
  100 ms. No underruns observed.
- ✅ **louder-hat amixer init migration** (`install/10-soundcard/*.sh`) —
  Plus 2X (`louder-hat-plus-2x.sh`) and Plus 1X (`_amixer-init-plus-1x.sh`)
  both rewritten to use ALSA control NAMES instead of numids. Verified
  names on Beat #1: stereo prefix `2.0` (not `2.x`), sub prefix `0.1`
  (PBTL mono). Plus 1X also had three silently-failing names ("2.0
  Digital Volume" → "2.0 Digital", "Channel L/R Gain" → "Channel
  Left/Right Gain", `Equalizer 0` → `Equalizer Off`). No more "Operation
  not permitted" log spam on boot.
- ✅ **Beat CamillaDSP `rew_2269` re-tuning synced to repo** —
  `config/camilladsp/beat.yml` had `gain: -4.0`, live BeatPi had `-6.1`.
  Picked live as truth. Other filters were identical.
- 🔵 **Power-button feature: implemented but parked.**
  `src/beatbird/hardware/power_button.py` + bridge integration +
  `install/45-power-button.sh` (sudoers NOPASSWD: /sbin/poweroff) +
  firmware `PLAY_SHUTDOWN_WARN` / `PLAY_SHUTDOWN` states with dedicated
  centered screen. Long-press → "Halten zum Ausschalten" → 2 s →
  "Ausschalten…" → poweroff. Disabled in `zipp-mini-2.yml` because GPIO3
  (canonical Pi wake pin) conflicts with the Louder Hat's I²C SCL —
  needs a freed pin (GPIO 17/22/27) before re-enable.  Trixie/lgpio
  gotcha noted: `RPi.GPIO` import touches CWD via lgpio's notification
  pipe; `power_button.start()` chdirs to `/var/lib/beatbird` (one of the
  `ReadWritePaths` in our hardened service unit) to work around it.

## Session 2026-05-19 (evening) — Beat #1 migration + firmware polish

- ✅ **Display wake on bridge events** (`firmware/.../state.cpp` + `main.cpp`) —
  `wake_screen()` helper resets the dim timer not only on touch but also on
  any "interesting" state change (`set_play_state`, `set_source`, `set_title`,
  `set_volume`). New track / volume change brightens the display, then it
  dims back to `DIM_BRIGHTNESS` after `DIM_AFTER_MS` of inactivity.
- ✅ **6-color palette schema in `Display` profile model** —
  `accent_glow`, `accent_dim`, `text_primary`, `text_secondary`,
  `accent_alert` accepted alongside the existing `accent_color`. Beat #1
  has its full forest palette (`#2D6A4F` / `#52B788` / `#1B4332` / `#F4EFE0` /
  `#A89E89` / `#C73E2C`) recorded in `beat-1.yml`. **Only `accent_color`
  is currently transmitted** via the single-color `PAL:` protocol; the
  other five are stored, awaiting protocol/firmware extension (see roadmap).
- ✅ **Beat #1 migration** off legacy `beatbird-display` → current
  `beatbird` repo: overlayroot disabled via cmdline.txt edit on the FAT
  partition (overlay covers home + /tmp; only `/boot/firmware` is on the
  raw FAT partition), `make install` ran clean after a `raspi-config
  do_serial → do_serial_hw + do_serial_cons` patch (the legacy `do_serial`
  triggered an interactive whiptail even with `nonint`). CamillaDSP
  bumped 4.0.0 → 4.1.2, go-librespot to 0.7.1, all systemd units
  rendered from the new template. BlueALSA + snapclient services from
  the legacy install left untouched. ⚠️ Overlay deliberately left
  disabled — Beat needs another tuning pass tomorrow (loudness, WLAN
  display, brand graphics) before re-enabling.
- ✅ **Per-speaker firmware rotation: `DISPLAY_ROTATE_NATIVE`** —
  Beat #1's panel is mounted such that no MADCTL command works: every
  `0x36` value with non-zero bits produced wrong-orientation or wrap
  artefacts. The init-commit firmware shipped without any `0x36` command
  at all; that was the working state for Beat. New build flag skips the
  MADCTL write entirely. Zipp Mini 2 still uses the old `DISPLAY_ROTATE_DEG=90`
  path (MADCTL=0xA0). PIO envs renamed to match speaker profiles —
  `pio run -e beat-1 -t upload`, `pio run -e zipp-mini-2 -t upload` etc.
  Debug envs `beat-rot{0,180,270}` removed once NATIVE proved correct.
- ✅ **`set_gap` rotation-dependency fix** — the call had been silently
  swapped during an earlier refactor: init commit had `set_gap(0x06,
  0x00)` (x_gap=6), current code had it as `set_gap(0x00, 0x06)`.
  Worked on Zipp because MADCTL=0xA0 swaps x/y addressing internally,
  cancelling the bug. On Beat (NATIVE, no MADCTL) the SH8601's built-in
  6-pixel column offset showed up as wrap-around "stripes" at the
  display edges. Now conditional on the rotation flag.
- ✅ **`raspi-config do_serial` install bug fixed** —
  `install/00-base.sh` was calling the legacy `do_serial 0` which is
  interactive even with `nonint`. Replaced with the split commands
  `do_serial_hw 0` + `do_serial_cons 1` (truly non-interactive).

## Code review findings (2026-05-19)

### High priority

- [ ] **webserver vol-mapping inconsistent with bridge** —
  `src/beatbird/webserver.py:77,96` calls `db_to_pct(db)` / `pct_to_db(req.pct)`
  with default min/max/gamma. On profiles with `curve_gamma > 1.0` (e.g. Zipp
  Mini 2) the dashboard shows a different % than the display and bypasses
  loudness compensation. Should either load profile and forward gamma, or
  route through the bridge.
- [ ] **race condition: `_refresh_system()` overwrites user volume** —
  `bridge.py:561`. Runs every 5 s; if the user is mid-rotation on the display
  the next refresh can snap the UI back to a stale DSP value. Add a
  last-user-touch guard (suppress overwrite for ~1.5 s after `set_volume`).
- [x] ~~**spectrum analyzer is dead code per profile but still compiled in**~~
  → **Resolved 2026-05-19**: kept as opt-in with documented reanimation path.
  All AMOLED profiles default to `spectrum_bands: 0`. Bridge gates the
  `SpectrumAnalyzer` instantiation on `> 0`, so the FFT thread isn't even
  spawned. Re-enabling requires `pip install -e ".[fft]"` + `libportaudio2`
  + an `/etc/asound.conf` `dsnoop` alias (see template comment).

### Medium priority

- [ ] **`SpotifyClient._call` return convention is tricky** —
  `sources/spotify.py:50`. Returns `None` on error, `{}` on 204, `dict` on
  200. Callers mostly use `is not None` which treats 204 as success — usually
  fine, but `get_state()` can return a degenerate empty-dict state. Tighten
  to a consistent shape.
- [ ] **`SAFE_FIRST_BOOT_PCT = 25` is a magic constant** — `bridge.py:316`.
  Move to `audio.volume.safe_first_boot_pct` so each speaker can pick its own
  (Lounge probably wants lower).
- [x] ~~**AMOLED heartbeat-watchdog timing**~~ → **Tuned 2026-05-19**: was
  25 s during the session, which produced false-positive reconnects under
  bursty bridge→ESP traffic (every ~2 min). Bumped to 60 s = miss 6
  heartbeats in a row before reacting. Acceptable latency for genuine
  unplugs; no more spurious reopens observed.
- [ ] **`sources/bluetooth.py` 400 LOC of hand-rolled D-Bus parsing** —
  fragile against BlueZ/BlueALSA updates. Migrating to `dbus-fast` would
  shrink the file and remove the regex parser; bigger lift.

### Low priority / quick wins

- [ ] **dead `dma_done_count` variable** — `firmware/.../main.cpp:53`.
  ISR-incremented, never read; remove with the `-Wvolatile` pragma block.
- [ ] **profile naming chaos** — `profiles/` has `lounge.yml` AND
  `lounge-profile.yml`; `zipp.yml`, `zipp-2.yml`, `zipp-mini-2.yml`.
  If `beat-1.yml` is truly legacy, move it (and any unused others) into a
  `profiles/legacy/` subdirectory.
- [ ] **module-level timing constants in `bridge.py`** —
  `STATUS_INTERVAL`, `SPOTIFY_POLL_INTERVAL`, etc. Eventually should
  come from the profile so Pi-Zero-2W vs Pi-5 can be tuned separately.

### Possible follow-ups (not bugs, ideas)

- [ ] **Web UI — settings editor (V2)** — current `webserver.py` does
  status + playback + volume + DSP reload. To add: live palette editor
  (six colour pickers, push via PAL: through the bridge), loudness
  max_boost sliders per filter (live patch via CamillaDSP, persist into
  profile YAML), volume-mapping editor (min/max/gamma with response
  curve preview), source-enable toggles. Requires a small bridge-side
  IPC for the palette + profile-reload after YAML writes. V1
  (2026-05-22) shipped just the system functions and live-loudness
  sliders that don't need bridge involvement.
- [ ] **Household portability — family-friendly setup epic** (~3,5 h)
  Goal: an assembled speaker handed to a family member just works after
  plug-in. Scope is "family/friends", not commercial.
  Trigger: 3 s long-press on the display while running normally.
  Phases:
    1. ScreenSetup firmware module — uses LVGL's lv_qrcode widget, shows
       WiFi-format QR (`WIFI:T:WPA;S:BeatBird-XYZ4;P:setup1234;;`) plus
       textual SSID/password below.
    2. Bridge CMD:SETUP handler + new `setup-mode.service` that stops
       wpa_supplicant/NM client mode and brings up `hostapd + dnsmasq`
       as AP. dnsmasq with DNS-wildcard → captive portal effect (phone
       OS auto-opens the speaker's web UI on join).
    3. Web wizard at `/setup` (only active in setup mode): speaker name
       (becomes hostname + friendly_name), WiFi SSID dropdown from
       `iw scan`, password, optional Spotify/weather/snapcast hooks.
       Submit → write secrets + profile → stop AP → join target WiFi →
       reboot.
    4. Factory-reset button in normal web UI — removes
       `/etc/beatbird/onboarded.flag` and reboots into setup mode.
  Hardware note: Pi Zero 2W has only one WiFi adapter, so AP and
  client modes can't coexist — speaker has no internet during setup
  itself (~30 s), expected limitation.
- [ ] **persistent "last user volume" in `/var/lib/beatbird/state.json`** —
  so the stale-state snap has a real fallback ("last known good"), not the
  blind 25%.
- [ ] **Night Mode flag** (per MQTT/HA toggle) — even more aggressive
  loudness + lowered max_db.
- [ ] **`sources/snapcast.py` is missing** — schema accepts it, no impl yet.
- [ ] **firmware OTA via bridge** — bridge accepts a new `.bin`, flashes
  ESP32 via USB-Serial bootloader. Eliminates the trip to the desk.
- [ ] **rotate file logging** — `/var/log/beatbird/bridge.log` so debugging
  doesn't always need a live SSH + `journalctl -f`.

## Roadmap

- [x] ~~Soundcheck + display test on Zipp Mini 2~~ ✅ (98 % play/pause
      reliability, energy ring + display fully verified 2026-05-19)
- [x] ~~**Beat #1 migration** off legacy `beatbird-display` → current
      `beatbird` repo~~ → **Done 2026-05-19 evening**, see session log
      above. Overlay reactivation + polish items pending.

### Identity split: model class vs per-unit instance vs user label (proposed 2026-05-28, parking-lot)

Current state: the profile YAML `identity.friendly_name` doubles as
both *device class* ("Zipp Mini 2") and *per-unit name*. You can't
have two Zipp Mini 2s in the same house with names "Küche" and "Bad"
without editing the YAML on disk — and the YAML conceptually wants
to stay version-controlled / per-model.

Goal: **friends-friendly setup** where the speaker can be individualised
in the browser without SSH, without touching YAML, by anyone who can
read.

Three-layer model proposed:

```
┌─ HARDWARE-CLASS  (profile YAML, git-tracked, install-time)
│   • model: beatpimini | beat-1 | beat-2 | ...
│   • soundcard / DSP-chain / display / GPIO mapping
│   • DEFAULT friendly_name (used until user changes it)
│
┌─ HARDWARE-INSTANCE  (intrinsic, read from the Pi at boot)
│   • Pi CPU serial from /proc/cpuinfo (16 hex chars, unique)
│   • or WLAN MAC, or /sys/firmware/devicetree/base/serial-number
│   • becomes the speaker_id — survives SD-card reflash,
│     stays stable for MQTT topics / HA entity IDs
│
┌─ USER-LABEL  (settings-overrides.json, web UI, runtime)
│   • friendly_name: "Küche"           ← hero field
│   • palette: {...}                   ← already implemented
│   • idle: {...}                      ← already implemented
│   • (future: room, EQ-preset, source-default, ...)
```

**What that buys us:**

- Profile duplication goes away. `beat-1.yml` + `beat-2.yml` collapse
  into one `beat.yml` — same hardware, no need for per-unit YAMLs.
- SD-card reflash doesn't change identity (CPU serial stays).
- SD-card *cloning* can't produce identity collisions (CPU differs per
  Pi by definition → no two clones get the same MQTT topic).
- Profiles become reusable *device definitions* rather than per-unit
  configs.

**Friend setup flow target:**

1. Friend gets a ready-built BeatPiMini, puts it in the bathroom.
2. WLAN setup is done before handover (or via first-boot wizard).
3. Boot jingle, display shows QR.
4. Friend scans → dashboard opens with "Welcome — name this speaker".
5. Types "Bad", taps Save.
6. Display header, BT picker, Spotify Connect entry, Web UI title
   all read "Bad" within seconds.
7. Friend pairs phone via BT using the on-display swipe-down PAIR
   page (works without WLAN access — Phase 1+2 of the earlier
   settings-carousel work).
8. Plays music.

No SSH, no YAML, no call to the maintainer.

**Phased rollout:**

1. **Schema split** — add top-level `model:` to Profile, make
   `sources.spotify.device_name` optional (defaults to friendly_name).
   Update existing 6 profiles (one line each). Mechanical, ~1h.
2. **Hardware-ID source** — bridge reads CPU serial / WLAN MAC at
   start, replaces the YAML `identity.speaker_id` field as
   authoritative. Profile schema makes `speaker_id` optional /
   deprecated. ~2h.
3. **Bridge fallback layer** — helper `effective_friendly_name()`
   that checks settings-overrides first, falls back to profile
   default. Wired into BT alias, Spotify device name, display
   header, web UI title. mtime poll already exists; just extend
   the schema to include `friendly_name`. ~2h.
4. **Web UI customisation card** — `/settings` gets a hero-position
   "Speaker Name" input above the palette. POST writes
   settings-overrides.json. ~1h.
5. **First-run wizard** — dashboard `/` detects friendly_name ==
   profile default, shows naming card before anything else.
   Subsequent visits skip it. ~2h.
6. **Hostname change (advanced)** — separate /settings section with
   clear warnings (mDNS URL changes → QR breaks). Probably not
   worth doing until Phase 1-5 has soaked. ~half a day.

**Open decisions:**

- **Hardware-ID source**: CPU serial (`/proc/cpuinfo`) or WLAN MAC
  or `/sys/firmware/devicetree/base/serial-number`. CPU serial is
  the most BIOS-native; MAC is user-recognisable. Suggest CPU serial
  for the canonical ID, derive a short readable suffix (last 4-6
  chars) for the human-facing hostname.
- **Naming pattern**: Sonos-style (`beatpi-a7c9` visible alongside
  "Küche"), Apple-style (opaque UUID, only friendly_name shown), or
  hybrid. Suggest hybrid: hostname = `<model>-<short-id>` (stable
  mDNS / QR target), friendly_name = user choice (everything else).
- **`speaker_id` field**: keep in profile schema as override-able
  for legacy / testing, or remove entirely? Probably keep but mark
  as "advanced — set only for migration off the old system".
- **MQTT base_topic stability**: with auto-derived speaker_id, the
  MQTT topic for an existing speaker would change on first boot
  after the rollout. Need a one-time migration script that pins
  the existing speaker_ids before the switch (so HA entities don't
  lose history).

**Status**: idea parked, maturing. Don't execute until naming pattern
+ hardware-ID source are settled. Picked up from chat 2026-05-28.

### New speaker: BeatPiMini (proposed 2026-05-28)

A small, self-built two-way speaker — first one in the lineup that
isn't a Libratone retrofit. Designed around a clean 2-way active
topology with the existing BeatBird stack on top.

**Hardware concept (locked 2026-05-28):**
- **Mid-bass**: SB Acoustics SB13PFCR25-4 — 5", 4 Ω
- **Tuning**: SB Acoustics SB13PFCR-00 passive radiator (no motor,
  just compliance + mass → tunes the box)
- **Tweeter**: salvaged ribbon from a Libratone LT300 we scrapped.
  Confirmed functional in the original LT300 — the speaker as a
  whole sounded bad (probably crossover / box tuning), not the
  ribbon itself. 4.3 Ω at 1 kHz, model unknown so no datasheet to
  reference for Fs / recommended cross frequency. Bench measurement
  (impedance sweep) needed before we lock the crossover.
- Compute: Pi Zero 2W
- Soundcard: Louder Hat Plus 1X (single stereo TAS5825M)
- Display: ESP32-S3 AMOLED 1.43" (same firmware as other beats)
- **Light strips**: 2× HD-5V-SK6812-144L-W (warm-white RGBW,
  144 LEDs/m, ~23 cm each → ~33 LEDs per strip, **~66 LEDs total**)
  driven via RMT peripheral so it's non-blocking against the LVGL
  60 Hz render loop. RAM cost on the ESP32-S3 is negligible
  (~260 bytes for the strip buffer); main constraints are power
  supply, level shifting and the LED-render integration with the
  existing display task.

**Power & enclosure (locked):**
- **PSU**: 19 V / 3.42 A laptop brick (65 W) into the Louder Hat,
  Pi powered from there. Buck-down 5 V / 5 A for the LED strips.
- **LED current budget**: 66 × ~60 mA full-white = ~4 A peak at
  5 V. The 5 A buck is at the edge — animations should cap brightness
  (e.g. ≤ 60 % at full white, or never light all channels max
  simultaneously). Warm-white-only baseline draws ~1.3 A, plenty
  of headroom.
- **Level shifting**: 3.3 V → 5 V via 74AHCT125 on the data line
  (SK6812 is more tolerant than WS2812 but the buffer is the
  reliable solution; without it the first ~5 LEDs are flaky).
- **Enclosure**: monoblock, trapezoidal base, visual cue from the
  Libratone Beat. **Both LED strips on the sides, bottom-to-top**.
  Mono signal (one speaker, no stereo pair) — see CamillaDSP impact
  below.

**Audio chain (with the locked drivers):**

- **Mono → 2-way active crossover in CamillaDSP**. The Louder Hat
  Plus 1X normally carries stereo L/R for the same speaker; here
  we repurpose:
    `L_in + R_in → mono` (sum to one channel)
    `mono → LP @ ~2.8 kHz → amp_out_L → 5" SB Acoustics`
    `mono → HP @ ~2.8 kHz → amp_out_R → ribbon`
  LR4 (24 dB/oct) is the starting point. Final frequency + slope
  set after a ribbon impedance sweep — its Fs determines the
  minimum safe crossover, and "salvaged from LT300 because it
  sounded bad" is information worth a remeasure.
- **Box tuning** — SB13PFCR25-4 + SB13PFCR-00 PR is a designed pair.
  Internal volume + radiator mass per the SB Acoustics datasheets.
  Offline modelling in WinISD / VituixCAD before any wood is cut;
  not in repo scope but flagged so we don't ship before it's done.
- **Ribbon protection** (in order of importance):
    1. Brick-wall HP (LR8+) ~200-300 Hz below the crossover →
       any DC / sub-bass through the ribbon path gets nuked
    2. CDSP peak limiter at ~-3 dB to catch amp clipping
    3. Physical series cap (3.3-4.7 µF film) in line with the
       ribbon wires → unbypassable DC blocker for misconfig + boot
    4. Lower `analog_gain_db` than the other speakers (-6 or -9 dB
       at boot, ramp after CDSP loads)

**Power, level shifting, LED render (locked):**

- Animations cap at ~60 % bright + never light all four channels at
  max → keeps peak under the 5 A buck's limit.
- 74AHCT125 buffer for the 3.3 V → 5 V data line. SK6812 forgiving,
  but the first few LEDs are unreliable without it.
- **LED strip render**: two vertical strips on the side flanks,
  bottom-to-top. Pattern: base brightness driven by
  `State::app.energy` (same source as the AMOLED ring), peak
  transients lift a "bubble" that travels up + fades. Same
  asymmetric envelope (α=0.45 attack, α=0.08 release) — same
  visual character as the energy ring.
- New firmware module: `firmware/.../src/leds/strip_render.cpp`.
  No new bridge protocol needed (reads existing `State::app.energy`).

**Remaining open items:**

- Ribbon Fs measurement (impedance sweep) — bench test, off-repo.
  Determines minimum safe crossover frequency. Ribbon confirmed
  working in the LT300 already, so this is "what's the safe
  operating range", not "does it work".
- Final crossover frequency + slope after the Fs is known
- Box volume + PR mass tuning (offline modelling, WinISD)
- LED max-brightness cap value — empirical, dial in until the
  buck doesn't droop under sustained full-bright animation

**Order of work:**

1. Bench: ribbon impedance sweep → gives us Fs + minimum safe
   crossover. Without that we'd pick a frequency by guess and
   probably either get cone-modulation distortion (too low) or
   waste the 5" upper range (too high).
2. New CDSP config `config/camilladsp/beatpimini.yml.tpl` — mono
   mixdown + 2-way crossover + ribbon protection chain
3. New profile YAML `profiles/beatpimini.yml` — references that
   CDSP config, conservative analog_gain_db, no sub
4. Firmware LED strip module — drive 66× SK6812 via FastLED RMT,
   bottom-to-top "VU bubble" coupled to the existing energy
   envelope
5. Box modelling (WinISD), enclosure CAD + build (off-repo)
6. Final crossover tune by ear once the box is built

Tracked here so future "what about a new speaker?" sessions have a
spec to argue against instead of inventing one from scratch.

### Settings panel — multi-page swipe (planned 2026-05-27)

Current state: swipe-down opens a single screen showing the QR code
for the web UI dashboard. The on-display PAIR button was removed in
favour of "scan → web UI → tap PAIR there".

**Problem:** the web UI is only reachable for users on the WLAN. A
guest who wants to pair their phone via BT shouldn't need WLAN
access — that exposes the rest of the household network. So we
need a way to trigger BT discoverable directly from the speaker,
without bringing back the cluttered single-button panel.

**Idea — swipeable settings carousel** inside the existing swipe-down
panel. Vertical swipe-down still opens; once open, horizontal swipes
flip between pages:

  ← [ QR — web UI ] · [ PAIR BLUETOOTH ] · [ NEXT SETTING ] →

First two pages cover the use cases we have now (web link + local
pair trigger). Page 3+ slot in over time without changing the entry/
exit gesture — candidates:

- Source switcher (Spotify / BT / Snapcast / Aux)
- Brightness preset (Day / Night / Auto)
- EQ preset (Flat / Bass+ / Vocal)
- "Forget all paired phones" panic button
- Rename speaker (push to BlueZ alias + mDNS)

**Open questions:**
- Page indicator UI: dot row at bottom, or label "1/N"?
- Swipe gesture interaction with the player screen's NEXT/PREV (which
  is also a horizontal swipe in the rotary zone) — different zones
  probably enough, but document the split
- Should the carousel auto-close after a page action, or stay open
  so the user can tweak multiple things?

## Session 2026-05-21 — UI redesign Phase 5 (weather) + Phase 1 (center-stage) groundwork

Implementation pass for the next big firmware patch — design locked
in chat 2026-05-21 (interactive mockup `beatbird-ui-preview.html`):

- ✅ **Phase 5 weather, end-to-end:**
  - Bridge: `src/beatbird/weather.py` — Open-Meteo poller, runs in a
    daemon thread with its own asyncio loop, pushes `WX:t=...|c=...|h=...|l=...`
    via `display.push_raw()`. Httpx-async preferred, requests-fallback
    if httpx isn't installed.
  - Profile schema: new `WeatherConfig` block (`enabled`, `latitude`,
    `longitude`, `interval_minutes` — defaults disabled).
  - Bridge `start()` spawns the poller when `weather.enabled: true`.
  - Display abstraction: new `push_raw()` method on `DisplayInterface`,
    implemented by `AmoledDisplay`, default no-op for others.
  - Firmware:
    - `State::Weather` struct + `WeatherIcon` enum (state.h, state.cpp)
    - `handle_weather_line()` in `serial_rx.cpp` parses `WX:` field-by-field
    - `screen_standby.h` / `.cpp` — full standby redesign: clock @ 130,
      dot-built weather icon @ 240, temp @ 320, H/L @ 358, condition @
      385, heartbeat dot @ 415. Six icons (clear / partly / cloudy /
      fog / rain / snow / thunder) built from dots + cloud helpers,
      matching the rest of the UI's dot vocabulary.
    - Graceful degrade: if `weather.valid == false` (no WX: ever
      received), standby renders only clock + heartbeat, same as before.
  - Docs: `WX:` line documented in `docs/protocol.md`.
- ✅ **Phase 1 (center-stage) — files landed but not wired yet:**
  - `firmware/.../screens/center_stage.h` / `.cpp`: single LVGL label
    centered on the player screen, evaluates a trigger priority chain
    every frame:
      1. PI OFFLINE (last_status_rx > 5 s, alert color)
      2. MUTE (volume == 0)
      3. PAUSE (state == PAUSED)
      4. WIFI WEAK (rssi < -85)
      5. Toast (1.2 s, NEXT/PREV feedback)
    Hidden when nothing active so title/artist breathe.
  - `Theme::Color::ACCENT_ALERT` compile-time constant added to theme.h.
  - **TODO** — actual `screen_player.cpp` refactor (remove
    `lbl_volume` + `state_icon`, resize title/artist, hook
    `CenterStage::create/update`) — separate patch, didn't land yet.
- ⏭ **Phase 2 (sound-print halo + vol-ring energy)**: **DONE 2026-05-22**.
      Halo was tried at r=225, found too noisy on the round 466 px panel
      and ripped back out. Energy modulation lives on the vol-ring lit
      dots + source-marker pulse (size + opacity), driven by a dynamic-
      range-remapped energy_smoothed so quiet vs. loud is visibly
      different. Plus full per-speaker palette protocol (PAL:a=…|p=…|s=…)
      and a wired-up ScreenStandby with weather, clock, conditions.
- ❌ **Phase 3 (source-select screen)**: **dropped** 2026-05-22 — only
      Spotify is actively in use, BT/Toslink/Snapcast either disabled or
      automatic, "last writer wins" in the bridge handles it. A
      full-screen picker is overengineering for the current setup.
- ⏭ **Phase 4 (split-flap animation)**: not started.
- ⏭ **BT pairing-mode trigger**: when Bluetooth gets re-enabled
      (currently parked due to GPIO3/I²C conflict), add a gesture or
      long-press that puts the speaker into discoverable mode for ~60 s
      and shows a `PAIRING…` CenterStage announcement with a countdown.
      Not a full source picker — just the one-shot "let my phone find
      this speaker" interaction. Out of scope until BT hardware works.

### Display redesign — landed across 2026-05-21..22

A coherent UI overhaul that freed up the centre of the screen for
larger, more legible content. Shipped in five phases (Phase 1 / 2 / 5
done as planned, Phase 3 dropped, Phase 4 done as planned).

- [x] ~~**Remove `lbl_volume`**~~ — gone, CenterStage shows `MUTE` when vol==0.
- [x] ~~**Centre status announcements**~~ — CenterStage with priority
      chain PI OFFLINE > MUTE > PAUSE > WIFI WEAK, plus 1.2 s toasts
      for SKIP > / < SKIP, fade-in/out transitions.
- [x] ~~**Title + artist larger**~~ — title at 44 px (font_clock), artist
      at 33 px (display_lg), source label at 22 px (display_md). Scroll
      speed pinned to 30 / 25 px/s via dynamic anim_time computation.
- [x] ~~**Energy visualisation into vol-ring**~~ — 12-dot smile widget
      removed. Lit vol-dots wobble ±33..78 % with energy_dyn(); source
      marker pulses opa+scale in phase. Dynamic-range remap stretches
      raw RMS 0.55..0.93 into a visible 0..1.2 modulation amplitude.
      Halo at r=225 was tried, found overloading on the round panel,
      ripped out.
- [x] ~~**Source management UI rework**~~ — **dropped** 2026-05-22. Only
      Spotify is active today; BT/Toslink/Snapcast are disabled or
      automatic. Last-writer-wins in the bridge is sufficient. A
      pairing-mode trigger is parked for if/when BT comes back online.

### Dot-vocabulary polish items — status

- [x] ~~**Mute glyph**~~ — CenterStage shows literal "MUTE" instead,
      same intent, no glyph asset needed.
- [⊘] ~~**Pulsing antenna at weak signal**~~ — obsolete. The WiFi dot
      antenna was removed entirely; CenterStage's "WIFI WEAK" trigger
      (RSSI < -85 dBm) covers the signal.
- [x] ~~**Bridge-disconnect / Pi-reboot indicator**~~ — done as
      CenterStage's "PI OFFLINE" trigger, threshold 12 s (= 2.4 × the
      bridge's 5 s STATUS_INTERVAL so subprocess hiccups don't flicker).
- [ ] **Source-change pulse** — last small polish item still open. On
      `Dirty::SOURCE`, scale `source_marker` to 1.5× for ~300 ms via
      `lv_obj_set_style_transform_scale`. Glyph-Phone "click" feedback
      for source switches. ~30 min job. Doesn't conflict with the
      continuous energy-driven marker pulse — just a one-shot overlay.
- [⊘] ~~**Boot antenna echo**~~ — obsolete. WiFi antenna removed; boot
      screen keeps its current "CONNECTING…" pulse.

### Display palette protocol — done 2026-05-22

The reserved palette slots (`accent_glow`, `accent_dim`, `text_primary`,
`text_secondary`, `accent_alert`) are now actually transmitted. Extended
`PAL:` format `PAL:a=…|g=…|d=…|p=…|s=…|e=…` is sent on every reconnect;
legacy `PAL:<hex>` single-accent form still accepted. Firmware tokens
are runtime `Theme::`-variables, screens consume them via Dirty::ACCENT
re-applies. Beat #1 boots with its forest+cream+linen+rust palette.

### Sound design ideas (whenever the mood strikes — not blocking anything)

- [ ] **Treble lift in loudness** (`air_lift` high-shelf @ ~8 kHz, +2-3 dB
      max_boost). Completes the Fletcher-Munson U-curve we currently only
      do on the bass side. ~30 min: add filter to `*.yml`, register in
      profile loudness chain, no code changes needed.
- [ ] **Tilt-EQ filter** with MQTT-switchable presets (warm / neutral /
      bright). One filter, gentle slope ±X dB per octave, patched via
      PatchConfig from a HA toggle. Lets you mood-shift without rebuilding
      the filter chain.
- [ ] **Adaptive compression at high volume** — soft compressor that
      kicks in above ~70 % volume to protect drivers and even out
      perceived loudness on dynamic tracks. CamillaDSP's `Compressor`
      filter, threshold linked to current volume via PatchConfig.
- [ ] **Subtle M/S stereo widening** on the treble band — adds perceived
      stage width to small speakers without making the center diffuse.
      Mid/side mixer + treble-only shelf on the sides.
- [ ] **Per-source EQ bias** — BT-A2DP often loses air; add +2 dB
      high-shelf only when `source=bluetooth`. Bridge would `PatchConfig`
      a single filter on source-change events. Spotify lossless stays
      flat.

### Open items for Beat #1 polish (next session)

- [ ] **Re-enable overlayroot on Beat #1** — currently `overlayroot=disabled`
      in `/boot/firmware/cmdline.txt` (backup at `cmdline.txt.preMigration`).
      Once polish is done, `sudo sed -i 's/overlayroot=disabled/overlayroot=tmpfs/'`
      + reboot. Verify migration survives the overlay re-engage.
- [ ] **Beat loudness review** — Steff reports bass boost is too aggressive.
      Profile currently has `bass_shelf max_boost_db: 3.0`, `sub_punch: 2.0`,
      `timpani_body: 1.5`. Walk through all three filters with him,
      decide new values, possibly also revisit CamillaDSP base gains.
- [⊘] ~~**WiFi status indicator on the display**~~ — deliberately
      dropped 2026-05-22. The dot-antenna widget existed briefly but
      duplicated CenterStage's WIFI WEAK / PI OFFLINE coverage; one
      channel is enough.
- [⊘] ~~**Bird brand graphic**~~ — dropped 2026-05-22. Not pursuing the
      Vögelchen aesthetic further.
- [ ] **Power button rewire** when housing is next opened — move button
      from GPIO3 → GPIO17 (or another free pin). Then flip
      `hardware.power_button.enabled: true` in `zipp-mini-2.yml` and
      `make update`. Code + firmware already shipped.
- [ ] **Display palette extension** — schema accepts 6 colors per profile
      (`accent_glow`, `accent_dim`, `text_primary`, `text_secondary`,
      `accent_alert`) but only `accent_color` is currently sent via the
      single-color `PAL:` protocol. Open work:
      (a) extend protocol to multi-color (e.g. `PAL:p=...|g=...|d=...|...`),
      (b) make firmware `TEXT_BODY` / `TEXT_DIM` / etc. runtime-mutable
      instead of compile-time `constexpr`, (c) bridge reads new fields and
      sends them. Beat #1 has its full forest palette already in `beat-1.yml`
      waiting for protocol support.
- [ ] **Snapcast server on TrueNAS** — in progress (Steff working on it
      2026-05-19). Once up, Beat #1's existing snapclient will auto-
      discover via mDNS; nothing to do on the speaker side beyond
      flipping `sources.snapcast.enabled: true` in `beat-1.yml` so the
      bridge knows about it and the new source-management UI can list
      it.
- [ ] **Spectrum reanimation** (optional) — `/etc/asound.conf` `dsnoop`
      alias so PortAudio and CamillaDSP can share the loopback capture.
      Then re-install `[fft]` extra + `libportaudio2`, set
      `spectrum_bands: 16`. Hours of work for marginal visual upgrade
      over LV: ring; only if motivated.
- [ ] REW measurement → custom DSP config
- [ ] Genre-EQ presets via PatchConfig
- [ ] Audio feedback sounds
- [ ] ESP32 main.cpp review (1168 lines)
- [ ] Snapcast multi-room
- [ ] MOSFET soft-start for Beat #1
- [ ] Lounge: UI board function test (LEDs + button)
- [ ] Lounge: `pigpio` service for LED dimming + button handler
