#!/usr/bin/env bash
# install/03-zram.sh — compressed RAM swap (zram).
#
# The Pi Zero 2W has 464 MB RAM and, under overlayroot (read-only SD), no disk
# swap is possible — a swapfile would land on the tmpfs overlay (pointless) or
# wear/corrupt the card. So the kernel has no relief valve under memory
# pressure and the OOM-killer takes out whatever's biggest. On BeatBird that
# was go-librespot mid-track, which cascaded into a false standby that tore
# down the Spotify Connect session. zram gives a compressed in-RAM swap device
# (zstd ~3:1) so transient spikes page out instead of triggering an OOM.
#
# Gated on profile `zram` (default true). zram-tools ships `zramswap.service`
# reading /etc/default/zramswap.

source "$(dirname "$0")/_lib.sh"

if [[ "$(pq_or zram true)" != "true" ]]; then
  log_step "zram disabled in profile — skipping"
  systemctl disable --now zramswap.service 2>/dev/null || true
  exit 0
fi

log_step "zram swap (zstd)"
ensure_pkg zram-tools

# zram disk = 100 % of RAM (the standard low-RAM sizing). zstd compresses
# typical anon pages ~3:1, so a saturated device costs ~1/3 of RAM in real
# terms — enough of a relief valve to absorb the spikes that OOM-killed
# go-librespot, without over-committing and starving the working set (150 %+
# does on a 464 MB box). High PRIORITY so it's always preferred over any
# other swap.
CONF=/etc/default/zramswap
NEW_CONF="$(cat <<'EOF'
# Managed by install/03-zram.sh — edit the profile `zram` flag, not this file.
ALGO=zstd
PERCENT=100
PRIORITY=100
EOF
)"

# Only touch the device if the config actually changed. Restarting zramswap
# does a swapoff first, which pages everything back into RAM — risky on a
# box that's already under memory pressure (the very thing we're fixing). A
# fresh boot creates the device with no swap in use, so an unchanged config
# needs no live restart.
if [[ "$(cat "$CONF" 2>/dev/null)" != "$NEW_CONF" ]]; then
  printf '%s\n' "$NEW_CONF" > "$CONF"
  systemctl daemon-reload
  systemctl enable zramswap.service
  # Safe to (re)start now only if swap is near-empty; otherwise leave it for
  # the next boot to avoid a swapoff-under-pressure stall.
  used_kb="$(awk '/SwapTotal/{t=$2}/SwapFree/{f=$2}END{print (t-f)+0}' /proc/meminfo)"
  if [[ "${used_kb:-0}" -lt 20000 ]]; then
    systemctl restart zramswap.service || log_warn "zramswap restart failed"
  else
    log_warn "swap in use (${used_kb} KiB) — new size applies on next reboot"
  fi
else
  systemctl enable zramswap.service >/dev/null 2>&1 || true
fi

# Show what we ended up with (visible in `make install` output).
swapon --show 2>/dev/null || true
