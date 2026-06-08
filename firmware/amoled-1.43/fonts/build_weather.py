#!/usr/bin/env python3
"""
build_weather.py — Generate one LVGL font with the 7 weather glyphs we use,
from Erik Flowers' "Weather Icons" (SIL OFL). Clean filled/line icons that
sit next to the Inter weather block on the standby screen (the old dot-matrix
icons clashed with the Warm Funktional grotesk).

Run once on the dev/sim machine (needs Node.js / npx). Outputs:
    fonts/weathericons-regular-webfont.ttf   (downloaded, gitignored)
    src/ui/fonts/weather_icons.c             (generated, gitignored)

Glyph → State::WeatherIcon mapping (see screen_standby.cpp::weather_glyph):
    f00d day-sunny      WX_CLEAR
    f002 day-cloudy     WX_PARTLY
    f013 cloudy         WX_CLOUDY
    f014 fog            WX_FOG
    f019 rain           WX_RAIN
    f01b snow           WX_SNOW
    f01e thunderstorm   WX_THUNDER
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ASSET_URL = ("https://raw.githubusercontent.com/erikflowers/weather-icons/"
             "master/font/weathericons-regular-webfont.ttf")
SIZE = 40
BPP = "4"
# The seven private-use codepoints we render.
GLYPHS = "0xf00d,0xf002,0xf013,0xf014,0xf019,0xf01b,0xf01e"

HERE       = Path(__file__).parent.resolve()
FIRMWARE   = HERE.parent
TTF_PATH   = HERE / "weathericons-regular-webfont.ttf"
OUTPUT_DIR = FIRMWARE / "src" / "ui" / "fonts"


def log(msg: str) -> None:
    print(f"[weather] {msg}", flush=True)


def download() -> None:
    if TTF_PATH.exists():
        log("TTF already present"); return
    log(f"downloading {ASSET_URL}")
    try:
        with urllib.request.urlopen(ASSET_URL, timeout=60) as r:
            data = r.read()
    except Exception as e:
        log(f"ERROR: download failed: {e}"); sys.exit(1)
    TTF_PATH.write_bytes(data)
    log(f"  wrote {TTF_PATH.name} ({len(data)//1024} KB)")


def convert() -> None:
    out = OUTPUT_DIR / "weather_icons.c"
    log(f"converting {SIZE}px → {out.relative_to(FIRMWARE)}")
    cmd = [
        "npx", "--yes", "lv_font_conv@latest",
        "--font", str(TTF_PATH),
        "--size", str(SIZE),
        "--bpp", BPP,
        "--format", "lvgl",
        "--lv-include", "lvgl.h",
        "--no-compress",
        "-r", GLYPHS,
        "--output", str(out),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         shell=(os.name == "nt"))
    if res.returncode != 0:
        log(f"ERROR: lv_font_conv failed ({res.returncode})")
        if res.stdout: log("stdout: " + res.stdout)
        if res.stderr: log("stderr: " + res.stderr)
        sys.exit(res.returncode)
    kb = out.stat().st_size // 1024 if out.exists() else 0
    log(f"done — {out.relative_to(FIRMWARE)} ({kb} KB)")
    log("Ensure -DHAS_WEATHER_ICONS=1 in platformio.ini, then rebuild.")


def main() -> int:
    if not shutil.which("npx"):
        log("ERROR: npx not found (install Node.js)"); return 1
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if "--clean" in sys.argv:
        for p in (TTF_PATH, OUTPUT_DIR / "weather_icons.c"):
            if p.exists():
                p.unlink(); log(f"removed {p.name}")
        return 0
    download()
    convert()
    return 0


if __name__ == "__main__":
    sys.exit(main())
