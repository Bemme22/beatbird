#!/usr/bin/env python3
"""
panel_drive.py — drive the `env:panel-diag` firmware over the speaker's Pi.

The diagnostic firmware takes single-letter commands on the serial line, but
the display ESP32 hangs off the Pi, not off a workstation. This runs on the Pi
(or over ssh) and saves retyping a serial terminal every time.

    sudo systemctl stop beatbird-bridge          # it owns /dev/ttyACM0
    python3 panel_drive.py b                     # fill black
    python3 panel_drive.py p                     # ruler + edge lines + ring
    python3 panel_drive.py g0 s                  # x_gap 0, then the ruler

⚠️ The ESP32-S3 has NATIVE USB-CDC and does NOT reset when the port is opened.
Anything the firmware prints at boot is therefore unreachable from here — do
not build a measurement that depends on catching it. (Two harnesses were
written against that assumption before it was noticed; one of them also called
reset_input_buffer(), which threw the boot output away a second time.)

────────────────────────────────────────────────────────────────────────────
MEASURING THE PANEL WINDOW  (open since 04.09.2026, do it with a good panel)
────────────────────────────────────────────────────────────────────────────
Two numbers are currently inherited or guessed rather than measured:

  * `x_gap = 6` in main.cpp came from the SH8601 and was carried over to the
    CO5300 unchanged. If it is wrong, every frame sits six columns off and the
    opposite edge is cropped — which matches the still-open note from
    04.09.2026 that "the outermost 8-px stripe is missing on the far side".
  * The frame-RAM wipe clears 512x512. 480 was measurably too small (the left
    crescent cleared, the bottom one did not); 512 works. Neither is a
    datasheet figure.

Procedure — needs eyes on the panel, roughly ten minutes:

  1. `p`  — the pattern draws 2-px edge lines (left RED, right GREEN, top BLUE,
     bottom YELLOW) and a ring at r=232. ⚠️ On a ROUND panel a 2-px edge column
     is only visible across ~43 px of height, so each line is a short arc, not
     a stripe. Look for arcs at 9/3/12/6 o'clock, and check the ring sits an
     even distance from the rim all the way round.
  2. `g0`, `g2`, `g4`, `g6`, `g8` — step x_gap and watch the LEFT arc appear
     and the RIGHT one disappear (or the reverse). The correct gap is the one
     where BOTH are visible and the ring is even.
  3. Repeat with `y<n>` if the top/bottom arcs misbehave.
  4. `m0`/`m90`/`m180`/`m270` — if an artefact stays on the same glass while
     the content rotates, it is the panel, not the addressing. That test is
     what proved the white sickle was hardware (04.09.2026); it belongs at the
     START of any panel diagnosis, not after a parameter sweep.

Whatever comes out replaces the inherited 6 and the guessed 512 in main.cpp.
"""
import argparse
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial missing — run with the bridge venv, "
             "e.g. /opt/beatbird/venv/bin/python panel_drive.py")

DEFAULT_PORT = "/dev/ttyACM0"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("cmd", nargs="*", help="diag commands, e.g. b / p / g6 / m180")
    ap.add_argument("--port", default=DEFAULT_PORT)
    ap.add_argument("--listen", type=float, default=2.0,
                    metavar="S", help="seconds to read back after sending")
    args = ap.parse_args()

    with serial.Serial(args.port, 115200, timeout=0.4) as s:
        time.sleep(1.0)
        s.reset_input_buffer()
        for c in args.cmd:
            s.write((c + "\n").encode())
            s.flush()
            time.sleep(0.5)
        if not args.cmd:
            s.write(b"?\n")          # no command: just ask for status
            s.flush()
        out, t0 = b"", time.time()
        while time.time() - t0 < args.listen:
            out += s.read(512)
    text = out.decode(errors="replace").strip()
    if text:
        print(text)
    else:
        # Fill commands print nothing — that is not a failure.
        print("(keine Ausgabe — Füllbefehle quittieren nicht; `?` zeigt den Status)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
