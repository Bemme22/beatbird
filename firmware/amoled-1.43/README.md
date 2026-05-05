# BeatBird firmware — Waveshare ESP32-S3-Touch-AMOLED-1.43

PlatformIO project for the AMOLED display firmware used by Beat #1, Beat #2,
Zipp Mini 2 and Zipp 2.

## Build / flash

```bash
cd firmware/amoled-1.43
pio run -t upload          # build & flash over USB
pio device monitor         # serial log at 115200
```

The firmware does not need to know which speaker it's running on — the Pi
pushes everything (title, artist, volume, spectrum, etc.) over the serial
link using the protocol described in [`../../docs/protocol.md`](../../docs/protocol.md).

## Hardware

| Part                  | Detail                                                  |
|-----------------------|---------------------------------------------------------|
| SoC                   | ESP32-S3-R8 (WROOM-1 N16R8)                             |
| Display               | 1.43" 466×466 AMOLED, SH8601 driver, QSPI interface     |
| Touch                 | FT6x36 capacitive                                       |
| IMU                   | QMI8658                                                 |
| RTC                   | PCF85063                                                |

Pin assignments live in `include/pins.h` — verified against Waveshare's
official `user_config.h`.

## Scripts

- `scripts/fix_toolchain_path.py` — pre-build: trims the toolchain path so
  Arduino-ESP32 doesn't choke on long Windows-style paths.
- `scripts/upload_via_pi.py` — lets you `pio run -t upload` from a dev
  machine while the ESP32 is physically connected to the Pi (the Pi acts
  as a USB serial proxy).
- `scripts/create_stubs.py` — generates placeholder `.h` files for
  resources that are provisioned dynamically.
