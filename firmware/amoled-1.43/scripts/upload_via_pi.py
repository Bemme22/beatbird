"""
PlatformIO extra script: flash ESP32 via Raspberry Pi over SSH.

Workflow:
  1. Stop beatbird-bridge on Pi (releases /dev/ttyACM0)
  2. SCP firmware.bin to Pi
  3. Flash with esptool on Pi
  4. Restart beatbird-bridge

Usage: pio run -t upload
"""

import subprocess
import sys
from pathlib import Path

Import("env")  # noqa: F821  (PlatformIO SCons global)

PI_HOST    = "zipp2minipi"
PI_PORT    = "/dev/ttyACM0"
FLASH_ADDR = "0x10000"
BAUD       = "921600"
CHIP       = "esp32s3"


def _run(cmd, check=True):
    print(f"  >> {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False)
    if check and result.returncode != 0:
        print(f"ERROR: command failed (exit {result.returncode})")
        sys.exit(result.returncode)
    return result.returncode


def upload_via_pi(source, target, env):
    firmware = Path(str(source[0]))
    print(f"\n=== BeatBird: Flash via Pi ({PI_HOST}) ===")
    print(f"    Firmware: {firmware.name}  ({firmware.stat().st_size // 1024} KB)\n")

    # 1. Stop bridge to free the serial port
    print("[1/4] Stopping beatbird-bridge...")
    _run(["ssh", PI_HOST, "sudo systemctl stop beatbird-bridge"])

    # 2. Transfer binary
    print("[2/4] Copying firmware to Pi...")
    _run(["scp", str(firmware), f"{PI_HOST}:/tmp/beatbird.bin"])

    # 3. Flash
    print("[3/4] Flashing ESP32...")
    flash_cmd = (
        f"python3 -m esptool --chip {CHIP} --port {PI_PORT} "
        f"--baud {BAUD} write_flash {FLASH_ADDR} /tmp/beatbird.bin"
    )
    rc = _run(["ssh", PI_HOST, flash_cmd], check=False)

    # 4. Always restart bridge, even if flash failed
    print("[4/4] Restarting beatbird-bridge...")
    _run(["ssh", PI_HOST, "sudo systemctl start beatbird-bridge"])

    if rc != 0:
        print("\nERROR: Flash failed — bridge restarted, check esptool output above.")
        sys.exit(rc)

    print("\n=== Flash complete ===\n")


env.Replace(UPLOADCMD=upload_via_pi)
