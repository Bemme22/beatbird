# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

BeatBird turns refurbished Libratone enclosures into active DSP speakers. A
Raspberry Pi runs the audio chain + a Python coordinator ("the bridge"); an
ESP32-S3 drives the AMOLED display over USB serial. One repo serves every
speaker — a speaker is fully described by a single profile YAML, and the
Makefile + numbered install scripts turn a fresh Pi OS image into that speaker.

There are **two independent codebases** here:

- **Pi-side Python** (`src/beatbird/`) — the bridge, webserver, source/DSP/HA
  adapters. Tested in CI, runs on the Pi under systemd.
- **ESP32 firmware** (`firmware/amoled-1.43/`) — C++ / PlatformIO / LVGL 9. Same
  UI source compiles for the device *and* a native SDL simulator.

## Profiles are the single source of truth

A speaker = one YAML in `profiles/`. `make profile PROFILE=<name>` creates the
symlink `profiles/current.yml -> <name>.yml`. **Everything** downstream resolves
from the active profile: which install scripts run, which CamillaDSP config is
picked, systemd unit content, MQTT topic roots, firmware build env. **No
speaker-specific constants live in code.** When adding a speaker-varying knob,
add it to the profile schema, not to a Python/shell constant.

- Schema is enforced by pydantic in `src/beatbird/config.py`; unknown fields
  raise a validation error. The authoritative field reference is
  `docs/profiles.md`.
- Firmware `[env:<name>]` blocks in `platformio.ini` mirror profile names — flash
  the env matching the speaker.

## Common commands

### Python (Pi-side)
```bash
python -m pytest tests/ -v                 # full suite (no install needed — see below)
python -m pytest tests/test_config.py -v   # one file
python -m pytest tests/test_loudness_curve.py -k curve   # one test by name
ruff check src/ tests/ --select E,F,W,B --ignore E501,E701,E741,B007,B027,B904,F841
```
`pyproject.toml` sets `pythonpath = ["src"]`, so tests run on a fresh clone with
no `pip install -e .`. Tests cover only the math + config layer (volume/loudness
curves, profile schema, serial escaping, power logic) — they never touch the
audio chain or hardware. Keep that boundary: anything needing ALSA/I²C/serial
hardware belongs on a real Pi, not in `tests/`.

### Deploy / operate (run on the Pi)
```bash
make profile PROFILE=beat-1   # activate a speaker (symlinks current.yml)
make secrets                  # write secrets/*.example templates to fill in
make install                  # full first-time install (runs install/*.sh in order)
make install-role ROLE=30-camilladsp.sh   # re-run one role for debugging
make update                   # git pull + re-render configs + restart services
make status / make logs       # systemd status / follow bridge journal
make dsp-reload               # reload CamillaDSP config without restart
make test-mode / make prod-mode   # toggle Restart=no for hardware diagnostics
make firmware-update          # OTA: flash latest fw-v* GitHub release via esptool
```
`make help` lists every target with its `##` description.

### Firmware (run on the dev box, not the Pi)
```bash
cd firmware/amoled-1.43
python3 fonts/build.py          # ONE-TIME per clone — generates gitignored font .c files
pio run -e beat-1 -t upload     # build + flash (uploads via SSH to the speaker's Pi)
pio run -e local-usb -t upload  # flash an ESP32 plugged into the dev machine
pio run -e sim && .pio/build/sim/program   # native LVGL+SDL2 simulator
```
The display simulator (`docs/SIMULATOR.md`) compiles the exact UI source plus a
Streamlit control panel that speaks the same wire protocol the bridge sends.
Prefer it for iterating on screens/animations/palettes — no flash cycle.

## Audio pipeline (the central design)

Every source writes the **same ALSA loopback** (`hw:Loopback,0`). CamillaDSP
reads the other end and streams to the physical DAC. Consequences worth
internalizing before touching audio code:

- **Sources never fight over hardware** (Spotify, Bluetooth, Snapcast, TOSLINK
  all share the loopback — no dmix).
- **One volume control**, and it lives in CamillaDSP. The bridge mirrors
  Spotify's/BT's notion of volume for UX responsiveness, but the only real
  attenuation happens in the DSP.
- **EQ applies to everything** regardless of source.
- CamillaDSP 4.x is required: runtime `PatchConfig` over websocket powers
  loudness compensation without restarts; state file persists volume across
  reboots.

## The bridge is a coordinator

`src/beatbird/bridge.py` orchestrates; all real logic is delegated to modules
(`audio/`, `sources/`, `display/`, `hardware/`, `ha/`). It runs as a systemd
`Type=notify` unit (`python -m beatbird.bridge`), polls sources, pushes a
`DisplayState` snapshot to the ESP32 (~200 ms playing / 2 s idle), and publishes
system health to MQTT every 5 s.

Failure philosophy: **the bridge never self-restarts** — systemd does
(`Restart=always`). Every module-level exception is caught and logged; the main
loop keeps running. A dead dependency degrades gracefully (source shows "none",
volume set/get silently no-ops) rather than crashing the coordinator. Preserve
this when adding integrations.

Other Pi-side entry points (`pyproject.toml [project.scripts]`):
`beatbird-web` (FastAPI dashboard, port 8080), `beatbird-firmware-update`.

Logging is set up in `beatbird.logging_setup` (kept stdlib-only so it's
testable without importing the bridge's heavy deps). stdout → journald by
default; `BEATBIRD_LOG_FILE` adds an opt-in size-rotated file that degrades to
stdout-only if the path isn't writable. Level via `BEATBIRD_LOGLEVEL`.

## Install scripts

`install/[0-9]*.sh` are **numbered, idempotent, single-role** — `make install`
runs them in sorted order; safe to re-run any one. Conventions:

- Every script sources `install/_lib.sh` and is invoked as root by the Makefile
  with `REPO_DIR` and `PROFILE_YML` set.
- Read profile values via the `pq` / `pq_bool` / `pq_or` helpers (dotted-key
  Python YAML lookups — no `yq` dependency).
- Generate `/etc/...` files with `render_template SRC DST KEY=VAL ...` (simple
  `{{ key }}` substitution from `config/` template sources).
- Use `ensure_pkg`, `ensure_line`, `ensure_module_loaded`, `enable_service`
  instead of raw apt/edits so re-runs stay idempotent. Note the Trixie
  compatibility shims (`/etc/modules` alongside `dtoverlay`).

## Pi ↔ ESP32 serial protocol

Line-oriented ASCII over USB CDC at 115200 baud, `\n`-terminated, no framing or
checksums — both sides tolerate dropped/corrupt/unknown lines. Full grammar in
`docs/protocol.md`. Evolution rule: **unknown tokens/lines are ignored**, so you
can add fields/commands without breaking older firmware; for a breaking change
introduce a new leading verb (`ST2:…`). Standby split-flap text is ASCII-only
and ≤ ~17 chars (the display animates byte-by-byte and clips on the round edge).

## Secrets

Nothing sensitive in git. `make secrets` writes gitignored templates under
`secrets/`; install lands them at `/etc/beatbird/{wifi.pass, mqtt.pass, env}`.
The bridge service reads `/etc/beatbird/env` via `EnvironmentFile=` and never
sees credentials in its YAML profile.

## CI

- `.github/workflows/python.yml` — ruff + pytest on changes to
  `src/`, `tests/`, `profiles/`, `pyproject.toml`. Fast math/config gate only.
- `.github/workflows/firmware.yml` — builds the AMOLED firmware for each
  physical speaker env on push/PR; **pushing a `fw-v*` tag** also attaches the
  `.bin`s to a GitHub Release, which is what `make firmware-update` pulls.
  Releases are tag-gated on purpose — not every main push ships to devices.

## Project documentation (Obsidian)

Long-form project memory lives in Steffen's Obsidian vault (synced via
Syncthing, not reachable from this repo):

- Hub note: `projekte/beatbird.md` — status, decision log, open items
- Session notes land in `00-inbox/` as dated notes, linked to the hub

Conventions for Claude Code:
- This CLAUDE.md covers *how to work in the repo*; the vault covers *why
  decisions were made*. Don't duplicate.
- **At the end of a working session, offer a paste-ready German Obsidian inbox
  note** (a fenced ` ```markdown ` block) Steffen drops into `00-inbox/`. Fixed
  format:
  - YAML frontmatter: `date: YYYY-MM-DD`, `tags: [beatbird, <topic>, session]`
  - `# YYYY-MM-DD — <kurzer Titel>` heading
  - `Hub: [[projekte/beatbird]]` link
  - sections, in order: **Erledigt** · **Entscheidungen (warum)** ·
    **Erkenntnisse (durable)** · **Offen**
  - Capture the *why* behind decisions and durable gotchas, not just what
    changed. Keep it tight — bullets, no prose walls.
- If repo reality and this file drift apart, flag it — Steffen updates both.
