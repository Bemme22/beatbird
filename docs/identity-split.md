# Identity split: model / instance / user-label

Prep / plan + decisions. Today `Identity` (profile YAML) carries three things
that change at different rates as ONE static blob:

```python
class Identity(BaseModel):
    hostname: str        = "beatbird"          # per-unit
    friendly_name: str   = "BeatBird Speaker"  # human, should be user-editable
    speaker_id: str      = "beatbird_generic"  # MQTT topic + client_id, per-unit
```

So every physical unit needs its own profile (`beat-1.yml` + `beat-2.yml` are
byte-for-byte dupes apart from these three lines), and renaming a speaker means
SSH + YAML edit. We want three layers that change independently:

| Layer | Lives in | Changes when | Drives |
|---|---|---|---|
| **hardware-class** (model) | profile YAML (`identity.model`) | never per-unit | which `beat.yml` / `zipp-mini-2.yml`, DSP/display/soundcard |
| **hardware-instance** | auto-detected from the Pi | new board | `speaker_id` (MQTT/client_id), hostname suffix — stable, unique, no YAML |
| **user-label** | settings-overrides (browser) | user renames | `friendly_name` (BlueZ alias, web title, MQTT idle naming) |

Result: `beat-1.yml` + `beat-2.yml` collapse into one `beat.yml`; naming a
speaker is a browser field, no SSH.

## Where each field is used today (migration targets)

- `speaker_id` → `mqtt_topic_base` (`config.py:334`), MQTT `client_id`
  (`ha/mqtt.py:58`). **Topic-stability critical** (see migration below).
- `friendly_name` → BlueZ adapter alias (`bridge.py:647`), MQTT idle device
  naming (`bridge.py:1546`), web UI title. → move to user-label
  (settings-overrides), with the profile/default as fallback.
- `hostname` → set at provisioning. → derive `<model>-<short-id>`.

## Open decisions (need your call before coding)

### 1. Hardware-instance ID source
| Source | Stable across reimage? | Notes |
|---|---|---|
| **Pi CPU serial** (`/proc/cpuinfo` Serial / `/sys/firmware/devicetree/base/serial-number`) | ✅ yes (board-tied) | **recommended** — survives SD reflash, unique per board |
| MAC (eth0/wlan0) | ⚠️ mostly | changes with USB dongles / wlan-vs-eth; Zipp uses onboard wlan ([[zipp-onboard-wifi]]) |
| `/etc/machine-id` | ❌ no | regenerated on reimage → would orphan MQTT history |

Recommend **CPU serial → short hash** (e.g. last 4 hex of a SHA of the serial)
for the human-facing suffix, full serial hash for `speaker_id`.

### 2. Naming pattern
- hostname: `<model>-<short-id>` (e.g. `beat-3f2a`).
- speaker_id: `<model>_<short-id>` (MQTT-safe, no slashes).
- friendly_name default: `<Model> <short-id>` (e.g. "Beat 3f2a"), user overrides
  in the browser.

### 3. MQTT topic migration (don't lose HA history)
Changing `speaker_id` changes every MQTT topic → HA loses the entity history.
So: **existing speakers keep their current `speaker_id`** via an explicit
profile/override pin; only NEW speakers auto-derive. Mechanism: `speaker_id` =
`identity.speaker_id` if explicitly set (legacy/pinned), else auto from the
instance ID. Document the pin for beat-1/beat-2 before collapsing their profiles.

## Config-model skeleton (target)

```python
class Identity(BaseModel):
    model: str = "beatbird"                     # hardware-class (was implicit)
    # All three below become DERIVED unless explicitly pinned:
    hostname: str | None = None                 # None -> f"{model}-{short_id}"
    friendly_name: str | None = None            # None -> override -> default
    speaker_id: str | None = None               # None -> f"{model}_{short_id}"
                                                # PINNED on beat-1/2 for MQTT history
```
Resolution order (a `resolved_*` property on Profile, instance id injected):
`pinned value` → `settings-override (friendly_name only)` → `derived from instance`.

## 6-phase rollout

1. **Instance ID helper** — `system.hardware_instance_id()` (CPU serial → short
   id). Pure + testable. *(scaffolded now — see `system.py` + test.)*
2. **Identity model** — add `model`, make the three fields optional/derived;
   keep back-compat (explicit values still win).
3. **Resolution** — `Profile.resolved_speaker_id / hostname / friendly_name`
   using the instance id + settings-overrides; route all call sites through them.
4. **user-label** — friendly_name editable in the web Settings page → written to
   settings-overrides; bridge picks it up (BlueZ alias + web + MQTT) on the
   existing override poll.
5. **MQTT migration** — pin beat-1/2 `speaker_id`, then collapse
   `beat-1.yml`+`beat-2.yml` → `beat.yml`; provisioning selects by `model`.
6. **Provisioning** — derive + set the hostname from model+instance at install;
   drop per-unit profiles.

## Phase 1 scaffold (done now, decision-free)

`system.hardware_instance_id()` reads the Pi CPU serial and returns a short
stable id — the one piece that's useful regardless of which naming/MQTT
decisions land. Unit-tested against a fixed serial. Everything else waits on the
three decisions above.
