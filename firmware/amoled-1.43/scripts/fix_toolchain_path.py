"""
PlatformIO pre-script: ensures the xtensa-esp-elf toolchain bin dir is in PATH.

The pioarduino platform installs framework-arduinoespressif32-libs 5.4.0 with
IDF 5.x headers (incl. quad_mode), but the platform.json spec version for the
toolchain may not match what PlatformIO's package resolver finds locally, so
the toolchain bin dir doesn't get prepended to PATH automatically.

This script adds it explicitly so the compiler is found by SCons.
"""
import os
Import("env")

toolchain_bin = os.path.join(
    os.path.expanduser("~"), ".platformio", "packages",
    "toolchain-xtensa-esp-elf", "bin"
)
if os.path.isdir(toolchain_bin):
    env.PrependENVPath("PATH", toolchain_bin)
