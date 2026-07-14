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

# zram disk = 150 % of RAM. zstd compresses typical anon pages ~3:1, so the
# real RAM cost of a full device is well under that 150 %, while giving the
# kernel enough headroom to page out spikes instead of OOM-killing. High
# PRIORITY so it's always preferred over any other swap.
cat > /etc/default/zramswap <<'EOF'
# Managed by install/03-zram.sh — edit the profile `zram` flag, not this file.
ALGO=zstd
PERCENT=150
PRIORITY=100
EOF

enable_service zramswap.service

# Show what we ended up with (visible in `make install` output).
swapon --show 2>/dev/null || true
