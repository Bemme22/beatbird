"""
beatbird-firmware-update — pull-OTA for the AMOLED display firmware.

Resolves the active speaker env from the profile, queries GitHub releases
for the latest fw-v* tag, downloads the matching firmware-<env>.bin,
verifies its SHA-256, then flashes via esptool over /dev/ttyACM0 while
the bridge is paused.

Usage:
  beatbird-firmware-update                    # check + flash if newer
  beatbird-firmware-update --check            # report status only, no flash
  beatbird-firmware-update --force            # reflash even if version matches
  beatbird-firmware-update --tag fw-v1.2.3    # pin a specific release
  beatbird-firmware-update --bin /tmp/x.bin   # local file, skip GitHub

Designed so a non-root user can run --check (read-only, no port access),
but the actual flash needs sudo for `systemctl stop beatbird-bridge` and
write access to /dev/ttyACM0 (dialout group).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

REPO        = "Bemme22/beatbird"
PI_PORT     = "/dev/ttyACM0"
CHIP        = "esp32s3"
BAUD        = "921600"
FLASH_ADDR  = "0x10000"
STATE_FILE  = "/var/lib/beatbird/firmware-version"
CACHE_DIR   = "/var/lib/beatbird/firmware-cache"


def active_env() -> str:
    """Derive the firmware env name from the active profile.

    Production install reads BEATBIRD_PROFILE from /etc/beatbird/env; dev
    checkouts fall back to profiles/current.yml in the repo. The env name
    is just the profile filename without .yml — that's how platformio.ini
    keys them too.
    """
    try:
        with open("/etc/beatbird/env") as f:
            for line in f:
                if line.startswith("BEATBIRD_PROFILE="):
                    path = line.split("=", 1)[1].strip()
                    return os.path.splitext(os.path.basename(os.path.realpath(path)))[0]
    except OSError:
        pass
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cur = os.path.join(here, "profiles", "current.yml")
    if os.path.exists(cur):
        return os.path.splitext(os.path.basename(os.path.realpath(cur)))[0]
    raise SystemExit(
        "Cannot resolve active speaker env "
        "(no BEATBIRD_PROFILE in /etc/beatbird/env, no profiles/current.yml)"
    )


def current_version() -> str | None:
    try:
        with open(STATE_FILE) as f:
            return f.read().strip() or None
    except OSError:
        return None


def fetch_release(tag: str | None) -> dict:
    if tag:
        url = f"https://api.github.com/repos/{REPO}/releases/tags/{tag}"
    else:
        url = f"https://api.github.com/repos/{REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def find_assets(release: dict, env: str) -> tuple[dict, dict]:
    bin_name = f"firmware-{env}.bin"
    sha_name = f"firmware-{env}.bin.sha256"
    by_name = {a["name"]: a for a in release.get("assets", [])}
    if bin_name not in by_name or sha_name not in by_name:
        avail = ", ".join(sorted(by_name)) or "(none)"
        raise SystemExit(
            f"Release {release.get('tag_name')} missing {bin_name} or {sha_name}.\n"
            f"Available assets: {avail}"
        )
    return by_name[bin_name], by_name[sha_name]


def download(url: str, dst: str) -> None:
    req = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
    tmp = dst + ".part"
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(64 * 1024)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp, dst)


def verify_sha256(bin_path: str, sha_path: str) -> None:
    with open(sha_path) as f:
        expected = f.read().split()[0].strip().lower()
    h = hashlib.sha256()
    with open(bin_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    got = h.hexdigest()
    if got != expected:
        raise SystemExit(f"SHA-256 mismatch: expected {expected}, got {got}")


def flash(bin_path: str, dry_run: bool = False) -> None:
    print(f"[1/3] stopping beatbird-bridge to release {PI_PORT}")
    if not dry_run:
        subprocess.run(["sudo", "systemctl", "stop", "beatbird-bridge"], check=False)
        # 1s settle — systemctl returns once the stop signal is sent, but
        # the bridge's serial close lags slightly.
        time.sleep(1)

    print(f"[2/3] esptool write_flash {FLASH_ADDR} ({os.path.getsize(bin_path) // 1024} KB)")
    # sys.executable points at the venv's Python so we always get the esptool
    # that was installed into that venv (system python3 doesn't have it).
    cmd = [sys.executable, "-m", "esptool", "--chip", CHIP, "--port", PI_PORT,
           "--baud", BAUD, "write_flash", FLASH_ADDR, bin_path]
    if dry_run:
        print("  (dry run) would run:", " ".join(cmd))
        rc = 0
    else:
        rc = subprocess.run(cmd).returncode

    print("[3/3] starting beatbird-bridge")
    if not dry_run:
        subprocess.run(["sudo", "systemctl", "start", "beatbird-bridge"], check=False)
    if rc != 0:
        raise SystemExit(f"esptool failed (exit {rc}) — bridge restarted, see output above")


def _save_version(v: str) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(v + "\n")
    os.replace(tmp, STATE_FILE)


def main() -> int:
    ap = argparse.ArgumentParser(description="BeatBird firmware OTA updater")
    ap.add_argument("--check",   action="store_true", help="report status only, no flash")
    ap.add_argument("--force",   action="store_true", help="reflash even if version matches")
    ap.add_argument("--tag",     help="pin a specific fw-v* tag (default: latest)")
    ap.add_argument("--bin",     help="local .bin path, skip GitHub")
    ap.add_argument("--dry-run", action="store_true",
                    help="skip systemctl + esptool, print only")
    args = ap.parse_args()

    env = active_env()
    cur = current_version()
    print(f"speaker env:    {env}")
    print(f"running fw:     {cur or '(unknown)'}")

    if args.bin:
        if not os.path.exists(args.bin):
            raise SystemExit(f"--bin path does not exist: {args.bin}")
        print(f"local bin:      {args.bin}")
        if args.check:
            return 0
        flash(args.bin, dry_run=args.dry_run)
        if not args.dry_run:
            _save_version("local")
        return 0

    release = fetch_release(args.tag)
    latest_tag = release["tag_name"]
    print(f"latest release: {latest_tag}")

    if cur == latest_tag and not args.force:
        print("up to date — nothing to do (use --force to reflash)")
        return 0

    if args.check:
        print(f"update available: {cur or '?'} -> {latest_tag}")
        return 0

    bin_asset, sha_asset = find_assets(release, env)
    os.makedirs(CACHE_DIR, exist_ok=True)
    bin_path = os.path.join(CACHE_DIR, bin_asset["name"])
    sha_path = os.path.join(CACHE_DIR, sha_asset["name"])
    print(f"downloading {bin_asset['name']} ({bin_asset['size'] // 1024} KB)...")
    download(bin_asset["browser_download_url"], bin_path)
    download(sha_asset["browser_download_url"], sha_path)
    verify_sha256(bin_path, sha_path)
    print("sha256 ok")

    flash(bin_path, dry_run=args.dry_run)
    if not args.dry_run:
        _save_version(latest_tag)
    print(f"flashed {latest_tag} — bridge will reconnect and confirm via FW: line")
    return 0


if __name__ == "__main__":
    sys.exit(main())
