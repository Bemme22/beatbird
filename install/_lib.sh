#!/usr/bin/env bash
# install/_lib.sh — common helpers sourced by every install/*.sh script.
#
# Contract: scripts are invoked as root by the Makefile with two env vars set:
#   REPO_DIR      absolute path to the repo root
#   PROFILE_YML   absolute path to the active profile (usually a symlink)
#
# Helpers:
#   pq KEY             → print profile value at dotted key (YAML + list index ok)
#   pq_bool KEY        → "true"/"false" for boolean flags
#   pq_or KEY DEFAULT  → pq with fallback
#   render_template SRC DST [KEY=VAL ...]
#   ensure_pkg PKG...  → apt-install only if missing
#   ensure_line FILE LINE
#   ensure_module_loaded MODULE  → load via /etc/modules (Trixie compat)
#   enable_service NAME
#   log_step "what we're doing"

set -euo pipefail

: "${REPO_DIR:?REPO_DIR must be set by the Makefile}"
: "${PROFILE_YML:?PROFILE_YML must be set by the Makefile}"

ETC_DIR=/etc/beatbird
SECRETS_DIR="$REPO_DIR/secrets"
BEATBIRD_USER="${BEATBIRD_USER:-$(stat -c '%U' "$REPO_DIR")}"
BEATBIRD_GROUP="${BEATBIRD_GROUP:-$(stat -c '%G' "$REPO_DIR")}"

# ─── Logging ─────────────────────────────────────────────────────────────────

log_step() { printf '  \033[1;36m→\033[0m %s\n' "$*"; }
log_ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
log_warn() { printf '  \033[1;33m!\033[0m %s\n' "$*"; }
log_err()  { printf '  \033[1;31m✗\033[0m %s\n' "$*" >&2; }

# ─── Profile queries ─────────────────────────────────────────────────────────
# Shell-out to Python instead of sourcing yq to avoid an extra system dep.

pq() {
  python3 - "$PROFILE_YML" "$1" <<'PY'
import sys, yaml
path = sys.argv[2].split('.')
try:
    with open(sys.argv[1]) as f:
        d = yaml.safe_load(f)
    for p in path:
        if isinstance(d, list):
            d = d[int(p)]
        elif d is None:
            print("", end=""); sys.exit(0)
        else:
            d = d.get(p)
    if d is None:
        print("", end="")
    elif isinstance(d, bool):
        print("true" if d else "false", end="")
    else:
        print(d, end="")
except (KeyError, IndexError, TypeError):
    print("", end="")
PY
}

pq_bool() {
  local v; v="$(pq "$1")"
  case "$v" in true|True|TRUE|yes|1) echo true;; *) echo false;; esac
}

pq_or() {
  local v; v="$(pq "$1")"
  [[ -z "$v" ]] && echo "$2" || echo "$v"
}

# ─── Template rendering ──────────────────────────────────────────────────────
# Simple {{ key }} substitution. Keys come from remaining args as KEY=VALUE.

render_template() {
  local src="$1" dst="$2"; shift 2
  local py_kv=()
  for kv in "$@"; do py_kv+=( "$kv" ); done

  python3 - "$src" "$dst" "${py_kv[@]}" <<'PY'
import sys, re, os
src, dst, *kvs = sys.argv[1:]
kv = dict(s.split('=', 1) for s in kvs)
with open(src) as f:
    text = f.read()
text = re.sub(r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}',
              lambda m: kv.get(m.group(1), m.group(0)), text)
os.makedirs(os.path.dirname(dst), exist_ok=True)
with open(dst, 'w') as f:
    f.write(text)
PY
}

# ─── apt helpers ─────────────────────────────────────────────────────────────

ensure_pkg() {
  local missing=()
  for p in "$@"; do
    dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p")
  done
  if ((${#missing[@]})); then
    log_step "apt install: ${missing[*]}"
    apt-get install -y -qq "${missing[@]}"
  fi
}

# ─── File helpers ────────────────────────────────────────────────────────────

ensure_line() {
  local file="$1" line="$2"
  [[ -f "$file" ]] || { echo "$line" > "$file"; return; }
  grep -qxF -- "$line" "$file" || echo "$line" >> "$file"
}

ensure_line_in_config_txt() {
  local line="$1"
  local target=/boot/firmware/config.txt
  [[ -f "$target" ]] || target=/boot/config.txt
  ensure_line "$target" "$line"
}

# ─── Module loading (Trixie compatibility) ───────────────────────────────────
# On Trixie (Debian 13), dtoverlay=snd-aloop doesn't reliably load the module.
# Ensure the module is listed in /etc/modules as a robust fallback that works
# on both Bookworm and Trixie.

ensure_module_loaded() {
  local module="$1"
  # Belt: dtoverlay in config.txt (works on Bookworm)
  ensure_line_in_config_txt "dtoverlay=${module}"
  # Suspenders: /etc/modules (works on Trixie where dtoverlay doesn't)
  ensure_line /etc/modules "$module"
  log_ok "module $module: dtoverlay + /etc/modules"
}

# ─── systemd helpers ─────────────────────────────────────────────────────────

enable_service() {
  systemctl daemon-reload
  systemctl enable --now "$1"
}

restart_if_active() {
  systemctl is-active --quiet "$1" && systemctl restart "$1" || true
}

# ─── /etc/beatbird ───────────────────────────────────────────────────────────

ensure_etc_beatbird() {
  install -d -m 755 "$ETC_DIR"
  install -d -m 750 -o "$BEATBIRD_USER" -g "$BEATBIRD_GROUP" "$ETC_DIR/ssl" 2>/dev/null || true
}

# Like enable_service but does NOT start immediately. Use for services that
# depend on hardware created by a dtoverlay (only loaded at boot) — they
# can't succeed on the first install run, but will work after reboot.
enable_service_at_boot() {
  systemctl daemon-reload
  systemctl enable "$1"
}
