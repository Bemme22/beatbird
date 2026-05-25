"""
test_cover_processor.py — eyeball the blur/darken/vignette pipeline.

Standalone runner that takes a URL or local file, runs the processor, and
writes both the input and the processed output to /tmp/ so you can flip
between them in an image viewer. Use to tune CoverProcessor parameters
without going through the full bridge → ESP32 round-trip.

Usage:
    python3 scripts/test_cover_processor.py URL_OR_FILE [--blur N] [--darken F]
                                            [--vignette F] [--quality N]
                                            [--size N] [--out DIR]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from beatbird.cover_processor import CoverProcessor


def main() -> int:
    ap = argparse.ArgumentParser(description="Test BeatBird's cover processor pipeline.")
    ap.add_argument("source", help="URL or path to a JPEG/PNG album cover")
    ap.add_argument("--blur",     type=float, default=12.0)
    ap.add_argument("--darken",   type=float, default=0.45,
                    help="0.0 = black, 1.0 = unchanged")
    ap.add_argument("--vignette", type=float, default=0.4,
                    help="0.0 = no vignette, 1.0 = edges become black")
    ap.add_argument("--quality",  type=int,   default=75)
    ap.add_argument("--size",     type=int,   default=466)
    ap.add_argument("--out",      type=Path,  default=Path("/tmp"))
    args = ap.parse_args()

    cp = CoverProcessor(
        output_size=args.size,
        blur_radius=args.blur,
        darken=args.darken,
        vignette_strength=args.vignette,
        jpeg_quality=args.quality,
    )

    # Load raw bytes — either via URL fetch or local file
    if args.source.startswith(("http://", "https://")):
        # Reuse CoverProcessor's downloader so we get the same code path
        cp._check_imports()
        raw = cp._download(args.source)
        if not raw:
            print("download failed", file=sys.stderr); return 1
        src_path = args.out / "cover_input.jpg"
        src_path.write_bytes(raw)
    else:
        src_path = Path(args.source)
        if not src_path.exists():
            print(f"file not found: {src_path}", file=sys.stderr); return 1
        raw = src_path.read_bytes()

    cp._check_imports()
    processed = cp._process(raw)

    out_path = args.out / "cover_output.jpg"
    out_path.write_bytes(processed)

    print(f"input:   {src_path}  ({len(raw)//1024} KB)")
    print(f"output:  {out_path}  ({len(processed)//1024} KB)")
    print(f"params:  blur={args.blur} darken={args.darken} "
          f"vignette={args.vignette} quality={args.quality} size={args.size}")
    print(f"\nOpen both in an image viewer side by side to compare.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
