"""
PlatformIO extra script: flash ESP32 via Raspberry Pi over SSH.

Workflow:
  1. Stop beatbird-bridge on Pi (releases /dev/ttyACM0)
  2. SCP firmware.bin to Pi
  3. Flash with esptool on Pi
  4. Restart beatbird-bridge

Each speaker env must set `custom_pi_host = <ssh-hostname>` in
platformio.ini so the upload goes to the right Pi. Without it, the
script errors out — better a clear failure than silently flashing the
wrong speaker.

Usage:
  pio run -e <speaker> -t upload
"""

import subprocess
import sys
from pathlib import Path

Import("env")  # noqa: F821  (PlatformIO SCons global)

PI_PORT    = "/dev/ttyACM0"
FLASH_ADDR = "0x10000"
BAUD       = "921600"
CHIP       = "esp32s3"


def _resolve_pi_host():
    """Read custom_pi_host from the current [env:*] block. Error out if
    missing — silently picking a default is how you flash the wrong speaker."""
    try:
        host = env.GetProjectOption("custom_pi_host")  # noqa: F821
    except Exception:
        host = None
    if not host:
        env_name = env["PIOENV"]  # noqa: F821
        print(
            "\nERROR: 'custom_pi_host' not set for [env:%s] in platformio.ini."
            "\n       Add e.g.  custom_pi_host = Zipp2miniPi  to that env block."
            % env_name
        )
        sys.exit(2)
    return host


def _run(cmd, check=True):
    print(f"  >> {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False)
    if check and result.returncode != 0:
        print(f"ERROR: command failed (exit {result.returncode})")
        sys.exit(result.returncode)
    return result.returncode


def upload_via_pi(source, target, env):
    pi_host = _resolve_pi_host()
    firmware = Path(str(source[0]))
    print(f"\n=== BeatBird: Flash via Pi ({pi_host}) ===")
    print(f"    Env:      {env['PIOENV']}")
    print(f"    Firmware: {firmware.name}  ({firmware.stat().st_size // 1024} KB)\n")

    # 1. Stop bridge to free the serial port. Exit code is ignored: when the
    # bridge is mid-restart (e.g. after `make update`), systemctl returns
    # exit 1 with "Job canceled" — but the service IS stopped. If the port
    # is actually still busy, esptool in step 3 will surface a clear error.
    print("[1/4] Stopping beatbird-bridge...")
    _run(["ssh", pi_host, "sudo systemctl stop beatbird-bridge"], check=False)

    # 2. Transfer binary
    print("[2/4] Copying firmware to Pi...")
    _run(["scp", str(firmware), f"{pi_host}:/tmp/beatbird.bin"])

    # 3. Flash
    print("[3/4] Flashing ESP32...")
    flash_cmd = (
        f"python3 -m esptool --chip {CHIP} --port {PI_PORT} "
        f"--baud {BAUD} write_flash {FLASH_ADDR} /tmp/beatbird.bin"
    )
    rc = _run(["ssh", pi_host, flash_cmd], check=False)

    # 4. Always restart bridge, even if flash failed
    print("[4/4] Restarting beatbird-bridge...")
    _run(["ssh", pi_host, "sudo systemctl start beatbird-bridge"])

    if rc != 0:
        print("\nERROR: Flash failed — bridge restarted, check esptool output above.")
        sys.exit(rc)

    print("\n=== Flash complete ===\n")


env.Replace(UPLOADCMD=upload_via_pi)
