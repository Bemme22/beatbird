# HA sensor data on the BeatBird display — plan

> ⚠️ **Superseded (2026-09-16).** This planning doc's `SENS:` design (raw
> values, direct room temps) was superseded by what actually got built in
> PR #5: `FACE:` (see `docs/protocol.md` §"FACE — standby faces" and
> `src/beatbird/ha/faces.py`), which carries HA-side *decisions* rather than
> raw sensor values, has priority/rotation, icons, and tap-to-acknowledge —
> none of which this plan anticipated. Keep this file for the phase-1/phase-2
> historical reasoning (direct-MQTT vs. Statestream), but treat `docs/protocol.md`
> as the current spec, not this one.

Roadmap for surfacing smart-home / Home-Assistant sensor values on the BeatBird
standby screen (room temperatures now, power/energy later). **This is a planning
doc for a dedicated future session — nothing here is built yet.**

> **Scope & secrets.** This file describes the *BeatBird-side* feature only — a
> generic "subscribe to sensor topics, show them on the display" mechanism. It
> contains **no personal data**: real broker IPs, entity ids, room names and
> topics live in gitignored `secrets/` (the repo is public). The **HA-side**
> work (which entities, how they reach MQTT) belongs in the *separate* HA
> workspace, never in this repo.

---

## Goal

The standby screen becomes a glanceable home dashboard:
- **Now:** a handful of room temperatures.
- **Later:** power / energy values (consumption, PV, etc.).
- **UX:** "both" — values mixed into the idle-text rotation **and** a dedicated
  **sensor face** the standby occasionally switches to (clock small, sensors big).

This doubles as the calm "eye-catcher" we parked on the player: the standby
*changing faces* is visual interest without continuous animation.

---

## What already exists (the easy part)

- The bridge runs an MQTT client (`src/beatbird/ha/mqtt.py`) that already
  **subscribes** (`_on_connect` → `client.subscribe(...)`) and dispatches in
  `_on_message`. Today it only listens to its own `…/set/#` command topics.
  Adding sensor-topic subscriptions is a small extension, not a rebuild.
- The **Zipp is already connected** to the HA broker (it publishes its event
  stream there). Reading *back* from the broker is the only new direction.
- `WX:` (weather) is the precedent: bridge polls a source, caches, pushes a
  pipe-separated line to the ESP, the standby screen renders it. The sensor
  feature mirrors this exactly.

---

## Data source reality (the gate)

Mixed, per the user:
- **Some sensors already on MQTT** (Zigbee2MQTT / Tasmota / ESPHome publish raw
  topics) → BeatBird subscribes **directly** to the device topic, HA does
  nothing. Ideal; start here.
- **Most live only in HA** (WiFi/cloud integrations) → HA must mirror their
  states onto MQTT first. Two HA-side options (separate workspace):
  - **MQTT Statestream** with an *include* filter for just the wanted entities
    → publishes to `…/statestream/sensor/<entity>/state`. Cleanest, declarative.
  - **Per-entity automation** publishing on state change. More manual, finer.

**Decision for the session:** prefer Statestream with a tight include list.

---

## Architecture (BeatBird side)

```
HA / MQTT sensors ──(subscribe)──▶ bridge cache ──(SENS: line)──▶ ESP32 standby
```

1. **Config (generic in repo, values in secrets).**
   - Profile gets a generic toggle, e.g. `standby.sensors.enabled: true` and a
     rotation cadence — schema only, no personal data.
   - Real mapping in gitignored `secrets/sensors.yml` (or env): a list of
     `{ topic, label, unit, json_path?, group }` entries. `json_path` extracts a
     field if the payload is JSON (z2m sends `{"temperature":21.5,...}`);
     omit for plain-value topics.
2. **Bridge.**
   - On connect, subscribe to each configured topic (alongside the existing
     `…/set/#`).
   - In `_on_message`, match sensor topics, parse (plain float or `json_path`),
     store latest value + timestamp in a `SensorStore`.
   - Mark values **stale** if not updated within N minutes → display greys/hides
     them (don't show a frozen 21.5° from yesterday).
   - Push to the ESP on the normal cadence.
3. **Serial protocol — new line `SENS:`** (follows the evolution rule: old
   firmware ignores unknown lines). Pipe-separated, ASCII, short keys:
   ```
   SENS:wohnzimmer=21.5°|buero=23.0°|schlafz=19.0°|power=412W
   ```
   - Labels are pre-shortened on the Pi (display is width-limited).
   - Stale entries omitted or suffixed with a marker.
   - Units folded into the value (keeps the parser trivial).

---

## Display design (fits "Warm Funktional")

Two surfaces, both fed by the same `SENS:` cache:

- **A) Idle-rotation** — sensor strings join the existing cross-fade idle line:
  `News → "Wohnzimmer 21.5°" → "Büro 23°" → …`. No new layout; cheapest.
- **B) Sensor face** — a second standby layout: **small clock** (top/corner),
  **large sensor grid** (label + value + unit, accent-tinted, cream type). The
  standby auto-cycles clock-face ↔ sensor-face every ~N s with a one-shot
  cross-fade (bounded transition → no judder). Optionally tap/swipe to switch.

Start with (A) for first data on screen; (B) is the "wow" surface.

---

## Phasing

- **Phase 1** — direct-MQTT room-temp sensors only → idle-rotation (A).
  Minimal, no HA-side work. Proves the path end-to-end.
- **Phase 2** — HA MQTT Statestream for the rest → the dedicated sensor face (B),
  multi-face standby cycling, stale handling.
- **Phase 3** — power / energy values; maybe simple trend/arrow (↑/↓ vs. last).

---

## Open questions for the dedicated session

- Final sensor list + short labels (room temps; later power channels).
- Statestream include-list vs. per-entity automations (HA workspace).
- Face-cycle cadence + whether tap/swipe switches faces manually.
- Units / decimals / stale-timeout per sensor.
- **Beat broker:** MQTT is currently *disabled* on `beat-1.yml` (placeholder
  host → start() hangs, see the mqtt-placeholder gotcha). Enabling sensor data
  on the Beat needs the real broker host in secrets first. The Zipp is ready.
- Keep all personal topics/entities/IPs in `secrets/`; HA config in the separate
  workspace.

---

## Touch points (when we build it)

- `src/beatbird/ha/mqtt.py` — subscribe to sensor topics, parse in `_on_message`.
- `src/beatbird/config.py` — `standby.sensors` schema (generic).
- `secrets/sensors.yml` (gitignored) — the real topic→label mapping.
- bridge — `SensorStore` + push `SENS:` to the display.
- `src/beatbird/display/amoled.py` — `push_sensors()` (mirror `push_*`).
- firmware `screen_standby.cpp` — parse `SENS:`, idle-rotation entries + the
  sensor face.
- `docs/protocol.md` — document the `SENS:` line.

---

# Display concept — decided 09.09.2026

> Supersedes the "clock small, sensors big" sketch above wherever they differ.
> Two findings drove it: what the panel can physically carry at reading
> distance, and what kind of thing the user actually wants to see.

## The hard constraint: one value per face, and it must be a number

Measured on RobinPi (1.75", 466 px across → **0.095 mm/px**):

| Element | Size | Cap height | At 3 m |
|---|---|---|---|
| `inter_clock` (standby time) | 140 px | ~9.7 mm | **~11 arcmin** — reads fine |
| `inter_title` (player title)  | 40 px  | ~2.8 mm | **~3.2 arcmin** — unreadable |

20/20 acuity resolves a letter at **5 arcmin**. The player title is *below* that
from the kitchen table — not "small", genuinely not resolvable. So:

- **A face carries exactly ONE far-readable value**, at roughly clock size.
  Anything else on that face is for arm's length, not for the room.
- ⚠️ **The big value must be a number or a symbol, never a word.** Digits are
  ten known shapes read as patterns, which is why 11 arcmin is enough for the
  clock; arbitrary text at the same size is much harder. "WASCHMASCHINE FERTIG"
  fails twice — it does not fit the width and it does not fit the eye.
- **Layout: reuse the standby clock's geometry** (small label above, 140 px
  value, small detail below). It is already verified at the real distance; a
  new layout would have to earn that again.

## Reading distance is a property of the INSTALLATION, so it goes in the profile

A speaker 3 m away carries one value; one at arm's length could carry three.
Which faces a speaker runs, and how dense they are, belongs in its profile YAML
next to `status_led` and `white_point` — same rule, same reason. SwallowPi is
getting a display too, possibly a larger one, and the arcmin budget there is a
different number entirely.

## What earns a face

1. ⭐ **Only what would change what you do.** "Living room 21 °C" is
   availability, not information. Show it and the display becomes wallpaper.
2. **States beat absolute values.** A number needs interpreting; a state does not.
3. **Calm by default, exception interrupts.** A short rotation of quiet faces;
   anything crossing a threshold takes the screen.

⭐⭐ Point 3 means **notifications are not a second feature**: they are entries in
the same list with a high priority and a timeout. Build the priority list, and
"washing machine done" is a configuration, not new code.

## ⭐⭐ The derivation belongs in HA, not here

Every case the user actually named is a *derived condition*, not a sensor value:

| Wanted | What it really is |
|---|---|
| Temperature drop in a room | rate of change |
| Warm inside, cooler outside → ventilate | comparison of two sensors |
| Washing machine done | state transition |
| Heat pump misbehaving | deviation from expected |
| Energy | none of the above as a raw value — see below |

So the bridge should subscribe to **decisions**, not raw sensors: HA has the
history, the templates and the statistics; this repo has a line-based ASCII
protocol that has no business computing dew points. One HA-side rule then
serves every speaker in the fleet.

⚠️ **Ventilation must compare absolute humidity or dew point, not relative
humidity.** In summer outside air is often cooler *and* wetter — a
relative-humidity rule gives correct advice in winter and wrong advice in
summer, and that only becomes obvious months later.

## Why energy feels awkward — and the three shapes that work

Instantaneous watts is the worst possible glanceable value: it moves every
second, a kettle makes it meaningless, and nobody acts on it. Energy becomes
usable only as:

1. ⭐⭐ **Baseline anomaly** — the minimum over a trailing window. A house has a
   floor; if the night baseline sits at 180 W instead of the usual 90, something
   is on that should not be. One number, clearly actionable, and it needs a
   minimum over a window rather than any statistics.
2. **Pace, not level** — kWh so far today against the same point yesterday.
3. **Opportunity** — PV surplus or a cheap tariff window. Not applicable here
   (neither exists yet).

**Heat pump:** the sharpest signal is not running/idle but the **electric
backup heater** — binary, expensive, and you would go and look. Then cycling
rate (starts per hour), then power draw without a flow-temperature rise.

**Water meter (planned):** the point of it is **leak detection** — continuous
flow over N minutes with no expected draw — not consumption.

## Suggested build order

**Start with washing machine + ventilate.** Unambiguous, no tuning, and between
them they cover the two primitives: an event with a timeout, and a standing
condition. Baseline, heat pump and water then attach with no new code.

⚠️ **Deliberately not first: baseline and heat pump.** Both need history before
"normal" is known. Set thresholds by feel and the speaker cries wolf for weeks
until it gets ignored — and an ignored indicator is worse than none.
