# BeatBird — Project Status

> Last updated: 2026-05-19

## Active speakers

| Speaker | Repo | Status | OS |
|---|---|---|---|
| Beat #1 | beatbird-display (old) | ✅ Production | Bookworm |
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
- [ ] **Beat #1 migration** off legacy `beatbird-display` → current
      `beatbird` repo. CamillaDSP-config already synced; Plus 2X amixer
      script ready. Open: bridge service deploy, go-librespot 0.7.1
      with new buffer config, firmware flash check.
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
