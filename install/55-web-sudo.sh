#!/usr/bin/env bash
# install/55-web-sudo.sh — sudoers rule for the web UI's system buttons.
#
# Hardware-side power button (45-power-button.sh) granted /sbin/poweroff
# unconditionally; the web UI needs a few more verbs for its Restart /
# Reboot / Shutdown panels:
#   - systemctl restart|start|stop on the beatbird service triad +
#     snapclient (no other units; allowlisted server-side too).
#   - systemctl reboot
#   - systemctl poweroff
#
# Only installed when web.enabled is true in the profile.

source "$(dirname "$0")/_lib.sh"

WEB_ENABLED="$(pq_bool web.enabled)"

if [[ "$WEB_ENABLED" != "true" ]]; then
  log_step "web disabled in profile, skipping sudoers rule"
  exit 0
fi

log_step "installing sudoers rule for web UI system buttons"

SUDOERS_FILE=/etc/sudoers.d/beatbird-web
cat > "$SUDOERS_FILE" <<EOF
# Allow the beatbird web service to restart/stop/start the beatbird
# service triad and to reboot/shutdown the Pi, all without a password.
# Used by src/beatbird/webserver.py /api/service and /api/system handlers.
# The web app already gates these by an allowlist; this just removes the
# password prompt so the buttons work over plain HTTP.
$BEATBIRD_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart beatbird-bridge
$BEATBIRD_USER ALL=(root) NOPASSWD: /usr/bin/systemctl start beatbird-bridge
$BEATBIRD_USER ALL=(root) NOPASSWD: /usr/bin/systemctl stop beatbird-bridge
$BEATBIRD_USER ALL=(root) NOPASSWD: /usr/bin/systemctl reload camilladsp
$BEATBIRD_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart camilladsp
$BEATBIRD_USER ALL=(root) NOPASSWD: /usr/bin/systemctl start camilladsp
$BEATBIRD_USER ALL=(root) NOPASSWD: /usr/bin/systemctl stop camilladsp
$BEATBIRD_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart go-librespot
$BEATBIRD_USER ALL=(root) NOPASSWD: /usr/bin/systemctl start go-librespot
$BEATBIRD_USER ALL=(root) NOPASSWD: /usr/bin/systemctl stop go-librespot
$BEATBIRD_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart snapclient
$BEATBIRD_USER ALL=(root) NOPASSWD: /usr/bin/systemctl start snapclient
$BEATBIRD_USER ALL=(root) NOPASSWD: /usr/bin/systemctl stop snapclient
$BEATBIRD_USER ALL=(root) NOPASSWD: /usr/bin/systemctl reboot
$BEATBIRD_USER ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff
EOF
chmod 0440 "$SUDOERS_FILE"

if ! visudo -cf "$SUDOERS_FILE" >/dev/null; then
  log_err "sudoers file failed validation, removing"
  rm -f "$SUDOERS_FILE"
  exit 1
fi

log_ok "sudoers rule installed at $SUDOERS_FILE"
