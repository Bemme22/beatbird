[Unit]
Description=go-librespot — Spotify Connect daemon
After=network-online.target avahi-daemon.service sound.target
Wants=network-online.target avahi-daemon.service

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
