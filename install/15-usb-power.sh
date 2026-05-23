#!/usr/bin/env bash
# install/15-usb-power.sh — disable USB autosuspend for stability.
#
# Problem we saw multiple times: USB WiFi dongle drops randomly because
# the kernel power-manager auto-suspends "idle" devices. The dongle then
# fails to fully come back, the Pi vanishes from the LAN, and the user
# does a 30-minute "why isn't my speaker reachable" debug session.
#
# Two-pronged fix:
#  1. Kernel cmdline param `usbcore.autosuspend=-1` disables autosuspend
#     globally from the next boot.
#  2. udev rule forces power/control = on for any USB net interface that
#     enumerates after boot (covers hot-replug too).
#
# Both are idempotent + persistent in /boot/firmware and /etc/udev.

source "$(dirname "$0")/_lib.sh"

log_step "USB autosuspend off (cmdline.txt)"

CMDLINE=/boot/firmware/cmdline.txt
if [[ -f "$CMDLINE" ]]; then
  if ! grep -q "usbcore.autosuspend=-1" "$CMDLINE"; then
    # cmdline.txt is a single long line — append before any newline.
    sed -i 's/$/ usbcore.autosuspend=-1/' "$CMDLINE"
    log_ok "added usbcore.autosuspend=-1 to $CMDLINE (active after reboot)"
  else
    log_ok "cmdline.txt already has usbcore.autosuspend=-1"
  fi
else
  log_warn "$CMDLINE not found — skipping cmdline edit (not a Pi?)"
fi

log_step "USB power-control udev rule"

UDEV_RULE=/etc/udev/rules.d/40-beatbird-usb-power.rules
cat > "$UDEV_RULE" <<'EOF'
# Force USB power/control = on for any newly-enumerated USB net device.
# Catches WiFi dongles whether they were present at boot (caught by the
# kernel cmdline param) or hot-plugged later.
ACTION=="add", SUBSYSTEM=="usb", DRIVER=="usb", TEST=="power/control", ATTR{power/control}="on"
ACTION=="add", SUBSYSTEM=="net", DEVPATH=="*/usb*", RUN+="/bin/sh -c 'echo on > /sys$$DEVPATH/../power/control 2>/dev/null || true'"
EOF
chmod 644 "$UDEV_RULE"
udevadm control --reload-rules 2>/dev/null || true
log_ok "udev rule installed at $UDEV_RULE"

# Apply to currently-loaded USB devices right now too, so the user
# doesn't have to reboot just for this script to take effect.
log_step "Applying power/control=on to current USB devices"
for f in /sys/bus/usb/devices/*/power/control; do
  [[ -w "$f" ]] && echo on > "$f" 2>/dev/null
done
log_ok "current USB devices set to power/control=on"
