#!/usr/bin/env python3
"""
build.py — Generate LVGL C font files from Departure Mono OTF.

Run this once on your dev machine. Requires Python 3.8+ and Node.js (for npx).
After running, enable HAS_DEPARTURE_MONO in platformio.ini and rebuild.

Usage:
    python3 build.py            # download + convert (default)
    python3 build.py --no-download   # skip download, use existing OTF
    python3 build.py --clean    # remove generated artefacts

Outputs:
    fonts/DepartureMono-Regular.otf            (downloaded, gitignored)
    src/ui/fonts/departure_mono_<size>.c       (generated, gitignored)
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

# Departure Mono renders pixel-perfect only at multiples of 11px.
SIZES = [11, 22, 33, 44]

# Character coverage: ASCII printable + Latin-1 + common typographic punctuation.
# Keep small to limit flash footprint. Each size at this range is ~5–25 KB.
RANGES = "0x20-0x7E,0xA0-0xFF,0x2013-0x2014,0x2018-0x201D,0x2022,0x2026"

# Source release. Latest as of writing — bump if a newer version is wanted.
RELEASE_VERSION = "1.500"
ASSET_URL = (
    "https://github.com/rektdeckard/departure-mono/releases/download/"
    f"v{RELEASE_VERSION}/DepartureMono-{RELEASE_VERSION}.zip"
)
OTF_NAME_IN_ZIP = "DepartureMono-Regular.otf"

# ─── Paths ──────────────────────────────────────────────────────────────────

HERE       = Path(__file__).parent.resolve()
FIRMWARE   = HERE.parent
OTF_PATH   = HERE / "DepartureMono-Regular.otf"
OUTPUT_DIR = FIRMWARE / "src" / "ui" / "fonts"


# ─── Helpers ────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    print(f"[fonts] {msg}", flush=True)


def have_command(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def download_font() -> None:
    if OTF_PATH.exists():
        log(f"OTF already present: {OTF_PATH.name}")
        return

    log(f"downloading {ASSET_URL}")
    try:
        with urllib.request.urlopen(ASSET_URL, timeout=30) as resp:
            data = resp.read()
    except Exception as e:
        log(f"ERROR: download failed: {e}")
        log("Hint: download the zip manually from")
        log(f"  https://github.com/rektdeckard/departure-mono/releases/latest")
        log(f"and extract {OTF_NAME_IN_ZIP} to {OTF_PATH}")
        sys.exit(1)

    log(f"  got {len(data) // 1024} KB, extracting {OTF_NAME_IN_ZIP}")
    with zipfile.ZipFile(BytesIO(data)) as zf:
        # The zip nests the OTF inside one or more directories; find by basename.
        matches = [n for n in zf.namelist() if n.endswith(OTF_NAME_IN_ZIP)]
        if not matches:
            log(f"ERROR: {OTF_NAME_IN_ZIP} not found in zip")
            log("Zip contents:")
            for n in zf.namelist():
                log(f"  {n}")
            sys.exit(1)
        with zf.open(matches[0]) as src, open(OTF_PATH, "wb") as dst:
            shutil.copyfileobj(src, dst)
    log(f"  wrote {OTF_PATH}")


def convert_one(size: int) -> None:
    out = OUTPUT_DIR / f"departure_mono_{size}.c"
    log(f"converting size={size}px → {out.relative_to(FIRMWARE)}")

    cmd = [
        "npx", "--yes", "lv_font_conv@latest",
        "--font",   str(OTF_PATH),
        "--size",   str(size),
        "--bpp",    "1",                  # pixel font — no AA needed
        "--format", "lvgl",
        "--lv-include", "lvgl.h",
        "--no-compress",
        "-r",       RANGES,
        "--output", str(out),
    ]

    # On Windows, npx is npx.cmd. shutil.which finds it; otherwise rely on shell.
    use_shell = (os.name == "nt")
    result = subprocess.run(cmd, capture_output=True, text=True, shell=use_shell)
    if result.returncode != 0:
        log(f"ERROR: lv_font_conv failed (exit {result.returncode})")
        if result.stdout: log("stdout: " + result.stdout)
        if result.stderr: log("stderr: " + result.stderr)
        sys.exit(result.returncode)


def patch_generated(size: int) -> None:
    """lv_font_conv emits `LV_FONT_DECLARE(departure_mono_NN)` as the default
    symbol name. We want the file to register itself with const exposed via
    `extern const lv_font_t departure_mono_NN;` — which is what the lvgl format
    already does. No patching needed in normal cases, but we strip the
    `#if defined(LV_LVGL_H_INCLUDE_SIMPLE)` boilerplate that conflicts with
    our build flag setup.
    """
    out = OUTPUT_DIR / f"departure_mono_{size}.c"
    if not out.exists():
        return
    text = out.read_text(encoding="utf-8")
    # Defensive — only rewrite if needed
    if "lv_conf.h" in text and "#include \"lvgl.h\"" not in text:
        text = text.replace(
            "#include \"lv_conf.h\"",
            "#include \"lvgl.h\"",
        )
        out.write_text(text, encoding="utf-8")


def clean() -> None:
    if OTF_PATH.exists():
        OTF_PATH.unlink()
        log(f"removed {OTF_PATH.name}")
    if OUTPUT_DIR.exists():
        for f in OUTPUT_DIR.glob("departure_mono_*.c"):
            f.unlink()
            log(f"removed {f.relative_to(FIRMWARE)}")


# ─── Main ───────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-download", action="store_true",
                    help="use existing OTF instead of fetching")
    ap.add_argument("--clean", action="store_true",
                    help="remove downloaded OTF and generated .c files")
    args = ap.parse_args()

    if args.clean:
        clean()
        return 0

    if not have_command("npx"):
        log("ERROR: 'npx' not found in PATH. Install Node.js first.")
        log("       https://nodejs.org/  (LTS is fine)")
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not args.no_download:
        download_font()
    elif not OTF_PATH.exists():
        log(f"ERROR: --no-download set but {OTF_PATH} doesn't exist")
        return 1

    for size in SIZES:
        convert_one(size)
        patch_generated(size)

    log("done — generated:")
    for size in SIZES:
        f = OUTPUT_DIR / f"departure_mono_{size}.c"
        kb = f.stat().st_size // 1024 if f.exists() else 0
        log(f"  {f.relative_to(FIRMWARE)}  ({kb} KB)")

    log("")
    log("Next step: enable in platformio.ini by uncommenting the build flag:")
    log("    -DHAS_DEPARTURE_MONO=1")
    log("Then `pio run -t upload`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
