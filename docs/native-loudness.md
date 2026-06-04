# Native CamillaDSP Loudness vs the manual LoudnessController

Prep / decision. Today the bridge does loudness compensation **manually**: on
every volume change `LoudnessController` recomputes an offset curve and patches
the bass EQ filter gains via `PatchConfig`. Two problems:

1. **Click** — each volume change rewrites filter coefficients live; the
   discontinuity is audible on bass-heavy material (the click/pop we chased).
2. **Drift risk** — reading patched gains back compounds (the loudness-feedback
   bug we already guard against; [[feedback-loudness-feedback-loop]]).

CamillaDSP 2.0+ has a **built-in `Loudness` filter** that does this internally,
smoothly, with no PatchConfig.

## The native `Loudness` filter

```yaml
filters:
  loudness:
    type: Loudness
    parameters:
      fader: Main          # tracks the main volume control
      reference_level: -20.0   # at/above this volume: gain only, no boost
      low_boost: 8.0       # max dB boost below 70 Hz   (default 10)
      high_boost: 4.0      # max dB boost above 3500 Hz (default 10)
      attenuate_mid: false # true = cut the mid instead of boosting extremes
                           #        (keeps headroom, avoids clipping)
```

Behaviour: above `reference_level` → only volume gain. Below
`reference_level - 20` → full boost. Linear in between. Shelves: low < 70 Hz,
high > 3500 Hz. **No PatchConfig, no per-volume coefficient rewrite — the click
source is gone, and there's nothing to read back, so no drift.**

Sources: [CamillaDSP README — Loudness](https://github.com/HEnquist/camilladsp/blob/master/README.md) ·
[issue #196 (loudness Qs)](https://github.com/HEnquist/camilladsp/issues/196) ·
[issue #73 (equal-loudness contour)](https://github.com/HEnquist/camilladsp/issues/73).

## The trade-off vs our voicing feature

The browser "Bass laut / leise + Kurve" gives **per-filter** control
(`bass_shelf` 120 Hz, `sub_punch` 45 Hz, `timpani_body` 70 Hz, `fullness`
200 Hz, `air_lift` 8 kHz — each with base_gain + max_boost + tunable knees).
The native filter is **2 shelves (low/high) + 4 global knobs** (reference,
low_boost, high_boost, attenuate_mid). So native = simpler + click-free, but
loses the fine multi-band bass shaping and the freely-tunable curve.

## Recommendation: **hybrid** (keep the voicing, kill the click)

Split the two jobs the LoudnessController currently does:

| Job | Today | Hybrid target |
|---|---|---|
| **Static tonal voicing** (the bass shape you *always* want) | bass EQ filters at their `base_gain` | **keep** as ordinary EQ filters — still browser-tunable, never volume-patched |
| **Volume-dependent boost** (more bass when quiet) | `max_boost × offset(curve)` patched per volume change → CLICK | **native `Loudness` filter** — CamillaDSP does it smoothly |

So:
- High volume → static voicing only (your "Bass laut").
- Quieter → the native `Loudness` filter fades in the low/high boost (your
  "Bass leise"), with **zero PatchConfig**.

Browser mapping:
- **"Bass laut"** → the static EQ filter gains (unchanged, fully tunable).
- **"Bass leise"** → the `Loudness` `low_boost` (one number, not per-filter).
- **"Kurve"** → `reference_level` (where the boost starts; the 20 dB ramp width
  is fixed in the native filter — slightly less tunable than the current custom
  knees, the one real loss).

The bridge stops patching gains per volume change entirely — it just sets the
Main volume; CamillaDSP's `Loudness` reacts. `LoudnessController` shrinks to
"apply the static voicing + push the Loudness params on a voicing edit (rare),
via SetConfig" — no per-volume work, no read-back, no drift.

## ✅ DECIDED (2026-06-04): Hybrid

Steff chose **Hybrid**: static voicing stays as ordinary (browser-tunable) EQ
filters, the native CamillaDSP `Loudness` filter takes over the
volume-dependent boost → click gone, no read-back/drift. Accepted trade-off:
loses per-band *max_boost* and the custom knee width.

**Next step (gated on a live Zipp for the A/B):** rework
`LoudnessController.apply()` to set the `Loudness` params instead of patching
bass gains; keep `build_loudness` for the static filters; map the web
`/api/loudness` "leise" field → `low_boost`, "Kurve" → `reference_level`;
rewrite `test_loudness_curve.py` around the new mapping. Do **not** commit
before an audible A/B on the Zipp (does native `low_boost` @ 70 Hz feel like
today's sub-heavy boost?).

---

## Options that were on the table

1. **Hybrid** (recommended) — keep static voicing, native filter for the
   volume-dependent part. Keeps most of the web UI; loses only the per-band
   *max_boost* and the custom knee width.
2. **Full native** — drop the manual chain entirely; web UI becomes
   reference/low_boost/high_boost. Simplest, biggest UI change.
3. **Status quo + click mitigation** — keep manual, just smooth the PatchConfig
   transitions (ramp the gain over a few ms). Smallest change, doesn't remove
   the drift risk.

If hybrid: rework `LoudnessController.apply()` to set the `Loudness` params (not
patch bass gains), keep `build_loudness` for the static filters, and update the
`/api/loudness` web endpoint's "leise" field to map to `low_boost`. The voicing
tests (`test_loudness_curve.py`) get rewritten around the new mapping.
Needs an audible A/B on the Zipp before committing (does native low_boost @
70 Hz feel like the current sub-heavy boost?).
