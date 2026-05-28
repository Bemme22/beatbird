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
