[Unit]
Description=BeatBird Web UI / diagnostics
After=beatbird-bridge.service
Wants=beatbird-bridge.service

[Service]
Type=simple
ExecStart={{ VENV }}/bin/uvicorn beatbird.webserver:app --host 0.0.0.0 --port {{ WEB_PORT }}
WorkingDirectory={{ REPO_DIR }}
Restart=always
RestartSec=5
User={{ BEATBIRD_USER }}

EnvironmentFile=/etc/beatbird/env
Environment=PYTHONUNBUFFERED=1

StandardOutput=journal
StandardError=journal
SyslogIdentifier=beatbird-web

[Install]
WantedBy=multi-user.target
