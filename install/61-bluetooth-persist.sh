#!/usr/bin/env bash
# install/61-bluetooth-persist.sh — persist BlueZ bond state across reboots.
#
# overlayroot=tmpfs wipes /var/lib/bluetooth on every reboot, so a phone that
# was paired before the boot still has the bond key on its side, but the
# speaker has forgotten its half. BlueZ rejects the reconnect silently and
# the only fix is forget+repair on the phone. This script breaks that loop
# by bind-mounting a directory from the persistent (read-only-overlay)
# layer over /var/lib/bluetooth before bluetooth.service starts.
#
# Run from inside overlayroot-chroot if overlay is active — otherwise the
# mount unit lands in tmpfs and disappears on the next reboot, defeating
# the whole point. The script aborts with a hint if it detects it's
# running on the live overlay.

source "$(dirname "$0")/_lib.sh"

BT_ENABLED="$(pq_bool sources.bluetooth.enabled)"
if [[ "$BT_ENABLED" != "true" ]]; then
  log_step "Bluetooth disabled in profile — skipping persistence setup"
  exit 0
fi

# Refuse to run on the live overlay — anything we write to /etc or
# /var/lib goes into the tmpfs upper layer and dies on reboot. The
# Makefile bt-persist target wraps this in overlayroot-chroot.
ROOT_FSTYPE="$(findmnt -n -o FSTYPE / 2>/dev/null || true)"
if [[ "$ROOT_FSTYPE" == "overlay" ]]; then
  log_warn "running on live overlayroot=tmpfs — changes would not persist."
  log_warn "re-run via: sudo overlayroot-chroot make install-role ROLE=61-bluetooth-persist.sh"
  log_warn "  or simply: make bt-persist"
  exit 1
fi

PERSIST_DIR=/var/lib/beatbird-bt
MOUNT_UNIT=/etc/systemd/system/var-lib-bluetooth.mount

log_step "creating persistent BT state dir at $PERSIST_DIR"
mkdir -p "$PERSIST_DIR"
chown root:root "$PERSIST_DIR"
chmod 700 "$PERSIST_DIR"

# Seed the persistent dir from the existing /var/lib/bluetooth content on
# first run. After that we leave it alone — bonds saved into the
# bind-mounted location are the source of truth and we shouldn't overwrite
# them from whatever happens to be in the tmpfs overlay this boot.
if [[ -d /var/lib/bluetooth ]] && [[ -z "$(ls -A "$PERSIST_DIR" 2>/dev/null)" ]]; then
  log_step "seeding $PERSIST_DIR from existing /var/lib/bluetooth"
  cp -a /var/lib/bluetooth/. "$PERSIST_DIR/" 2>/dev/null || true
fi

# Systemd .mount unit. The unit name MUST match the mountpoint path with
# slashes replaced by dashes per systemd convention.
log_step "installing $MOUNT_UNIT"
cat > "$MOUNT_UNIT" <<EOF
[Unit]
Description=Persistent BlueZ bond state (bind over /var/lib/bluetooth)
# bluetooth.service has no PartOf= linking it to this mount, so the only
# thing tying ordering together is Before=. RequiresMountsFor pulls in
# the local FS where the source dir lives.
Before=bluetooth.service
After=local-fs.target
RequiresMountsFor=/var/lib
ConditionPathIsDirectory=$PERSIST_DIR

[Mount]
What=$PERSIST_DIR
Where=/var/lib/bluetooth
Type=none
Options=bind

[Install]
WantedBy=bluetooth.service
EOF
chmod 0644 "$MOUNT_UNIT"

# Create the WantedBy= symlink manually instead of via `systemctl enable`.
# When this script runs inside overlayroot-chroot (the supported path,
# since otherwise the files don't persist), systemctl reports
# is-system-running=offline and `enable` may refuse or no-op the dbus
# round-trip. `ln -s` works regardless and produces the exact same
# on-disk artifact systemctl would have created.
WANTS_DIR=/etc/systemd/system/bluetooth.service.wants
mkdir -p "$WANTS_DIR"
ln -sf "$MOUNT_UNIT" "$WANTS_DIR/var-lib-bluetooth.mount"

# daemon-reload is meaningless inside the chroot — the live PID 1
# isn't reachable here. It runs on next boot automatically. If the
# caller wants the mount active without a reboot, they need to:
#   systemctl daemon-reload && \
#     systemctl stop bluetooth bluealsa beatbird-bt-agent && \
#     systemctl start var-lib-bluetooth.mount && \
#     systemctl start bluetooth bluealsa beatbird-bt-agent

log_ok "BT state will persist at $PERSIST_DIR across reboots"
log_ok "mount unit installed at $MOUNT_UNIT — takes effect on next boot"
