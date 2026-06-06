# BeatBird

DIY active DSP speakers based on refurbished Libratone enclosures.
One repo, one installer, one profile per speaker.

## Supported speakers

| Profile         | Enclosure           | Compute       | Soundcard                | Display      |
|-----------------|---------------------|---------------|--------------------------|--------------|
| `beat-1`        | Libratone Beat #1   | Pi Zero 2W    | Louder Hat Plus 2X       | AMOLED 1.43" |
| `beat-2`        | Libratone Beat #2   | Pi Zero 2W    | Louder Hat Plus 1X       | AMOLED 1.43" |
| `zipp-mini-2`   | Zipp Mini 2 / LTH200| Pi Zero 2W    | Louder Hat Plus 1X       | AMOLED 1.43" |
| `zipp-2`        | Zipp 2   / LTH300   | Pi Zero 2W    | Louder Hat Plus 1X       | AMOLED 1.43" |
| `zipp`          | Zipp      / LT300   | Pi Zero 2W    | Louder Hat Plus 1X       | LED + Button |
| `lounge`        | Libratone Lounge    | Pi 5 (1 GB)   | 2× Plus 2X + 1× non-Plus | LED + Button |

## Quick start (fresh Pi OS Bookworm Lite)

```bash
git clone https://github.com/Bemme22/beatbird.git
cd beatbird

# 1. Pick a profile (creates profiles/current.yml as a symlink)
make profile PROFILE=beat-1

# 2. Fill in secrets (WiFi PSK, MQTT password). Templates live in secrets/
make secrets

# 3. Install everything
make install

# 4. Verify
make status
```

## Everyday commands

```bash
make update            # git pull + re-render configs + restart services
make status            # systemd status for all beatbird services
make logs              # follow bridge log
make dsp-reload        # reload CamillaDSP config without restart
make amixer-apply      # re-apply amp levels (after reboot or driver reload)
```

## Build & deploy

The production speakers (Beat, Zipp) run `overlayroot="tmpfs"`: `/`, `/etc`,
`/var/lib` and `/home` are a tmpfs overlay, so **anything written live reverts on
reboot** unless it's also written to the read-only base via `overlayroot-chroot`.
That split drives the two deploy paths below.

### Pi-side code (bridge / web / templates) — the 95 % case

Push to `main`, then from the dev box:

```bash
make deploy HOST=beatpi.fritz.box            # → ssh <host> sudo beatbird-update main
sudo beatbird-update [BRANCH]                # or run on the speaker; default BRANCH=main
```

`beatbird-update` is overlayroot-aware: it fast-forwards the **live** repo *and*
persists the **base** layer through `overlayroot-chroot` (survives reboot), then
restarts `beatbird-bridge` + `beatbird-web`. The bridge venv is an editable
install, so the restart picks the new code up. No-op chroot on a plain rw root.

### Config-affecting changes (CamillaDSP / go-librespot / new helpers + sudoers)

`beatbird-update` moves **code only** — it does *not* re-render `/etc` configs or
install new `install/` helpers. On overlayroot speakers run the affected role(s)
**inside the chroot** (so they land in the base), then reboot to activate:

```bash
ssh <host> 'sudo overlayroot-chroot bash -c "
  cd /home/devusr/beatbird &&
  REPO_DIR=/home/devusr/beatbird \
  PROFILE_YML=/home/devusr/beatbird/profiles/current.yml \
  bash install/30-camilladsp.sh"'          # e.g. a new DSP config; 55-web-sudo.sh for helpers
ssh <host> sudo systemctl reboot            # activates the base /etc + /usr/local writes
```

On a plain rw root (LoungePi) just run `make update` — no chroot needed.

### ESP32 firmware (run on the dev box, flashes over SSH to the speaker's Pi)

```bash
cd firmware/amoled-1.43
python3 fonts/build.py                       # ONE-TIME per clone — generates gitignored font .c
pip install "click<8.2"                      # esptool 5.x crashes on click >= 8.2
pio run -e beat-1 -t upload                  # build + flash (env == profile/speaker)
pio run -e sim                               # native LVGL + SDL2 simulator (needs SDL2; Linux)
```

`make firmware-update` instead pulls the latest tagged `fw-v*` GitHub release and
OTA-flashes it (tag-gated — not every `main` push ships to devices).

## Repo layout

```
beatbird/
├── profiles/          # one YAML per speaker — single source of truth
├── install/           # numbered, idempotent install scripts (one role per file)
├── config/            # template sources (rendered into /etc/…)
├── src/beatbird/      # Python package (bridge, webserver, DSP/source adapters)
├── firmware/          # ESP32 firmware projects (PlatformIO)
├── tools/             # developer utilities (simulator, stubs)
├── docs/              # architecture, protocol, profile schema
└── Makefile           # orchestrator
```

## Secrets

Nothing sensitive lives in the repo. Credentials go into:

- `/etc/beatbird/wifi.pass`  — WiFi PSK (mode 0600, root:root)
- `/etc/beatbird/mqtt.pass`  — MQTT password (mode 0640, owned by bridge user)
- `/etc/beatbird/env`        — shell-style env for systemd (derived from both)

`make secrets` creates templates at `secrets/*.example` for you to fill in
locally — those files are in `.gitignore`.

## Docs

- [`docs/architecture.md`](docs/architecture.md) — audio pipeline, sources, bridge modules
- [`docs/profiles.md`](docs/profiles.md) — profile schema reference
- [`docs/installation.md`](docs/installation.md) — end-to-end first-time install
- [`docs/protocol.md`](docs/protocol.md) — ESP32 ↔ Pi serial protocol
