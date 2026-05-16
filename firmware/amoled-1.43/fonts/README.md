# Fonts — Departure Mono integration

This directory holds the build tooling for converting Departure Mono (an
external OFL-licensed pixel font) into LVGL C arrays embedded in the firmware.

## Why Departure Mono

Pixel-perfect monospaced font with a Nothing-Glyph / sci-fi terminal feel.
Renders crisply on the SH8601 AMOLED only at exact 11 px multiples —
**11 px, 22 px, 33 px, 44 px** — so we use those sizes throughout the theme.

License: SIL Open Font License (free for commercial + embedded use).

## One-time setup

The .c font files are **not committed**. Run the build script once on your
dev PC. Requires Python 3.8+ and Node.js (LTS is fine — same Node that
PlatformIO bundles works).

```bash
cd firmware/amoled-1.43/fonts
python3 build.py
```

This will:

1. Download `DepartureMono-1.500.zip` from the official GitHub release
2. Extract `DepartureMono-Regular.otf` to this directory
3. Run `npx lv_font_conv` (auto-fetched) to generate
   - `src/ui/fonts/departure_mono_11.c`
   - `src/ui/fonts/departure_mono_22.c`
   - `src/ui/fonts/departure_mono_33.c`
   - `src/ui/fonts/departure_mono_44.c`

Each `.c` file is ~5–30 KB. Total flash footprint: ~50 KB.

## Activate in build

After running `build.py`, uncomment the build flag in `platformio.ini`:

```ini
build_flags =
    ...
    -DHAS_DEPARTURE_MONO=1
```

Without the flag the firmware falls back to Montserrat, so the build always
works even if you haven't run the script yet.

## Maintenance

To upgrade to a newer Departure Mono release:

1. Bump `RELEASE_VERSION` in `build.py`
2. `python3 build.py --clean` then `python3 build.py`
3. Test, commit the platformio.ini change if needed

To regenerate from a local OTF without re-downloading:

```bash
python3 build.py --no-download
```

## Character coverage

Currently included:

- ASCII printable (0x20–0x7E)
- Latin-1 supplement (0xA0–0xFF) — German umlauts, ß, copyright etc.
- Em/en dash, smart quotes, bullet, ellipsis

Edit `RANGES` in `build.py` to add more (e.g. Cyrillic — but expect each
size to grow by ~10 KB).
