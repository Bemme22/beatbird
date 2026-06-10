[Unit]
Description=go-librespot — Spotify Connect daemon
# time-sync.target: the Pi has no RTC, so the boot clock is wrong until NTP
# syncs. Spotify uses TLS — a wrong clock fails the cert check, so go-librespot
# would start, fail to reach an AP ("connection refused"), and need a manual
# restart. Waiting for the clock fixes that (requires systemd-time-wait-sync,
# enabled by install/40-go-librespot.sh).
After=network-online.target avahi-daemon.service sound.target time-sync.target
Wants=network-online.target avahi-daemon.service time-sync.target

[Service]
Type=simple
User={{ BEATBIRD_USER }}
# go-librespot reads config from ~/.config/go-librespot/config.yml automatically.
# No CLI flags needed — the config path is resolved from the service user's $HOME.
ExecStart={{ GLSP_BIN }}
Restart=always
RestartSec=5

# PrivateTmp=true BREAKS go-librespot (Unix sockets) — do NOT add it.
SupplementaryGroups=audio

[Install]
WantedBy=multi-user.target
