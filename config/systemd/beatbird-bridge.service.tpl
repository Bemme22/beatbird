[Unit]
Description=BeatBird Bridge — Pi ↔ Display ↔ Home Assistant
Documentation=https://github.com/Bemme22/beatbird
After=network.target camilladsp.service go-librespot.service
Wants=camilladsp.service go-librespot.service

[Service]
Type=simple
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

# Light hardening (avoid PrivateTmp — breaks alsa device access)
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/var/lib/beatbird /etc/beatbird

[Install]
WantedBy=multi-user.target
