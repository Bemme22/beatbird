#!/usr/bin/env bash
# install/02-logging.sh — persistent journald + retention caps.
#
# Why: Trixie's default journald uses Storage=auto, which only persists if
# /var/log/journal/ exists. After a reboot or power-cycle, all bridge/WiFi
# logs are gone — exactly the moment we most want them. By creating the
# directory and capping disk use, we get post-mortem visibility for the
# "speaker vanished from LAN at 19:42" class of problem.

source "$(dirname "$0")/_lib.sh"

log_step "Persistent journald directory"
install -d -m 2755 -o root -g systemd-journal /var/log/journal
# systemd picks this up next restart — but flush+rotate makes it active now.
systemd-tmpfiles --create --prefix /var/log/journal >/dev/null 2>&1 || true

log_step "journald retention caps"
install -d /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/beatbird.conf <<'EOF'
# /etc/systemd/journald.conf.d/beatbird.conf — written by install/02-logging.sh
#
# Persistent storage with bounded disk use. SD-card-friendly — caps total
# journal size at 200 MB and prunes anything older than two weeks. Plenty
# of room for ~2-3 incidents worth of WiFi/bridge logs without filling the
# card or killing flash endurance.
[Journal]
Storage=persistent
SystemMaxUse=200M
SystemMaxFileSize=20M
MaxRetentionSec=2week
ForwardToSyslog=no
EOF

log_step "Reload journald"
systemctl restart systemd-journald
journalctl --flush 2>/dev/null || true
log_ok "journald is now persistent (cap 200 MB, retain 2 weeks)"
