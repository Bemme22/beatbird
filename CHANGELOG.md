# Changelog

## v2.1.0 — 2026-04-22

Production hardening release. First multi-speaker support (Zipp Mini 2).

### P0 Fixes (critical)
- **Log level** set to INFO by default; DEBUG only via `BEATBIRD_LOGLEVEL=DEBUG`
  environment variable. Reduces journal writes from ~430k/day to ~5k/day.
- **SIGTERM handler** added — clean shutdown on `systemctl stop`, immediate
  MQTT offline publish, proper serial port close.

### P1 Fixes (important)
- **Persistent WebSocket** to CamillaDSP — single long-lived connection
  instead of 15 connect/disconnect cycles per second. Thread-safe with
  auto-reconnect.
- **Spotify session close** via `POST /player/close` (go-librespot v0.8+)
  instead of `systemctl restart go-librespot`. Speaker stays visible in
  Spotify Connect during source handoff.
- **Volume curve from profile** — `audio.volume.min_db` and `max_db` are
  now per-speaker settings in the profile YAML, not hardcoded constants.
- **Loudness base gains from CamillaDSP** — the bridge reads filter
  parameters from the running CamillaDSP config at startup instead of
  duplicating them in Python constants. REW re-tuning no longer requires
  a bridge code change.

### New: Bluetooth A2DP source
- Bidirectional volume sync (phone slider ↔ CamillaDSP ↔ AMOLED arc)
- AVRCP playback control from display (play/pause/next/prev)
- Hard source handoff: BT activation kills Spotify session, Spotify
  activation disconnects BT devices. No audio overlap.
- Volume echo guard prevents sync ping-pong.
- Uses BlueALSA Manager1.GetPCMs API for reliable PCM discovery.

### New: Innomaker AMP Pro Mini support
- Full installer script for MA12070P-based boards.
- CamillaDSP 2-way crossover config for Zipp Mini 2 (3.5 kHz L-R 4th order).
- `plughw:` playback device for format/rate conversion from BT streams.

### Architecture
- Explicit `Source` and `Playback` enums replace string-based state tracking.
- `_transition_source()` method centralises all handoff logic.
- Profile-driven configuration via pydantic models with validation.

### Profiles
- Added `audio.volume` block with `min_db` / `max_db` per speaker.
- All 6 speaker profiles updated.

## v2.0.0 — 2026-04-21

Initial multi-speaker scaffold. Repo restructure from `beatbird-display`.
