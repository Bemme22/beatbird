# Serial protocol — Pi ↔ ESP32 AMOLED display

Line-oriented ASCII over USB CDC at **115200 baud**. Every message is a
single line terminated with `\n`; no framing, no checksums, no response
model beyond best-effort. Either side must tolerate dropped, corrupted, or
unexpected lines.

## Pi → ESP32

### `ST` — compact state push (200 ms during playback, 2 s idle)

One pipe-separated line with key:value tokens. Order is conventional but
the ESP32 parses by key so reordering is safe.

```
ST:play|TI:Once in a Lifetime|AR:Talking Heads|SO:spotify|VO:45|PO:45300|DU:232000|LV:64|TM:18:42|FX:12,18,22,35,...
```

| Key | Meaning                                           | Range / values                          |
|-----|---------------------------------------------------|-----------------------------------------|
| ST  | playback state                                    | `play` `pause` `stop` `standby`         |
| TI  | track title                                       | UTF-8, `|` escaped as space             |
| AR  | artist                                            | UTF-8                                   |
| SO  | active source                                     | `spotify` `bluetooth` `toslink` `snapcast` `none` |
| VO  | volume                                            | 0–100                                   |
| PO  | track position                                    | ms                                      |
| DU  | track duration                                    | ms (≥ 1)                                |
| LV  | signal level (for VU-style anim)                  | 0–100                                   |
| TM  | clock                                             | `HH:MM`                                 |
| FX  | optional spectrum array                           | N comma-separated 0–100 values          |

### `SYS` — system health (every 5 s)

```
SYS:cp=47.3|hstereo=ok|hsub=ok|ds=1|sv=1|wi=-58
```

| Key  | Meaning                                      |
|------|----------------------------------------------|
| cp   | CPU temperature (°C)                         |
| h*   | amp-channel status — `ok` / `OT` / `OC` / `DC` / `error` ; one key per channel; the prefix after `h` is the channel name (e.g. `hstereo`, `hsub`, `hleft`) |
| ds   | CamillaDSP active (`0`/`1`)                  |
| sv   | go-librespot active (`0`/`1`)                |
| wi   | WiFi RSSI (dBm, negative)                    |

### `PAL` — accent colour (once per connect)

```
PAL:F0CB7B
```

Pushed by the bridge immediately after the serial connection is established,
and re-sent on every reconnect (the ESP32 may have rebooted). The colour is
defined in the speaker profile under `display.accent_color` and represents
the per-speaker visual identity. The ESP32 stores it as its primary tint —
volume ring, progress arc, energy dots, text, and play/pause icon all
inherit from it. Source markers remain coloured per source.

Format: 6-char hexadecimal RGB, with or without leading `#`. Case-insensitive.

### Single-shot messages

These are legacy from v1 and may still be emitted occasionally for UX
immediacy (e.g. volume knob feedback):

```
VOL:45              volume changed (0–100)
STATE:PLAY|PAUSE|STOP|STANDBY
SOURCE:spotify|bluetooth|toslink|snapcast|none
BOOT:stage|progress
ERROR:service|message
TIME:HH:MM          clock-only update
DATE:<string>       standby date line, preformatted + localized by the Pi
                    (e.g. "DATE:SAMSTAG · 7. JUNI"). UTF-8; rendered in Inter,
                    so umlauts / middle-dot are fine (NOT split-flap text).
                    Re-sent on every idle rotation so it survives an ESP reboot.
WX:t=18|c=2|h=22|l=12   weather snapshot (see below)
BRT:<0-255>         display brightness (time-of-day auto-dim). Firmware ramps
                    to this as the active level; dims further when untouched.
NIGHT:<0|1>         minimal night standby (1 = dim clock only, weather/status/
                    icon hidden). Driven by the Pi's day-phase logic.
```

### WX: — Weather data (Bridge → Display)

Pushed every 30 min by the bridge from an Open-Meteo poll.

```
WX:t=<temp>|c=<icon>|h=<high>|l=<low>
```

Fields:

| Field | Meaning |
|-------|---------|
| `t`   | Current temperature in °C, rounded to int |
| `c`   | Weather icon id (see `firmware/include/state.h::WeatherIcon`): `0`=clear, `1`=partly cloudy, `2`=cloudy, `3`=fog, `4`=rain, `5`=snow, `6`=thunderstorm |
| `h`   | Today's high temperature in °C, rounded to int |
| `l`   | Today's low  temperature in °C, rounded to int |

All fields are optional and independent; missing fields keep their last
known value. The first `WX:` ever received flips `State::weather.valid` to
true; the standby screen uses that flag to gate showing the weather
block (graceful degrade if no bridge config / no internet).


## ESP32 → Pi

```
VOL:0-100           user rotated the arc; bridge calls set_volume()
CMD:PLAYPAUSE       tap gesture — same as PLAY in v1
CMD:PLAY
CMD:PAUSE
CMD:NEXT
CMD:PREV
CMD:STOP
CMD:SOURCE:bluetooth    source picker selected (Phase 2)
CMD:BT_PAIR         long press on single-button builds
TEMP:22.5           QMI8658 head temperature (logged, unused)
[hb] t=12345 ...    heartbeat line, ignored by bridge
```

## Versioning & evolution

The bridge treats unknown ESP32 messages as `debug` log lines — adding new
commands on the firmware side is safe. The ESP32 side should likewise
ignore unknown tokens in state lines so the Pi can add new fields without
breaking older firmware.

For a breaking change, introduce a new leading verb (`ST2:…`) and have the
firmware subscribe to both.
