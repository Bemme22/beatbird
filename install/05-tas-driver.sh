#!/usr/bin/env bash
# install/05-tas-driver.sh — Sonocotta TAS58xx Kernelmodul + Overlay bauen.
# No-op für innomaker-amp-pro oder andere Treiber.

source "$(dirname "$0")/_lib.sh"

DRIVER="$(pq soundcard.driver)"
if [[ ! "$DRIVER" =~ ^louder-hat ]]; then
  log_step "TAS-Treiber für $DRIVER nicht benötigt — überspringe"
  exit 0
fi

if lsmod | grep -q '^tas58xx '; then
  log_ok "tas58xx bereits geladen"
  exit 0
fi
if modinfo snd-soc-tas58xx >/dev/null 2>&1; then
  log_step "tas58xx bereits gebaut — lade nur"
  modprobe snd-soc-tas58xx
  exit 0
fi

log_step "Build-Abhängigkeiten"
ensure_pkg "linux-headers-$(uname -r)" build-essential git

SRC=/opt/sonocotta-tas58xx-src
log_step "Sonocotta-Treiber holen → $SRC"
if [[ ! -d "$SRC/.git" ]]; then
  git clone https://github.com/sonocotta/tas5805m-driver-for-raspbian.git "$SRC"
else
  git -C "$SRC" pull --ff-only || true
fi

log_step "Kernel-Modul bauen"
cd "$SRC"
make clean
make all
make install
depmod -a

log_step "DT-Overlay kompilieren"
bash compile-overlay.sh

log_step "Modul laden"
modprobe snd-soc-tas58xx
lsmod | grep -q '^tas58xx ' && log_ok "tas58xx geladen" || {
  log_err "tas58xx wollte nicht — dmesg checken"; exit 1;
}
