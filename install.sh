#!/usr/bin/env bash
# install.sh — One-shot bootstrap for a fresh Pi OS.
#
# Intended for first-time setup from a clean Bookworm Lite install. Installs
# the handful of tools we need to run the Makefile, clones the repo if not
# already present, then kicks off `make install`.
#
# Usage (on the fresh Pi):
#   curl -fsSL https://raw.githubusercontent.com/Bemme22/beatbird/main/install.sh | bash -s -- beat-1
#   # or, after cloning:
#   ./install.sh beat-1
#
# Safe to re-run.

set -euo pipefail

PROFILE="${1:-}"
REPO_URL="${BEATBIRD_REPO:-https://github.com/Bemme22/beatbird.git}"
REPO_DIR="${BEATBIRD_DIR:-$HOME/beatbird}"

die() { echo "ERROR: $*" >&2; exit 1; }

if [[ -z "$PROFILE" ]]; then
  echo "Usage: $0 <profile>"
  echo "  e.g. $0 beat-1"
  exit 1
fi

# 1. Base tools we need to drive the Makefile
echo "==> Installing bootstrap dependencies"
sudo apt-get update -qq
sudo apt-get install -y -qq git make python3-yaml ca-certificates

# 2. Clone the repo if it isn't already
if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "==> Cloning $REPO_URL → $REPO_DIR"
  git clone "$REPO_URL" "$REPO_DIR"
else
  echo "==> Repo exists at $REPO_DIR — pulling latest"
  git -C "$REPO_DIR" pull --ff-only || true
fi

cd "$REPO_DIR"

# 3. Activate profile
echo "==> Activating profile: $PROFILE"
make profile PROFILE="$PROFILE"

# 4. Secrets template
if [[ ! -f secrets/wifi.pass || ! -f secrets/mqtt.pass ]]; then
  make secrets
  cat <<EOF

==> Secrets templates created at $REPO_DIR/secrets/
    Edit them now, then re-run:    cd $REPO_DIR && make install

EOF
  exit 0
fi

# 5. Full install
echo "==> Running full install"
make install
