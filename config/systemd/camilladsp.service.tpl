[Unit]
Description=CamillaDSP audio processor
After=sound.target network.target louder-hat-init.service
Wants=sound.target

[Service]
Type=simple
User={{ BEATBIRD_USER }}
Group={{ BEATBIRD_GROUP }}
ExecStart=/usr/local/bin/camilladsp \
    -s /var/lib/camilladsp/camilladsp-state.yml \
    -p 1234 \
    -a 0.0.0.0 \
    -l warn \
    -g 0 \
    /etc/camilladsp/config.yml
ExecReload=/bin/kill -HUP $MAINPID
Restart=always
RestartSec=3

SupplementaryGroups=audio

[Install]
WantedBy=multi-user.target
