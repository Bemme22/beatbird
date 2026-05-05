# BeatBird — Architecture

## System overview

```
      ┌──────────────┐      USB serial      ┌──────────────────────┐
      │ ESP32 AMOLED │◄────────────────────►│                      │
      │  (or LED/    │                      │                      │
      │   button)    │                      │                      │
      └──────────────┘                      │                      │
                                            │   Raspberry Pi       │
      ┌──────────────┐     I²S + I²C        │   (Zero 2W / Pi 4)   │
      │ Louder Hat / │◄────────────────────►│                      │
      │  Innomaker   │                      │                      │
      └──────────────┘                      └─────────┬────────────┘
                                                      │
            ┌─────────────────────────────────────────┼─────────────────┐
            │                                         │                 │
            ▼                                         ▼                 ▼
     ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   ┌──────────────┐
     │ go-librespot │    │ bluealsa /   │    │ USB SPDIF /  │   │ Snapclient   │
     │   (Spotify)  │    │   A2DP       │    │   TOSLINK    │   │ (multi-room) │
     └──────┬───────┘    └──────┬───────┘    └──────┬───────┘   └──────┬───────┘
            │                   │                   │                   │
            └───────── ALSA Loopback hw:Loopback,0 ─┴─────────┬─────────┘
                                                              │
                                                              ▼
                                            ┌─────────────────────────────┐
                                            │  CamillaDSP                 │
                                            │   - Room/driver EQ          │
                                            │   - Loudness compensation   │
                                            │   - Volume control          │
                                            │   - Subsonic HP             │
                                            └──────────────┬──────────────┘
                                                           │
                                                    hw:LouderRaspberry,0
                                                           │
                                                           ▼
                                                   ┌───────────────┐
                                                   │  TAS5825M amp │
                                                   │  → drivers    │
                                                   └───────────────┘
```

## Audio pipeline

Every source writes to the same ALSA loopback device (`hw:Loopback,0`).
CamillaDSP reads the other end (`hw:Loopback,1`) and streams processed audio
to the physical DAC. This means:

1. **Sources don't fight over the hardware.** Spotify, Bluetooth, Snapcast —
   all of them share the loopback without needing dmix tricks.
2. **Volume is a single concept.** There's exactly one volume control, and
   it lives in CamillaDSP. The bridge keeps Spotify's notion of volume in
   sync so the Spotify app still "feels" responsive, but under the hood the
   only attenuation happens in the DSP.
3. **EQ runs on everything.** Whatever the source, the room/driver
   correction filters are applied identically.

### Why CamillaDSP 4.x

- `PatchConfig` over websocket lets the bridge change filter parameters at
  runtime (loudness compensation) without restarting anything.
- State file persistence means volume survives reboots / service restarts.
- Linkwitz-Riley combos for crossovers that actually behave.

## Bridge internals

The Python bridge (`src/beatbird/bridge.py`) is a coordinator, nothing more.
All non-trivial logic is delegated:

| Module                        | Responsibility                                      |
|-------------------------------|-----------------------------------------------------|
| `beatbird.config`             | Load & validate profile YAML                        |
| `beatbird.audio.camilladsp`   | Websocket wrapper: volume, signal level, patching   |
| `beatbird.audio.loudness`     | Curve computation + filter patching                 |
| `beatbird.audio.spectrum`     | Background FFT thread (numpy + ALSA capture)        |
| `beatbird.sources.spotify`    | go-librespot HTTP client                            |
| `beatbird.hardware.louder_hat`| TAS5825M CHAN_FAULT reader                          |
| `beatbird.hardware.innomaker` | MA12070P status (stub)                              |
| `beatbird.display.amoled`     | Serial protocol to ESP32                            |
| `beatbird.display.led_button` | GPIO NeoPixel ring + button handler                 |
| `beatbird.ha.mqtt`            | Paho client + HA auto-discovery                     |
| `beatbird.webserver`          | Minimal FastAPI dashboard on port 8080              |

### Event flow

```
  ┌──────────────┐
  │ ESP32 / Btn  │──CMD:PLAYPAUSE──┐
  └──────────────┘                 │
                                   ▼
  ┌──────────────┐        ┌──────────────┐
  │ Home Assist. │──MQTT──► BeatBirdBridge │
  └──────────────┘        └───────┬──────┘
                                  │
               ┌──────────────────┼──────────────────┐
               ▼                  ▼                  ▼
        go-librespot        CamillaDSP         Display push
          HTTP API          WS API              (state + FFT)
```

Every 200 ms during playback (2 s idle) the bridge assembles a
`DisplayState` snapshot and pushes it to the display. Every 5 s it collects
system stats (CPU temp, amp faults, WiFi RSSI) and publishes them to MQTT.

## Profiles as the single source of truth

A speaker is defined by exactly one YAML file in `profiles/`. The Makefile
activates one via a symlink:

```
profiles/current.yml -> beat-1.yml
```

Everything downstream — install scripts, CamillaDSP config selection,
systemd unit content, MQTT topic roots — resolves from that profile. No
speaker-specific constants live in code.

## Failure modes & recovery

| Failure                      | Behaviour                                            |
|------------------------------|------------------------------------------------------|
| go-librespot down            | Bridge polls return None → source shown as "none"    |
| CamillaDSP down              | Volume set/get silently fail; bridge stays up        |
| ESP32 USB disconnected       | Serial reconnect loop, 5 s delay                     |
| I²C bus hiccup               | `i2cget` returns error; amp status = "error"         |
| MQTT broker unreachable      | paho auto-reconnects in background; LWT keeps HA sane|

The bridge never self-restarts on its own — systemd handles that with
`Restart=always, RestartSec=5`. Any module-level exception is caught and
logged; the main loop keeps running.

## Secrets

Nothing sensitive in git. At install time:

- `/etc/beatbird/wifi.pass`  — WiFi PSK (600, root)
- `/etc/beatbird/mqtt.pass`  — MQTT password (640, bridge group)
- `/etc/beatbird/env`        — generated shell env for systemd

The bridge service pulls `/etc/beatbird/env` via `EnvironmentFile=` and
never sees the credentials in its YAML profile.
