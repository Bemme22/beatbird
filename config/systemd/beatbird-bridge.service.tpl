[Unit]
Description=BeatBird Bridge — Pi ↔ Display ↔ Home Assistant
Documentation=https://github.com/Bemme22/beatbird
After=network.target camilladsp.service go-librespot.service
Wants=camilladsp.service go-librespot.service

[Service]
# Type=notify so we can use sd_notify-based liveness — the bridge pings
# WATCHDOG=1 from its main loop. If 90 s pass without a ping (process
# hung, deadlocked, blocked on a stuck socket) systemd kills + restarts
# it. Catches the "alive but doing nothing" failure mode that the plain
# Restart=on-failure path misses.
Type=notify
NotifyAccess=main
WatchdogSec=90
ExecStart={{ VENV }}/bin/python -m beatbird.bridge
WorkingDirectory={{ REPO_DIR }}
Restart=always
RestartSec=5
User={{ BEATBIRD_USER }}

# Profile + secrets come from here
EnvironmentFile=/etc/beatbird/env
Environment=PYTHONUNBUFFERED=1
# RPi.GPIO on Trixie wraps lgpio, which writes notification pipes to CWD by
# default. ProtectSystem=strict locks everything except ReadWritePaths;
# point lgpio at one of those. (power_button.py also chdirs as a fallback.)
Environment=LG_WD=/var/lib/beatbird

# Serial (dialout), I²C (i2c), GPIO (gpio), audio (audio)
SupplementaryGroups=dialout i2c gpio audio

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=beatbird-bridge

# Light hardening (avoid PrivateTmp — breaks alsa device access).
#
# NoNewPrivileges intentionally NOT set: the bridge needs to call sudo
# for two narrowly-scoped, NOPASSWD-restricted commands —
#   - /usr/local/sbin/beatbird-bt-sync (BlueZ state → disk under
#     overlayroot=tmpfs, see install/61-bluetooth-persist.sh)
#   - /usr/sbin/overlayroot-chroot (used elsewhere for persistence)
# Both are listed by full path in /etc/sudoers.d/beatbird-overlay so the
# bridge can't repurpose sudo for arbitrary commands. Enabling
# NoNewPrivileges would break the sudo call with "no new privileges
# flag is set, which prevents sudo from running as root".
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/var/lib/beatbird /etc/beatbird

[Install]
WantedBy=multi-user.target
