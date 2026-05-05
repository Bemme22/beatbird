# Installation

## 0. Prerequisites

- Raspberry Pi running **Pi OS Bookworm (Lite) 64-bit** with SSH enabled
- Speaker enclosure with chosen soundcard already wired in
- (For AMOLED profiles) ESP32 display attached via USB — can be flashed later
- Home Assistant MQTT broker reachable on the LAN (optional but recommended)

## 1. Initial login

Flash Pi OS with the Raspberry Pi Imager, setting hostname, SSH keys, WiFi,
and user in the imager's advanced options. Wait for the Pi to boot and SSH in.

## 2. Bootstrap

From the Pi shell:

```bash
curl -fsSL https://raw.githubusercontent.com/Bemme22/beatbird/main/install.sh -o bootstrap.sh
bash bootstrap.sh beat-1
```

(Replace `beat-1` with `beat-2`, `zipp-mini-2`, `zipp-2`, `zipp`, or `lounge`.)

The bootstrap script:

1. Installs `git make python3-yaml`
2. Clones the repo to `~/beatbird`
3. Activates the profile (`make profile PROFILE=beat-1`)
4. Creates secret templates at `~/beatbird/secrets/`
5. Exits so you can fill in the secrets

## 3. Fill in secrets

```bash
cd ~/beatbird
nano secrets/wifi.pass       # your WiFi PSK, one line
nano secrets/mqtt.pass       # your MQTT password, one line
```

## 4. Edit the profile

The profile has sane defaults from memory, but some things are
installation-specific:

```bash
nano profiles/beat-1.yml     # at least: wifi.ssid, mqtt.host, mqtt.user
```

Everything else has reasonable defaults that you can tune later without
reinstalling.

## 5. Full install

```bash
make install
```

This runs, in order:

| Step | Role                            | What it does                                     |
|------|---------------------------------|--------------------------------------------------|
| 00   | `00-base.sh`                    | apt deps, user groups, hostname, `/etc/beatbird/`|
| 10   | `10-soundcard.sh`               | Dispatches to driver-specific script             |
| 20   | `20-wifi.sh`                    | WPA / NM config, powersave off, keepalive        |
| 30   | `30-camilladsp.sh`              | Download CamillaDSP, render config, systemd unit |
| 40   | `40-go-librespot.sh`            | Download, render config, systemd unit            |
| 50   | `50-snapcast.sh`                | Only if `sources.snapcast.enabled`               |
| 60   | `60-bluetooth.sh`               | Only if `sources.bluetooth.enabled`              |
| 70   | `70-bridge.sh`                  | Python venv + package + bridge & web services    |
| 75   | `75-display.sh`                 | udev for AMOLED, GPIO for LED+button             |
| 90   | `90-finalize.sh`                | Summary + reboot prompt                          |

Each step is idempotent and can be re-run in isolation:

```bash
make install-role ROLE=30-camilladsp.sh
```

## 6. Reboot

```bash
sudo reboot
```

The first boot after install is slow — the soundcard overlay, mDNS, WiFi
all need to settle. After ~30 s:

```bash
make status
```

Expect `active (running)` for `beatbird-bridge`, `camilladsp`,
`go-librespot`, `louder-hat-init`.

## 7. Flash the ESP32 (AMOLED profiles only)

```bash
cd ~/beatbird/firmware/amoled-1.43
pio run -t upload
pio device monitor          # Ctrl-C to exit
```

The firmware auto-connects to the bridge — no configuration on the ESP32 side.

## 8. Day-to-day

```bash
make update            # git pull + re-render configs + restart services
make logs              # follow bridge log
make status            # systemd status
make dsp-reload        # reload CamillaDSP YAML without restart
make amixer-apply      # re-apply amp levels (e.g. after driver reload)
```

## Troubleshooting

**No sound at all:**
```bash
aplay -l                                    # is LouderRaspberry listed?
systemctl status louder-hat-init            # did amixer init run?
camilladsp --version                        # is CDSP installed?
sudo journalctl -u camilladsp -n 50         # is it unhappy?
```

**ESP32 display stays on boot screen:**
- Unplug / replug USB
- `ls -l /dev/beatbird-display` — udev symlink present?
- `sudo journalctl -u beatbird-bridge -n 50 -f` — bridge should log
  `"connected to /dev/..."`

**MQTT / HA not seeing the device:**
- `systemctl status beatbird-bridge` — check the MQTT connect log line
- On HA: `mosquitto_sub -t 'homeassistant/#' -v` — discovery messages visible?

**Spotify Connect not appearing:**
- `dpkg -l | grep raspotify` — must be empty (collides with go-librespot)
- `sudo systemctl status go-librespot`
- Same Zeroconf/WiFi subnet as your phone?
