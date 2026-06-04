#!/usr/bin/env bash
# install/56-update-helper.sh — one-command, overlayroot-aware self-update.
#
# The pain this removes: on overlayroot=tmpfs speakers (Beat, Zipp) a normal
# `make update` can't run over a passwordless SSH session — it shells out to
# `sudo bash install/*.sh`, and devusr only has NOPASSWD for a fixed allowlist
# (systemctl verbs, overlayroot-chroot, journalctl). And a plain `git pull`
# lands in tmpfs → reverts on reboot.
#
# This installs `/usr/local/sbin/beatbird-update` (root helper) + a NOPASSWD
# sudoers rule, mirroring beatbird-persist-overrides / beatbird-bt-sync. Then a
# code deploy is just:
#     ssh <speaker> sudo beatbird-update          # default branch main
#     ssh <speaker> sudo beatbird-update <branch>
# It pulls the LIVE overlay repo (immediate, the bridge venv is an EDITABLE
# install so a restart picks the new code up), PERSISTS by pulling the read-only
# base repo through overlayroot-chroot (survives reboot), and restarts the
# repo-driven services. No-op chroot on a plain rw root (LoungePi).
#
# NOTE: this does NOT re-render the /etc CamillaDSP / go-librespot configs — for
# a config-affecting change run the full `make update` on the box. 95% of updates
# are code (bridge / web / templates), which this covers cleanly.

source "$(dirname "$0")/_lib.sh"

log_step "installing beatbird-update helper"
HELPER=/usr/local/sbin/beatbird-update
cat > "$HELPER" <<'HELP'
#!/bin/bash
# beatbird-update — overlayroot-aware self-update. Run as root (via sudo).
set -uo pipefail

BRANCH="${1:-main}"
REPO=/home/devusr/beatbird

# Branch names only — this runs as root, don't let an arg smuggle a shell.
if ! [[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "[beatbird-update] bad branch name: $BRANCH" >&2; exit 2
fi
if [ ! -d "$REPO/.git" ]; then
  echo "[beatbird-update] no repo at $REPO" >&2; exit 1
fi
OWNER="$(stat -c %U "$REPO" 2>/dev/null || echo devusr)"
log(){ echo "[beatbird-update] $*"; }

# Pull $REPO to origin/$BRANCH, fast-forward only. $1 = "owner" or "root" context
# (git refuses on dir it doesn't own → safe.directory; in chroot we're root).
ff_pull() {
  local pfx=""
  [ "$1" = owner ] && pfx="sudo -u $OWNER"
  $pfx git -C "$REPO" -c safe.directory="$REPO" fetch --quiet origin "$BRANCH" \
    && $pfx git -C "$REPO" -c safe.directory="$REPO" checkout --quiet "$BRANCH" \
    && $pfx git -C "$REPO" -c safe.directory="$REPO" merge --quiet --ff-only "origin/$BRANCH"
}

# 1) Live overlay repo → immediate effect once services restart.
log "live pull ($BRANCH)"
if ff_pull owner; then
  log "live now at $(sudo -u "$OWNER" git -C "$REPO" rev-parse --short HEAD)"
else
  log "ERROR: live pull failed (uncommitted changes? not ff?)"; exit 1
fi

# 2) Persist on overlayroot: pull the read-only base repo via the chroot.
if mount | grep -q "overlayroot on / "; then
  log "overlayroot → persisting base repo via chroot"
  # The chroot runs as root, so its git writes land root-owned in the base.
  # After the next reboot the tmpfs overlay is a fresh copy of that base →
  # the repo (and .git refs) come up root-owned and the *next* live ff-pull
  # (run as $OWNER) can't lock its refs. chown the tree back so the owner-
  # context git keeps working across reboots.
  if overlayroot-chroot bash -c "
        echo nameserver 1.1.1.1 > /etc/resolv.conf
        git -C '$REPO' -c safe.directory='$REPO' fetch --quiet origin '$BRANCH' \
          && git -C '$REPO' -c safe.directory='$REPO' checkout --quiet '$BRANCH' \
          && git -C '$REPO' -c safe.directory='$REPO' merge --quiet --ff-only 'origin/$BRANCH' \
          && chown -R '$OWNER:$OWNER' '$REPO'"; then
    log "base repo persisted (survives reboot)"
  else
    log "WARN: chroot persist failed — live update active but will revert on reboot"
  fi
else
  log "plain rw root — no chroot needed"
fi

# 3) Restart the services that run from the (editable) repo. CamillaDSP +
# go-librespot run separate binaries off rendered /etc configs (unchanged here),
# so they're left alone.
log "restarting beatbird-bridge + beatbird-web"
systemctl restart beatbird-bridge beatbird-web 2>/dev/null || true
log "done (branch=$BRANCH)"
HELP
chmod 0755 "$HELPER"
log_ok "wrote $HELPER"

log_step "installing sudoers rule for beatbird-update"
SUDOERS_FILE=/etc/sudoers.d/beatbird-update
cat > "$SUDOERS_FILE" <<EOF
# Passwordless overlayroot-aware self-update (install/56-update-helper.sh).
# Lets a deploy run over SSH: \`ssh <speaker> sudo beatbird-update [branch]\`.
# The helper itself only does git-ff-pull + overlayroot-chroot persist +
# systemctl restart of the beatbird services — all already individually NOPASSWD.
$BEATBIRD_USER ALL=(root) NOPASSWD: /usr/local/sbin/beatbird-update
$BEATBIRD_USER ALL=(root) NOPASSWD: /usr/local/sbin/beatbird-update *
EOF
chmod 0440 "$SUDOERS_FILE"
if ! visudo -cf "$SUDOERS_FILE" >/dev/null; then
  log_err "sudoers file failed validation, removing"
  rm -f "$SUDOERS_FILE"
  exit 1
fi
log_ok "sudoers rule installed at $SUDOERS_FILE"
