#!/usr/bin/env bash
# install/05-tas-driver.sh — Sonocotta TAS58xx Kernelmodul + Overlay bauen.
# No-op für innomaker-amp-pro oder andere Treiber.

source "$(dirname "$0")/_lib.sh"

DRIVER="$(pq soundcard.driver)"
if [[ ! "$DRIVER" =~ ^louder-hat ]]; then
  log_step "TAS-Treiber für $DRIVER nicht benötigt — überspringe"
  exit 0
fi

IS_TRIPLE=false
[[ "$DRIVER" == "louder-hat-triple" ]] && IS_TRIPLE=true

# Fast-path skip for the single/dual stacks if the module is already there.
# The triple/TDM stack ALWAYS goes through the full path below, because it
# needs the out-of-tree TDM patch applied to the source before building — a
# stock-built module wouldn't have the slot support.
if [[ "$IS_TRIPLE" == false ]]; then
  if lsmod | grep -q '^tas58xx '; then
    log_ok "tas58xx bereits geladen"
    exit 0
  fi
  if modinfo snd-soc-tas58xx >/dev/null 2>&1; then
    log_step "tas58xx bereits gebaut — lade nur"
    modprobe snd-soc-tas58xx
    exit 0
  fi
fi

log_step "Build-Abhängigkeiten"
ensure_pkg "linux-headers-$(uname -r)" build-essential git device-tree-compiler

SRC=/opt/sonocotta-tas58xx-src
log_step "Sonocotta-Treiber holen → $SRC"
if [[ ! -d "$SRC/.git" ]]; then
  git clone https://github.com/sonocotta/tas5805m-driver-for-raspbian.git "$SRC"
else
  git -C "$SRC" pull --ff-only || true
fi

# ─── TDM multi-DAC patch (triple stack only) ─────────────────────────────────
# The stock driver is 2-channel. The Lounge triple stack drives 3 DACs as 6
# independent channels over one shared I2S line via TDM, which needs a driver
# change: per-codec `ti,tdm-slot-offset`, raised channel max, and the SAP/TDM
# register writes. Kept as a patch so we track upstream + can PR it back.
# Idempotent via the --reverse --check probe.
if [[ "$IS_TRIPLE" == true ]]; then
  PATCH="$REPO_DIR/install/patches/tas58xx-tdm-slots.patch"
  if [[ -f "$PATCH" ]]; then
    cd "$SRC"
    # Reset the working tree first so a CHANGED patch re-applies cleanly. The
    # source carries the previous patch un-committed; without the reset a stale
    # partial state makes both the reverse- and forward-check fail and the old
    # code gets built silently. Reset → apply makes the step idempotent.
    git checkout -- . 2>/dev/null || true
    # --recount: trust the +/-/context lines, not the @@ counts, so we can add
    # hunks to the patch without hand-fixing every header offset.
    if git apply --recount "$PATCH"; then
      log_ok "TDM patch angewendet"
    else
      log_warn "TDM patch passt nicht (Upstream geändert?) — manuell prüfen: $PATCH"
    fi
  else
    log_warn "TDM patch fehlt: $PATCH — triple stack braucht ihn"
  fi
fi

log_step "Kernel-Modul bauen"
cd "$SRC"
make clean
make all
make install
depmod -a

log_step "DT-Overlay kompilieren (single + dual aus Upstream)"
bash compile-overlay.sh

# ─── Triple TDM overlay (our own, not in upstream) ───────────────────────────
if [[ "$IS_TRIPLE" == true ]]; then
  TRIPLE_DTS="$REPO_DIR/install/overlays/tas58xx-triple-overlay.dts"
  # compile-overlay.sh writes to /boot/overlays; Trixie/Bookworm use
  # /boot/firmware/overlays. Prefer the firmware path if present.
  OVL_DIR=/boot/firmware/overlays
  [[ -d "$OVL_DIR" ]] || OVL_DIR=/boot/overlays
  if [[ -f "$TRIPLE_DTS" ]]; then
    log_step "Triple-TDM-Overlay kompilieren → $OVL_DIR/tas58xx-triple.dtbo"
    dtc -I dts -O dtb -W no-unit_address_vs_reg -W no-graph_child_address \
        -o "$OVL_DIR/tas58xx-triple.dtbo" "$TRIPLE_DTS"
    log_ok "tas58xx-triple.dtbo gebaut"
  else
    log_warn "Triple overlay dts fehlt: $TRIPLE_DTS"
  fi
fi

log_step "Modul laden"
modprobe snd-soc-tas58xx
lsmod | grep -q '^tas58xx ' && log_ok "tas58xx geladen" || {
  log_err "tas58xx wollte nicht — dmesg checken"; exit 1;
}
