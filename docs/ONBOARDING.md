# Onboarding — From bare Pi to playing speaker

This walkthrough takes a fresh Raspberry Pi (Zero 2W or 4) through to a
working BeatBird speaker. Total time on a stable internet connection
~45 minutes, most of it `apt-get`.

The repo lives on the Pi at `/home/<user>/beatbird` (the Makefile derives
`REPO_DIR` from its own location, so you can put it anywhere).

---

## 0 · Hardware checklist

What you should have wired up before running anything:

- Raspberry Pi (tested: Zero 2W with Bookworm, Pi 4 with Trixie)
- Sound card (one of: Sonocotta Louder Hat Plus 1X / Plus 2X, Innomaker
  AMP Pro Mini Hat; ALSA Loopback fallback for headphone-jack output)
- Optional: Waveshare ESP32-S3-Touch-AMOLED-1.43 wired via USB-C to a
  Pi USB port. Flash firmware separately (see [Display firmware](#display-firmware)).
- USB WiFi dongle if the Pi is inside a metal enclosure
- SD card with a fresh Raspberry Pi OS Lite image (Bookworm 64-bit or
  Trixie 64-bit). Use `rpi-imager`, enable SSH, set username
  (we use `devusr` in the install scripts but anything works).

---

## 1 · First boot

```sh
ssh devusr@<pi-ip>            # whatever rpi-imager left you with
sudo apt-get update
sudo apt-get install -y git make
git clone https://github.com/Bemme22/beatbird.git
cd beatbird
```

If you're on a flaky WiFi and the clone is dropping, do it via a USB
ethernet adapter or local copy from another machine, then continue.

---

## 2 · Pick a speaker profile

Each speaker has a profile YAML in `profiles/`:

| Profile           | Hardware                                              |
|-------------------|-------------------------------------------------------|
| `beat-1.yml`      | Libratone Beat #1 — Louder Hat Plus 2X, AMOLED 1.43"  |
| `beat-2.yml`      | Same as Beat #1 (template for second unit)            |
| `zipp-mini-2.yml` | Libratone Zipp Mini 2 — Louder Hat Plus 1X, AMOLED    |
| `zipp-2.yml`      | Libratone Zipp 2 — Louder Hat Plus 2X (template)      |
| `lounge.yml`      | Libratone Lounge — LED ring + button, no display      |
| `_template.yml`   | Bare template — copy + edit for new hardware          |

Activate the one matching your build:

```sh
make profile PROFILE=beat-1     # adjust to taste
```

This creates a `profiles/current.yml` symlink. Every install/* script
reads from there.

---

## 3 · Fill in the secrets

Sensitive values (WiFi password, MQTT broker password, weather
coordinates, snapserver host) live in `secrets/` which is git-ignored.
The Makefile seeds templates you then edit:

```sh
make secrets
```

Then `vim secrets/*` and fill in:

- `wifi.pass` — single line, your WiFi PSK
- `mqtt.pass` — single line, your MQTT broker password (skip if not using MQTT)
- `location.coords` — single line, `lat,lon` decimal degrees (e.g. `52.5200,13.4050`).
  Skip / leave default if you don't want the weather block on the standby screen.
- `snapcast.host` — mDNS name or IP of your Snapserver (e.g. `truenas.local`
  if you run MA's snapserver there). Names survive DHCP changes; pinning
  to an IP only invites future "speaker stopped working" debug sessions.
- `static-ip.conf` *(optional)* — for the second-line-of-defence
  reservation:
  ```
  IPV4=192.168.178.113/24
  GATEWAY=192.168.178.1
  DNS=192.168.178.1 1.1.1.1
  ```
  Without this file, the Pi takes whatever DHCP gives it and relies on
  the Fritzbox-side IP pin alone.

Edit the profile's `wifi.ssid` to your network name (the only
profile field you usually need to touch for a fresh deploy).

---

## 4 · Full install

```sh
make install
```

This walks the `install/*.sh` scripts in order:

| Script                            | What it does                                         |
|-----------------------------------|------------------------------------------------------|
| `00-base.sh`                      | apt packages, `/etc/beatbird/env`, `/var/lib/beatbird/` |
| `10-soundcard/<driver>.sh`        | I²C amp init, ALSA Loopback, level setup             |
| `20-wifi.sh`                      | WiFi credentials, NetworkManager/wpa_supplicant      |
| `25-static-ip.sh`                 | (optional) static IP reservation                     |
| `30-camilladsp.sh`                | CamillaDSP binary + config + service                 |
| `40-go-librespot.sh`              | Spotify Connect daemon                               |
| `45-power-button.sh`              | (optional) Long-press shutdown button                |
| `50-snapcast.sh`                  | Snapclient for multi-room                            |
| `55-web-sudo.sh`                  | NOPASSWD rules so the web UI can restart services   |
| `60-bluetooth.sh`                 | (optional) BlueALSA stack                            |
| `70-bridge.sh`                    | Python venv, bridge service, mqtt entitlement       |
| `75-display.sh`                   | udev rule for `/dev/beatbird-display`               |
| `90-finalize.sh`                  | Enable everything, summary                           |

Every script is idempotent; you can re-run individual ones via:

```sh
make install-role ROLE=30-camilladsp.sh
```

---

## 5 · Display firmware (separate)

If the speaker has an AMOLED display, flash the ESP32-S3 firmware
**from a development machine**, not the Pi:

```sh
cd firmware/amoled-1.43
pio run -e <speaker> -t upload
```

The upload script `scripts/upload_via_pi.py` SSHes into the Pi (using
the env's `custom_pi_host`), stops the bridge, scp's the binary, and
uses esptool to flash. The Pi acts as the USB host for the ESP32.

Per-env `custom_pi_host` is in `platformio.ini` — edit if your SSH
hostname differs from `BeatPiSpeaker` / `Zipp2miniPi`.

---

## 6 · Verify

```sh
make status                  # systemd status of all units
curl -s http://localhost:8080/api/health | jq    # diagnostic snapshot
```

Open `http://<host>.local:8080/` in a browser — the dashboard shows
current playback, lets you adjust volume / loudness, restart services,
and reboot the Pi.

`/health` on the same port shows the network + service diagnostics page.

---

## 7 · Day-to-day

```sh
make update                  # git pull + re-render + restart
make logs                    # tail bridge log
make dsp-reload              # reload camilladsp config without service restart
```

For a Pi running with `overlayroot=tmpfs` active (recommended for
production — wear-protects the SD), durable changes need a chroot:

```sh
sudo overlayroot-chroot
cd ~/beatbird
git pull && make update
exit
sudo reboot
```

---

## Common stumbles

- **`make update` fails with merge conflict on `profiles/beat-1.yml`** —
  you have local hot-fixes on the Pi that overlap with an upstream
  change. `git checkout -- profiles/beat-1.yml` to discard and try
  again, or `git stash` to keep them.
- **Web UI shows "active" but Spotify app can't see the speaker** —
  avahi/mDNS issue. `sudo systemctl restart avahi-daemon go-librespot`
  on the Pi.
- **Speaker not in Fritzbox network list / new IP every reboot** — the
  Fritzbox pin sometimes silently drops. Always pin AND use
  `secrets/static-ip.conf` for the second-line-of-defence. mDNS names
  (`<hostname>.local`) survive either way.
- **Display dark after firmware update** — bridge needs ~5 s after
  flash for serial reconnect. If still dark after a minute, check
  `/dev/beatbird-display` symlink exists and the udev rule is active.
- **Volume jumps to 25 % on every reboot** — should only happen on the
  very first boot now (we persist last-known-good in
  `/var/lib/beatbird/state.json`). If it keeps happening, check that
  the bridge service has `ReadWritePaths=/var/lib/beatbird`.

---

## Health page glossary

`http://<host>:8080/health` shows these probes — what they mean:

| Row             | Green if…                                            |
|-----------------|------------------------------------------------------|
| IP              | `hostname -I` returned something                     |
| SSID            | iwgetid found a connected network                    |
| RSSI            | Worse than -85 dBm is red, between -85 and -67 amber |
| Gateway         | ICMP ping to default route succeeded                 |
| Internet        | ICMP ping to 1.1.1.1 succeeded                       |
| mDNS / avahi    | avahi-daemon systemd unit is active                  |
| Spotify API     | HTTPS HEAD to api.spotify.com got any status code    |
| Spotify AP      | TCP handshake on ap-gew4.spotify.com:4070            |
| Snapserver      | TCP handshake on `<configured-snap-host>:1705`       |
| Services        | systemctl is-active per beatbird unit                |

The "recent warnings" panel tails `journalctl -u beatbird-bridge -p warning`
for fast triage when something looks off.
