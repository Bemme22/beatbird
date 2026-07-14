# Profile schema

Every speaker is described by a single YAML file under `profiles/`. The
active profile is whatever `profiles/current.yml` points at (created by
`make profile PROFILE=<name>`).

This document is the authoritative reference for every field. The
implementation lives in `src/beatbird/config.py` as pydantic models; any
unknown field raises a validation error.

## Top-level

```yaml
identity:   …    # who am I
platform:   …    # Pi model
soundcard:  …    # which amp board
audio:      …    # CamillaDSP + loudness
display:    …    # UI hardware
wifi:       …    # network
mqtt:       …    # HA integration
sources:    …    # audio inputs
web:        …    # FastAPI dashboard
```

## `identity`

```yaml
identity:
  hostname: beat-1            # Linux hostname (becomes <hostname>.local via mDNS)
  friendly_name: "Beat #1"    # shown in Spotify Connect, HA device page
  speaker_id: beatpi_speaker  # slug used as MQTT unique_id prefix
```

Change `speaker_id` only if you want a clean slate in HA — the old entities
will stick around until manually removed.

## `platform`

One of: `pi-zero-2w`, `pi-3b-plus`, `pi-4`, `pi-5`. Drives kernel-overlay
choices and the default `--system-site-packages` venv setup.

## `soundcard`

`driver` picks the install script under `install/10-soundcard/`. Valid
values:

| Value                 | Hardware                             | Status      |
|-----------------------|--------------------------------------|-------------|
| `louder-hat-plus-2x`  | Sonocotta Louder Hat Plus 2X         | implemented |
| `louder-hat-plus-1x`  | Sonocotta Louder Hat Plus 1X         | implemented |
| `louder-hat-triple`   | 2× Plus + 1× non-Plus (Lounge)       | TODO        |
| `innomaker-amp-pro`   | Innomaker AMP Pro Mini (MA12070P)    | stub        |

Common TAS5825M options (ignored for non-TAS drivers):

```yaml
primary_i2c:       0x4c     # stereo amp address
secondary_i2c:     0x4d     # sub amp (Plus 2X only)
sub_enabled:       true
sub_crossover_hz:  150      # TAS internal DSP (not CamillaDSP)
sub_digital_volume: 110     # 0..127 — stay ≤110 at 24V PVDD
analog_gain_db:    -3       # safety margin at boot
pbtl:              false    # Plus 1X only: bridge mono (OUT_A||OUT_B) for one
                            # high-power driver. Also close the SJ5+SJ6 solder
                            # bridges on the board — the flag only sets the
                            # chip's modulation (bridge_mode=1 + mixer_mode=1,
                            # in-chip L+R→mono). Leave false for stereo.
```

## `audio`

```yaml
audio:
  camilladsp_config: beat       # picks config/camilladsp/<name>.yml
  sample_rate: 48000            # TAS5825M only accepts 48000
  format: S32LE
  loudness:
    enabled: true
    filters:
      - {name: bass_shelf,   max_boost_db: 3.0}
      - {name: sub_punch,    max_boost_db: 2.0}
      - {name: timpani_body, max_boost_db: 1.5}
```

`loudness.filters[].name` must match a filter defined in the chosen
CamillaDSP config. The bridge boosts that filter's gain by up to
`max_boost_db` at the lowest volumes, scaling smoothly to 0 at 80% volume
and above.

## `display`

```yaml
display:
  type: amoled              # amoled | led-button | none
  variant: waveshare-1.43
  serial_device: auto       # "auto" uses udev VID match, else /dev/path
  spectrum_bands: 16        # must match firmware build

# For led-button profiles:
  led_pin: 18
  led_count: 12
  button_pin: 17
  led_brightness: 128
```

## `wifi`

```yaml
wifi:
  ssid: "your-ssid"
  country: DE                     # 2-letter ISO code
  use_usb_dongle: true            # onboard radio is Faraday-caged inside metal
  disable_onboard_radio: true     # adds dtoverlay=disable-wifi
  disable_bluetooth: true         # adds dtoverlay=disable-bt
```

The PSK is NOT in the profile — it lives in `/etc/beatbird/wifi.pass`.

## `mqtt`

```yaml
mqtt:
  enabled: true
  host: 192.168.1.10
  port: 1883
  user: beatbird
  discovery_prefix: homeassistant      # HA's MQTT discovery prefix
  base_topic: beatbird/beat-1          # BeatBird publishes under this
```

Password lives in `/etc/beatbird/mqtt.pass`.

## `sources`

```yaml
sources:
  spotify:
    enabled: true
    device_name: "Beat #1"
    bitrate: 320                 # 96 | 160 | 320
    normalisation: true
  bluetooth:
    enabled: false               # requires onboard BT (not compatible with disable_bluetooth=true)
    a2dp: true
  toslink:
    enabled: false
    device: "hw:1,0"             # ALSA device for USB SPDIF interface
  snapcast:
    enabled: false
    server: 192.168.1.10
    latency_ms: 30
```

## `web`

```yaml
web:
  enabled: true
  port: 8080
```

Dashboard at `http://<hostname>.local:8080`.

## `idle`

Standby-screen behaviour and the idle-timeout that drops the player screen to
the clock-standby screen. All optional.

```yaml
idle:
  standby_timeout_s: 60.0          # PAUSED with a track loaded → standby
  standby_timeout_stopped_s: 30.0  # track loaded but playback STOPPED
  standby_timeout_idle_s: 10.0     # no source / nothing meaningful on screen
  close_session_on_standby: false  # tear down Spotify Connect on standby?
  idle_message_interval_s: 45.0    # standby flap-text rotation cadence
  rss_url: ""                      # optional Atom/RSS feed for standby headlines
  rss_refresh_minutes: 30
  rss_weight: 0.5                  # 0=always local list, 1=always RSS
  max_chars: 50                    # per-headline cap (flap label width)
```

- The idle timeout is **content-adaptive**: `standby_timeout_idle_s` (empty
  player), `standby_timeout_stopped_s` (stopped), else `standby_timeout_s`
  (paused with a track). The idle clock is reset by *any* playback observation
  (Spotify/BT/Snapcast), so active playback never trips standby.
- `close_session_on_standby` is **false by default** — standby only affects the
  display/LEDs/SFX and the Spotify Connect session stays alive, so the speaker
  remains in the device list and resumes instantly. Set `true` only if you want
  the Connect slot freed while idle (e.g. so no other device can silently grab
  the speaker at night).
