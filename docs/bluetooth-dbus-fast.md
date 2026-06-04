# Bluetooth: migrate `sources/bluetooth.py` off subprocess to `dbus-fast`

Prep / plan. `sources/bluetooth.py` (~877 LOC) talks to BlueZ + BlueALSA by
**shelling out** to `bluetoothctl` and `busctl` (call / get-property /
set-property / tree) and **regex-parsing the text output**. That is fragile vs
BlueZ updates and slow (a subprocess per call). Goal: native typed D-Bus via
[`dbus-fast`](https://github.com/Bluetooth-Devices/dbus-fast).

> **✅ DECIDED (2026-06-04): Option B.** Steff approved the migration via a
> dedicated asyncio loop-thread holding one persistent `MessageBus`, with a sync
> facade so callers stay unchanged. Incremental (read-paths first, behind a flag),
> pairing/agent stays on `bluetoothctl` initially. **Gated:** the subprocess
> fallback is only dropped after live-adapter validation on a Zipp/Beat (pair a
> phone → list/connect-state/volume/AVRCP/disconnect). Do it in its own PR off
> `prep/big-rocks`. Step 1 (deps + `BluetoothBus` plumbing, no behaviour change)
> is CI-mockable and needs no adapter.

## The blocker to decide first: async ↔ sync

`dbus-fast` is **async-only** (asyncio `MessageBus`). The rest of BeatBird
(bridge loop, webserver handlers) is **synchronous**. Three integration shapes:

| Option | Shape | Verdict |
|--------|-------|---------|
| A | `asyncio.run(coro())` per call | simplest, but tears down the loop + reconnects the bus every call — slow, racy. ✗ |
| B | one asyncio loop on a **dedicated daemon thread**; a singleton holds one long-lived `MessageBus`; sync facade methods submit coroutines via `asyncio.run_coroutine_threadsafe(...).result(timeout)` | **recommended** — one persistent bus, sync API unchanged for callers, clean timeouts. |
| C | make the whole bridge async | out of scope, huge blast radius. ✗ |

**Recommendation: Option B.** A `BluetoothBus` singleton owns the loop thread +
`MessageBus` (system bus); every current public function becomes a thin sync
wrapper around an async method. Callers (`bridge.py`, `webserver.py`) keep their
exact signatures — the migration is invisible to them.

```python
class BluetoothBus:
    _loop: asyncio.AbstractEventLoop      # on a daemon thread, started lazily
    _bus:  MessageBus                     # system bus, connected once, reused
    def _run(self, coro, timeout=4.0):    # sync bridge: submit + .result()
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)
```

## What moves, what stays

| Current (subprocess) | dbus-fast target | Notes |
|---|---|---|
| `_get_bluealsa_pcms` (busctl call GetPCMs + regex) | `org.bluealsa /org/bluealsa Manager1.GetPCMs()` → typed `a{oa{sv}}` | biggest win — kills the worst regex. |
| `_read/_write_bt_volume_by_path` (busctl get/set-property) | `org.bluealsa.PCM1.Volume` property get/set | typed `q`/byte, no parsing. |
| `_is_streaming`, `_find_sink_pcm` | property reads on PCM1 / MediaTransport1 | |
| `send_avrcp` (busctl tree + call) | `org.bluez.MediaPlayer1.<Play/Pause/Next/…>()`; find player via `GetManagedObjects` | replace `tree` text-walk with ObjectManager. |
| `disconnect_device`, `disconnect_all_bt` | `org.bluez.Device1.Disconnect()` | |
| `set_trusted`, `trust_all_paired` | `org.bluez.Device1.Trusted` property set | |
| `set_adapter_alias`, `is/set_discoverable` | `org.bluez.Adapter1` properties | |
| `list_paired_devices`, `_btctl_info` | `GetManagedObjects` → filter `Device1` w/ `Paired=true` | typed dict, no `bluetoothctl info` regex. |
| **pairing / agent flow + `forget_device`** | **KEEP on `bluetoothctl`** (at least initially) | the pairing agent (PIN/confirm) is genuinely easier via bluetoothctl's interactive agent than registering an `org.bluez.Agent1`. Hybrid is fine. |

Device/player/PCM object paths all come from **`ObjectManager.GetManagedObjects`**
(one call → the whole tree, typed) instead of `busctl tree --list` + regex per
function. That single change removes most of the fragility.

## Migration order (incremental, each shippable)

1. Add `dbus-fast` to deps (`pyproject`/bridge venv). Add the `BluetoothBus`
   singleton + loop-thread plumbing. No behaviour change yet.
2. Port the **read paths** first (lowest risk): `GetManagedObjects`-based
   `list_paired_devices` + the BlueALSA PCM/volume reads. Keep the old
   functions as fallback behind a flag for one release.
3. Port the **writes**: Trusted, Disconnect, Adapter properties, AVRCP, volume
   set.
4. Drop the subprocess helpers + the regex. Keep only the bluetoothctl pair/
   forget/agent path.

## Testing

- **Unit (CI, no adapter):** mock `BluetoothBus._run` / the `MessageBus` so the
  mapping logic (GetManagedObjects dict → `BTDevice`, volume raw↔pct) is tested
  without D-Bus. The raw↔pct math (`_vol_raw_to_pct` / `_vol_pct_to_raw`) is
  already pure — unit-test it directly.
- **Integration (needs a real adapter — Zipp/Beat):** pair a phone, verify
  list/connect-state/volume/AVRCP/disconnect against the live bus. This is the
  gate before dropping the subprocess fallback.

## Risks / watch-outs

- BlueZ object paths + the BlueALSA service name (`org.bluealsa`) must match the
  running stack — [[bt-pi-setup-gotchas]] (package name `bluez-alsa-utils`,
  rfkill, missing bt-agent). dbus-fast doesn't fix a missing service, only the
  parsing.
- The loop-thread lifecycle: start lazily, daemon thread, reconnect the bus on
  `Disconnected`. Don't leak a thread per import.
- `dbus-fast` introspection vs hard-coded interfaces: prefer
  `bus.get_proxy_object(..., introspection)` with cached introspection XML for
  the few well-known BlueZ/BlueALSA interfaces to avoid an introspect round-trip
  per call.

## Honest scope note

This is a real rewrite (~877 LOC) that **must be validated on a live adapter**
before the subprocess fallback is removed. The plan above makes it incremental
and keeps the public API stable so nothing else in the codebase changes. Do it
in its own PR off `prep/big-rocks`, port read-paths first, ship behind a flag.
