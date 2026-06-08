#!/usr/bin/env python3
"""
build_inter.py — Generate LVGL C font files from Inter (the "Warm Funktional"
display language). Proportional grotesk, anti-aliased (bpp 4), unlike the
pixel-perfect Departure Mono (bpp 1) handled by build.py.

Run once on the dev/sim machine. Requires Python 3.8+ and Node.js (npx).
After running, enable HAS_INTER in platformio.ini and rebuild.

Usage:
    python3 build_inter.py            # download + convert (default)
    python3 build_inter.py --no-download
    python3 build_inter.py --clean

Outputs:
    fonts/Inter-<Weight>.ttf          (downloaded, gitignored)
    src/ui/fonts/inter_<name>.c       (generated, gitignored)

Type scale (device px on the 466 panel):
    inter_clock  140  ExtraBold  digits+colon subset   — standby clock
    inter_title   40  ExtraBold  full Latin-1          — now-playing title
    inter_lg      30  SemiBold   full Latin-1          — weather temp, big labels
    inter_md      21  Medium     full Latin-1          — artist, body
    inter_sm      14  SemiBold   full Latin-1          — tracked labels, date, time
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path

# ─── Configuration ──────────────────────────────────────────────────────────

# Inter static release (OFL). Bump if a newer version is wanted.
RELEASE_VERSION = "4.1"
ASSET_URL = (
    "https://github.com/rsms/inter/releases/download/"
    f"v{RELEASE_VERSION}/Inter-{RELEASE_VERSION}.zip"
)

# Weight name → static TTF basename inside the release zip (extras/ttf/).
WEIGHT_FILE = {
    "ExtraBold": "Inter-ExtraBold.ttf",
    "SemiBold":  "Inter-SemiBold.ttf",
    "Medium":    "Inter-Medium.ttf",
}

# ASCII printable + Latin-1 (German umlauts live here) + common punctuation.
RANGES = "0x20-0x7E,0xA0-0xFF,0x2013-0x2014,0x2018-0x201D,0x2022,0x2026"

# Clock only needs digits, colon, a space (and °/. for safety). Subsetting keeps
# the 140 px face tiny despite the size.
CLOCK_SYMBOLS = "0123456789:. °"

# name, weight, size, symbols(optional → subset), ranges(optional → full)
INSTANCES = [
    ("inter_clock", "ExtraBold", 108, CLOCK_SYMBOLS, None),
    ("inter_title", "ExtraBold",  40, None,          RANGES),
    ("inter_lg",    "SemiBold",   30, None,          RANGES),
    ("inter_md",    "Medium",     21, None,          RANGES),
    ("inter_sm",    "SemiBold",   14, None,          RANGES),
]

BPP = "4"   # anti-aliased

# ─── Paths ──────────────────────────────────────────────────────────────────

HERE       = Path(__file__).parent.resolve()
FIRMWARE   = HERE.parent
OUTPUT_DIR = FIRMWARE / "src" / "ui" / "fonts"


def log(msg: str) -> None:
    print(f"[inter] {msg}", flush=True)


def have_command(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def ttf_path(weight: str) -> Path:
    return HERE / WEIGHT_FILE[weight]


def download_fonts() -> None:
    needed = {w: ttf_path(w) for w in WEIGHT_FILE}
    if all(p.exists() for p in needed.values()):
        log("all Inter weights already present")
        return

    log(f"downloading {ASSET_URL}")
    try:
        with urllib.request.urlopen(ASSET_URL, timeout=60) as resp:
            data = resp.read()
    except Exception as e:
        log(f"ERROR: download failed: {e}")
        log("Hint: grab Inter manually from https://github.com/rsms/inter/releases")
        sys.exit(1)
    log(f"  got {len(data) // 1024} KB")

    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        for weight, basename in WEIGHT_FILE.items():
            dst = ttf_path(weight)
            if dst.exists():
                continue
            matches = [n for n in names if Path(n).name == basename]
            if not matches:
                log(f"ERROR: {basename} not found in zip. Contents (ttf):")
                for n in names:
                    if n.lower().endswith(".ttf"):
                        log(f"  {n}")
                sys.exit(1)
            with zf.open(matches[0]) as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)
            log(f"  extracted {dst.name}")


def convert_one(name: str, weight: str, size: int, symbols, ranges) -> None:
    out = OUTPUT_DIR / f"{name}.c"
    log(f"converting {name}  {weight} {size}px → {out.relative_to(FIRMWARE)}")
    cmd = [
        "npx", "--yes", "lv_font_conv@latest",
        "--font",   str(ttf_path(weight)),
        "--size",   str(size),
        "--bpp",    BPP,
        "--format", "lvgl",
        "--lv-include", "lvgl.h",
        "--no-compress",
        "--output", str(out),
    ]
    if symbols:
        cmd += ["--symbols", symbols]
    else:
        cmd += ["-r", ranges]

    use_shell = (os.name == "nt")
    result = subprocess.run(cmd, capture_output=True, text=True, shell=use_shell)
    if result.returncode != 0:
        log(f"ERROR: lv_font_conv failed (exit {result.returncode})")
        if result.stdout: log("stdout: " + result.stdout)
        if result.stderr: log("stderr: " + result.stderr)
        sys.exit(result.returncode)


def clean() -> None:
    for w in WEIGHT_FILE:
        p = ttf_path(w)
        if p.exists():
            p.unlink(); log(f"removed {p.name}")
    if OUTPUT_DIR.exists():
        for f in OUTPUT_DIR.glob("inter_*.c"):
            f.unlink(); log(f"removed {f.relative_to(FIRMWARE)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args()

    if args.clean:
        clean(); return 0

    if not have_command("npx"):
        log("ERROR: 'npx' not found. Install Node.js (LTS).")
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not args.no_download:
        download_fonts()
    elif not all(ttf_path(w).exists() for w in WEIGHT_FILE):
        log("ERROR: --no-download set but Inter weights missing")
        return 1

    for name, weight, size, symbols, ranges in INSTANCES:
        convert_one(name, weight, size, symbols, ranges)

    log("done — generated:")
    for name, *_ in INSTANCES:
        f = OUTPUT_DIR / f"{name}.c"
        kb = f.stat().st_size // 1024 if f.exists() else 0
        log(f"  {f.relative_to(FIRMWARE)}  ({kb} KB)")
    log("")
    log("Next: ensure -DHAS_INTER=1 is set in platformio.ini, then rebuild.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
