# Measurement session — REW + UMIK → CamillaDSP

A runbook for measuring a BeatBird speaker with REW + a UMIK and turning the
result into DSP changes. Two goals, increasing effort:

1. **Driver time-alignment** (a `Delay` filter) — cheap, audible, tightens the
   crossover + imaging on the 2-way speakers.
2. **Room / driver correction** (PEQ or a `Conv` FIR filter) — the bigger sonic
   upgrade: correct magnitude (and, with a timing reference, phase) at the
   listening spot.

> The Beat was already REW-tuned once (the `rew_*` PEQ bands in beat.yml), so the
> generic REW flow is assumed known. This focuses on the **BeatBird-specific**
> parts: signal path, flat mode, driver isolation, and mapping back to CamillaDSP.

---

## 0. Pre-session checklist

- [ ] UMIK-1 + its calibration file loaded in REW.
- [ ] Speaker on the LAN, reachable (`ssh <host>`), services up.
- [ ] Decide the speaker: **Zipp** (crossover is in CamillaDSP → drivers
      isolatable) or **Beat** (crossover is in the TAS5825M chip → drivers
      *not* isolatable from CamillaDSP — magnitude/room only). **LoungePi** (Pi 5,
      all-active, crossovers in CamillaDSP, no CPU limit) is the ideal FIR target
      once its multi-lane path is done.
- [ ] Mic at the primary listening position, on a stand, pointed at the speaker.

---

## 1. Put the speaker in FLAT measurement mode

We measure the driver + room, **not** our EQ. Switch to the `-meas` config (flat,
loudness suspended) via the web DSP switcher:

`http://<speaker>:8080/advanced` → DSP switcher → **"Messmodus (flat, REW)"**

(or POST `/api/dsp-config {"name":"<speaker>-meas"}`). Switch back to "Produktion"
when done. While a non-production config is active the bridge auto-suspends the
loudness patch loop, so nothing moves underneath the measurement.

---

## 2. Get the sweep INTO the speaker

Sources write the ALSA loopback (`beatbird_mix` dmix → `hw:Loopback,0`),
CamillaDSP captures the other end. So the clean, coloration-free way to feed a
test sweep through the **full** chain (loopback → CamillaDSP → amp → driver) is to
play REW's exported sweep WAV **on the Pi**:

```bash
# copy REW's sweep export to the Pi, then:
aplay -D beatbird_mix /tmp/sweep.wav     # goes through DSP + amp + driver
```

Avoid Bluetooth for measurement (codec + latency coloration). TOSLINK from the
measuring laptop is clean too if the speaker has optical in enabled.

### Timing reference — what you can get
- **Magnitude only** (for PEQ / minimum-phase FIR room correction): no timing
  reference needed. Play the sweep on the Pi, capture in REW with **no timing
  reference**. This is ~90 % of the value and is the easy path.
- **Phase / delay** (driver alignment, full linear-phase FIR): needs a consistent
  timing reference. Cleanest is to **run REW on the Pi itself** (Java; comfortable
  on the Pi 5 LoungePi, heavy but possible on a Pi Zero) so REW controls play +
  capture on one machine and knows time-zero. Then every measurement shares the
  same path latency → **relative** driver timing is exact even if absolute is not.

---

## 3. Measurement A — room / driver magnitude → correction

1. Flat mode (step 1), sweep on the Pi (step 2), capture at the listening spot.
2. Optionally spatial-average a few mic positions around the seat (REW "Average").
3. In REW: target curve (slight downward house curve), generate either:
   - **PEQ** (a handful of filters) → add as `Biquad` filters in the speaker's
     production config (like the existing `rew_*` bands), **or**
   - **FIR** → REW "Export → minimum-phase FIR (WAV)". Load as a `Conv` filter
     (step 5). FIR can correct far more detail than a few PEQ bands.

## 4. Measurement B — driver time-alignment (Zipp; Beat caveat)

Goal: the relative delay between the two drivers so their acoustic centers line up
at the crossover.

- **Zipp** (crossover in CamillaDSP, ch0 = 3" broadband, ch1 = 1" tweeter): measure
  each driver alone. Ask me to drop two throwaway measurement configs
  (`zipp-meas-broadband` / `zipp-meas-tweeter`) that mute the other output channel,
  or mute one channel live. Measure each → compare impulse arrival times in REW →
  the difference is the delay to add (as a `Delay` filter) on the **earlier**
  driver's channel.
- **Beat** (crossover inside the TAS5825M): the woofer/tweeter split isn't visible
  to CamillaDSP, so per-driver isolation needs the amp, not the DSP — skip
  alignment here unless we drive the TAS registers to mute a driver.

---

## 5. Mapping results back to CamillaDSP

- **PEQ bands** → `Biquad` filters in the production config + their names in the
  per-channel pipeline (mirror the existing `rew_*` entries).
- **FIR correction** → a `Conv` filter loading the REW WAV:
  ```yaml
  filters:
    room_fir:
      type: Conv
      parameters:
        type: Wav
        filename: /etc/camilladsp/fir/<speaker>-room.wav
        channel: 0
  ```
  Add `room_fir` to the channel pipeline(s). Ship the WAV via an install role so
  it persists (overlayroot). **CPU/latency**: FFT-partitioned convolution —
  moderate taps (a few k) are fine on a Pi Zero 2W and add ~tens of ms latency
  (irrelevant for music). Deep correction / long taps → prefer the Pi 5.
- **Driver delay** → a `Delay` filter on the earlier driver's channel:
  ```yaml
  filters:
    tweeter_align:
      type: Delay
      parameters: {delay: 0.35, unit: ms}   # value from measurement B
  ```

Validate any new config with `camilladsp --check <file>` on the speaker before
swapping it in (the DSP switcher reads the repo file live; `beatbird-update`
pulls it). Keep the change as an A/B variant first, confirm by ear, then promote
to production.

---

## When you run it
Tell me the speaker + which goals, and I'll prep the throwaway measurement
configs (flat / per-driver isolation) and the result→config edits as you feed me
the REW numbers.
