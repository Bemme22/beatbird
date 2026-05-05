[Unit]
Description=go-librespot — Spotify Connect daemon
After=network-online.target avahi-daemon.service sound.target
Wants=network-online.target avahi-daemon.service

[Service]
Type=simple
User={{ BEATBIRD_USER }}
ExecStart={{ GLSP_BIN }} --config_path {{ GLSP_CONF }}
Restart=always
RestartSec=5

# PrivateTmp=true BREAKS go-librespot (Unix sockets) — do NOT add it.
SupplementaryGroups=audio

[Install]
WantedBy=multi-user.target
