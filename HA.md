# HA sensor data on the BeatBird display — plan

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
