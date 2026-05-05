#!/usr/bin/env bash
# install/75-display.sh — dispatcher for display hardware setup.

source "$(dirname "$0")/_lib.sh"

DISPLAY_TYPE="$(pq display.type)"
if [[ -z "$DISPLAY_TYPE" || "$DISPLAY_TYPE" == "none" ]]; then
  log_step "No display configured — skipping"
  exit 0
fi

SUB_SCRIPT="$(dirname "$0")/75-display/${DISPLAY_TYPE}.sh"
if [[ ! -f "$SUB_SCRIPT" ]]; then
  log_err "Unknown display type: $DISPLAY_TYPE"
  exit 1
fi

log_step "Display: $DISPLAY_TYPE"
bash "$SUB_SCRIPT"
