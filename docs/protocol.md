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

**Extended form.** If the profile sets any of `accent_glow`, `accent_dim`,
`text_primary`, `text_secondary` or `accent_alert`, the bridge sends the
key=value form instead — `PAL:a=2D6A4F|g=52B788|d=1B4332|p=F4EFE0|s=A89E89|e=C73E2C`.
Slots are applied in the fixed order `agdpse`, so an explicit `g=`/`d=` in the
same line overrides what `a=` derived. Both forms are accepted; the firmware
tells them apart by the presence of `=`.

**Two slots are derived from the accent** whenever they are not pushed:

| Slot | Derivation | Why |
|------|-----------|-----|
| `d` accent_dim  | each channel `>> 2` (~25 % on black) | unfilled ring/bar segments |
| `g` accent_glow | one gain on all channels until the largest hits 255 — HSV `V → 1`, hue and saturation untouched | the "playing" colour |

⚠️ `g` is brightened by **saturating, never by mixing in white**. Its only
consumer is the LED strip's PLAY state, which renders it at ~full level, and a
pastel at full level is white light. Before 05.09.2026 `g` was not derived at
all: a profile with no palette left it on the compile-time default, so RobinPi
played a white VU meter while every other state showed the accent.

### `LED` — status-strip wiring (once per connect)

```
LED:pin=18|n=46|rgbw=1|bri=40|wmix=45|wp=FF8C64|map=bloom|join=inner
```

Pushed right after `PAL:` on every (re)connect, and again when the ESP32
reports a reboot. Describes the addressable status strip wired to a free GPIO
of the display board — RobinPi calls it the *Brustfleck*.

| Key    | Meaning                                        | Range / values          |
|--------|------------------------------------------------|-------------------------|
| `pin`  | ESP32 GPIO the strip data line hangs on        | 0–48 (NOT a Pi BCM pin) |
| `n`    | number of pixels; `0` disables the strip       | 0–300                   |
| `rgbw` | `1` = SK6812 RGBW (GRBW), `0` = WS2812 RGB (GRB) | `0` / `1`             |
| `bri`  | global brightness/current cap                  | 0–255                   |
| `map`  | `area` = one diffused cluster (level drives brightness); `mirror` = bars filled from the CHAIN's middle outwards; `bloom` = each bar swells from ITS OWN midpoint to both ends | `area` / `mirror` / `bloom` |
| `join` | which ends the two bars are wired together at (`mirror` only — a bloom is symmetric, so it cannot tell) | `inner` / `outer` |
| `wp`   | **white point**: the RGB triple that renders as neutral white on this strip; each channel is scaled by `wp/255` | 6-char hex, `FFFFFF` = off |
| `wmix` | percent of a colour's achromatic part rendered on the RGBW white die instead of mixed from R+G+B | 0–100 |

Every token is optional; a missing one keeps its previous value, so a partial
line is a valid update. Values come from `display.status_led` in the speaker
profile.

**Why this is on the wire and not a build flag.** Pin, count and chip type are
facts about *one enclosure*, and the repo ships one firmware image for every
speaker — same argument as `PAL:`. Consequently the firmware drives **no GPIO
at all** until this line arrives; that is a safety property, not laziness:
GPIO18 is the strip pin on RobinPi but `MAIN_I2C_SDA` on the 1.43 board, so a
compiled-in default would have a Beat/Zipp bit-banging its own I²C bus.

**Why an RGBW strip needs `wp` and `wmix` — measured on RobinPi, 06.09.2026.**
Both exist because an LED strip is *not* a colour-managed display, and sRGB
values sent to one are drive levels, not brightnesses.

* `wp` — a green die emits roughly 2–3× the perceived light of a red one at the
  same digit (higher efficacy, and the eye peaks near 555 nm). Uncorrected, every
  warm colour drifts green or white: champagne `F0CB7B` read as arctic blue-white,
  bronze `E0913F` as yellow-green, and yellow `FFDD00` kept a green cast, while
  `FF6A00` — the one colour with little green and no blue — was clean. One
  calibration fixes all of them. Calibrate on **white**, never on the target
  colour: a cast hides inside a saturated hue (yellow *is* red plus green) and
  is obvious on a neutral. Do it at high `bri`, where the dies still scale.
* `wmix` — the achromatic part of a colour must come from the white die, not be
  mixed from three narrow-band dies (that mix is never neutral; blue dominates,
  hence the cold cast). But a phosphor white die is far brighter per digit, so a
  1:1 substitution over-whitens — at `wmix=100` bronze rendered as plain warm
  white. Hence a percentage, applied **after** `wp`: the minimum over unbalanced
  channels would pick the wrong achromatic amount.

⚠️ Neither belongs in `PAL:`. The display and the strip share one palette, so a
colour bent until the strip looks right would be wrong on the panel. These are
calibrations of one strip's dies and live in `display.status_led`.

Re-sends are idempotent — the firmware compares the wiring and only rebuilds
the driver when pin, count or chip actually changed, so a flapping USB link
does not blink the strip.

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
