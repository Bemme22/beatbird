#!/usr/bin/env bash
# install/61-bluetooth-persist.sh — persist BlueZ bond state across reboots
# despite overlayroot=tmpfs wiping /var/lib/bluetooth.
#
# Background: a previous version of this script tried to bind-mount a
# "persistent" subdir over /var/lib/bluetooth. That didn't work — the
# subdir lived on / which is itself the overlay, so writes still landed
# in the tmpfs upper layer and died on reboot. Proof at the time: the
# on-disk file via overlayroot-chroot read Trusted=false while
# bluetoothctl info reported Trusted=true. Two views of the same path,
# different content, both backed by the same overlay.
#
# This version uses an explicit sync mechanism instead. The bridge calls
# /usr/local/sbin/beatbird-bt-sync after every successful pair / trust
# event; that script tars the current /var/lib/bluetooth (the overlay
# view) through overlayroot-chroot (which remounts the underlying disk
# RW). On the next boot the overlay's lower layer carries the synced
# state and bluetoothd accepts incoming reconnects without forget+repair.
#
# Must run from inside overlayroot-chroot if overlay is active — same
# reasoning as before, the script writes to /etc and /usr/local/sbin
# which would otherwise land in tmpfs.

source "$(dirname "$0")/_lib.sh"

BT_ENABLED="$(pq_bool sources.bluetooth.enabled)"
if [[ "$BT_ENABLED" != "true" ]]; then
  log_step "Bluetooth disabled in profile — skipping persistence setup"
  exit 0
fi

ROOT_FSTYPE="$(findmnt -n -o FSTYPE / 2>/dev/null || true)"
if [[ "$ROOT_FSTYPE" == "overlay" ]]; then
  log_warn "running on live overlayroot=tmpfs — changes would not persist."
  log_warn "re-run via: make bt-persist (wraps in overlayroot-chroot)"
  exit 1
fi

SYNC_SCRIPT=/usr/local/sbin/beatbird-bt-sync
SUDOERS_FILE=/etc/sudoers.d/beatbird-bt-sync

# ── Clean up the broken bind-mount artefacts from the v1 of this script ──
# The mount unit + WantedBy symlink were created in the lower layer
# (persistent), so we have to scrub them through chroot too. They'll
# still be present on existing deploys until this runs.
OLD_MOUNT_UNIT=/etc/systemd/system/var-lib-bluetooth.mount
OLD_WANTS_LINK=/etc/systemd/system/bluetooth.service.wants/var-lib-bluetooth.mount
OLD_PERSIST_DIR=/var/lib/beatbird-bt
if [[ -e "$OLD_MOUNT_UNIT" || -L "$OLD_WANTS_LINK" ]]; then
  log_step "removing stale bind-mount artefacts (v1 of bt-persist)"
  rm -f "$OLD_MOUNT_UNIT" "$OLD_WANTS_LINK"
fi
if [[ -d "$OLD_PERSIST_DIR" ]]; then
  log_step "removing $OLD_PERSIST_DIR (no longer used; bonds now live in /var/lib/bluetooth on disk)"
  # Best-effort: data here is the stale seed, not the live state. Live
  # state was always in the overlay /var/lib/bluetooth which we sync
  # explicitly below.
  rm -rf "$OLD_PERSIST_DIR"
fi

# ── Seed /var/lib/bluetooth on the disk with the current bond data ──
# We're inside overlayroot-chroot here, so /var/lib/bluetooth is the
# real disk path (lower layer of the runtime overlay). Bonds that
# bluetoothd wrote in the overlay tmpfs aren't visible to us here, but
# whatever's on disk (from a previous install or prior sync) we leave
# alone. The bridge will sync the live state after pair events.
mkdir -p /var/lib/bluetooth
chown root:root /var/lib/bluetooth
chmod 700 /var/lib/bluetooth

# ── Install the sync script ──
log_step "installing $SYNC_SCRIPT"
cat > "$SYNC_SCRIPT" <<'SYNC_EOF'
#!/bin/bash
# beatbird-bt-sync — write live /var/lib/bluetooth to the persistent
# layer so BlueZ bonds survive overlayroot=tmpfs reboots.
#
# Mechanism: tar the current overlay view of /var/lib/bluetooth and
# extract it inside overlayroot-chroot, which remounts the underlying
# disk read/write for the duration of the call. On the next boot, the
# overlay shows the lower layer (= what we just wrote) and bluetoothd
# accepts the phone's reconnect without requiring forget+repair.
#
# Invoked by the bridge via sudo (NOPASSWD entry installed alongside
# this script). Safe to run at any time — bluez survives concurrent
# read/write on its state files.
set -e

RUNTIME=/var/lib/bluetooth
if [[ ! -d "$RUNTIME" ]]; then
  echo "no $RUNTIME, nothing to sync" >&2
  exit 0
fi

# Empty dir is fine — we still create the destination shell. Use tar's
# --no-recursion + . pattern when empty so we don't accidentally pass
# an empty file set to the extract side.
tar -c -C "$RUNTIME" . 2>/dev/null \
  | overlayroot-chroot bash -c '
      set -e
      mkdir -p /var/lib/bluetooth
      chown root:root /var/lib/bluetooth
      chmod 700 /var/lib/bluetooth
      cd /var/lib/bluetooth
      # --overwrite so existing bond files are replaced with the
      # current (newer) versions from the overlay.
      tar -x --overwrite 2>/dev/null
    '

echo "beatbird-bt-sync: persisted $(find "$RUNTIME" -mindepth 1 | wc -l) entries to disk"
SYNC_EOF
chmod 0755 "$SYNC_SCRIPT"

# ── Sudoers entry: bridge user can trigger sync without password ──
# Restricted to this exact script path so it can't be repurposed for
# arbitrary commands. The script itself only operates on
# /var/lib/bluetooth via overlayroot-chroot, which we already trust the
# bridge user with (separate NOPASSWD entry in beatbird-overlay).
log_step "installing $SUDOERS_FILE"
cat > "$SUDOERS_FILE" <<EOF
# Auto-generated by install/61-bluetooth-persist.sh — let the bridge
# user trigger BlueZ state persistence without a password prompt. The
# script is fixed and only touches /var/lib/bluetooth via chroot.
$BEATBIRD_USER ALL=(root) NOPASSWD: $SYNC_SCRIPT
EOF
chmod 0440 "$SUDOERS_FILE"
if ! visudo -cf "$SUDOERS_FILE" >/dev/null; then
  log_warn "sudoers file failed validation, removing"
  rm -f "$SUDOERS_FILE"
  exit 1
fi

log_ok "BT state sync script installed at $SYNC_SCRIPT"
log_ok "bridge will call it via 'sudo $SYNC_SCRIPT' after pair/trust events"
