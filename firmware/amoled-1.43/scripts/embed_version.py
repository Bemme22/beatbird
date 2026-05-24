"""
PlatformIO extra script: embed the firmware version as a C macro.

Source of truth (in order of precedence):
  1. `BEATBIRD_FW_VERSION` env var (set by CI when building a tagged release)
  2. `git describe --tags --dirty --always` (local dev builds)
  3. literal "unknown" (no env var, no git)

The resulting macro `BEATBIRD_FW_VERSION` is consumed by:
  - main.cpp boot banner (so we see it in `pio device monitor`)
  - Proto::send_version() (so the bridge can read it over serial and
    avoid re-flashing the same version)
"""

import os
import subprocess

Import("env")  # noqa: F821


def _detect_version() -> str:
    env_version = os.environ.get("BEATBIRD_FW_VERSION", "").strip()
    if env_version:
        return env_version
    try:
        return subprocess.run(
            ["git", "describe", "--tags", "--dirty", "--always"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


VERSION = _detect_version()
print(f"BeatBird firmware version: {VERSION}")

# StringifyMacro wraps in escaped quotes so the C preprocessor sees a string
env.Append(CPPDEFINES=[("BEATBIRD_FW_VERSION", env.StringifyMacro(VERSION))])  # noqa: F821
