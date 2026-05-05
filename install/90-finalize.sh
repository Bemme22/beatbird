#!/usr/bin/env bash
# install/90-finalize.sh — final reboot prompt and sanity summary.

source "$(dirname "$0")/_lib.sh"

HOSTNAME_NEW="$(pq identity.hostname)"
FRIENDLY="$(pq identity.friendly_name)"

cat <<EOF

╭─────────────────────────────────────────────────────────────╮
│ BeatBird install complete.                                  │
│                                                             │
│  Speaker:    $FRIENDLY
│  Hostname:   $HOSTNAME_NEW.local
│                                                             │
│  A reboot is recommended to activate:                       │
│    - new config.txt overlays (soundcard, WiFi, BT)          │
│    - hostname change                                        │
│                                                             │
│  After reboot, verify with:                                 │
│    make status                                              │
│    journalctl -u beatbird-bridge -f                         │
╰─────────────────────────────────────────────────────────────╯
EOF
