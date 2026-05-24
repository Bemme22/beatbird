"""
sim_web.py — Streamlit control panel for the BeatBird LVGL/SDL simulator.

The sim binary listens on TCP :7777 for newline-separated commands — same
grammar as its stdin REPL (`:play`, `:offline`, raw protocol lines, …).
This Streamlit app gives those commands a clickable face you can drive from
any browser, including from Windows over the LAN while the sim itself stays
in the VNC session.

Run on the same host as the sim:

    ~/.pio-venv/bin/streamlit run scripts/sim_web.py \\
        --server.address 0.0.0.0 --server.port 8080

Then open  http://devstation.local:8080  from anywhere on the LAN.
"""

from __future__ import annotations

import socket
from datetime import datetime

import streamlit as st


SIM_HOST = "127.0.0.1"
SIM_PORT = 7777


# ─── TCP send helper ────────────────────────────────────────────────────────

def send_to_sim(lines: list[str]) -> tuple[bool, str]:
    """Returns (ok, message). Logged to session_state for the activity feed."""
    payload = ("\n".join(lines) + "\n").encode("ascii", "replace")
    try:
        with socket.create_connection((SIM_HOST, SIM_PORT), timeout=2.0) as s:
            s.sendall(payload)
        return True, " | ".join(lines)
    except OSError as e:
        return False, f"TCP {SIM_HOST}:{SIM_PORT} — {e}"


def fire(label: str, lines: list[str]) -> None:
    """Click handler — push to sim, append to the in-page activity log."""
    ok, msg = send_to_sim(lines)
    stamp = datetime.now().strftime("%H:%M:%S")
    icon = "→" if ok else "✗"
    st.session_state.log.insert(0, f"{stamp}  {icon} {label}: {msg}")
    st.session_state.log = st.session_state.log[:50]   # cap


# ─── Page setup ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="BeatBird Sim Control",
    page_icon="🐦",
    layout="centered",
)

if "log" not in st.session_state:
    st.session_state.log = []

st.title("BeatBird Sim Control")
st.caption(f"sim TCP target: `{SIM_HOST}:{SIM_PORT}`")


# ─── Button grid ────────────────────────────────────────────────────────────
# Each button: label → list of lines sent verbatim to the sim. The sim's
# stdin REPL grammar accepts `:scenario` shortcuts plus raw protocol lines.

def row(buttons: list[tuple[str, list[str]]]) -> None:
    cols = st.columns(len(buttons))
    for col, (label, lines) in zip(cols, buttons):
        if col.button(label, use_container_width=True, key=label):
            fire(label, lines)


st.subheader("Playback")
row([
    ("Play",       [":play"]),
    ("Pause",      [":pause"]),
    ("Stop",       [":stop"]),
    ("Next track", [":next"]),
])

st.subheader("Power state")
row([
    ("Standby", [":standby"]),
    ("Wake",    [":wake"]),
])

st.subheader("Connectivity")
row([
    ("Spotify offline", [":offline"]),
    ("Reconnecting",    [":reconnect"]),
    ("No network",      [":no-network"]),
    ("WiFi weak",       [":weak-wifi"]),
])
row([
    ("All healthy", [":healthy"]),
])

st.subheader("Sources")
row([
    ("Spotify",   [":play"]),
    ("Bluetooth", [":bluetooth"]),
    ("Snapcast",  [":snapcast"]),
])

st.subheader("Shutdown sequence")
row([
    ("Long-press warn", [":shutdown-warn"]),
    ("Shutting down",   [":shutdown"]),
])

st.subheader("Volume")
row([
    ("Mute (0)",   [":vol 0"]),
    ("Quiet (25)", [":vol 25"]),
    ("Mid (50)",   [":vol 50"]),
    ("Loud (75)",  [":vol 75"]),
    ("Max (100)",  [":vol 100"]),
])

st.subheader("Weather (standby icon)")
row([
    ("Clear",      [":wx-clear"]),
    ("Partly",     [":wx-partly"]),
    ("Cloudy",     [":wx-cloudy"]),
    ("Rain",       [":wx-rain"]),
    ("Snow",       [":wx-snow"]),
    ("Thunder",    [":wx-thunder"]),
])

st.subheader("Standby flap text")
# Field tested: round display clips at the right edge past ~17 chars at
# font_display_md. Keep samples ≤ 16 chars.
row([
    ("BEREIT WENN DU",  [":flap BEREIT WENN DU"]),
    ("404 SOUND FEHLT", [":flap 404 SOUND FEHLT"]),
    ("DJ HAT PAUSE",    [":flap DJ HAT PAUSE"]),
])
flap_col, flap_btn = st.columns([3, 1])
custom_flap = flap_col.text_input(
    "Custom flap", placeholder="Your own message (capped at 17 chars)",
    label_visibility="collapsed", max_chars=17,
)
if flap_btn.button("Push flap", use_container_width=True):
    if custom_flap.strip():
        fire(f"flap: {custom_flap}", [f":flap {custom_flap.strip().upper()}"])

## Boot sequence parked — needs sim-side support to load Boot screen back
## after the initial transition, and to reset State::app.connected_to_pi so
## the boot→player auto-transition doesn't fire instantly. Coming next.

st.subheader("Live palette swap")
# The firmware accepts an extended palette `PAL:a=…|g=…|d=…|p=…|s=…|e=…`
# with one slot per UI role:
#   a accent          — vol arc, source dot, flap text, etc.
#   g glow            — vol-dot highlight when wobbling on audio
#   d dim             — secondary accent uses (faded vol dots, dim source)
#   p text_primary    — clock, temp, track title (cream by default)
#   s text_secondary  — high/low row, condition label
#   e alert           — PI OFFLINE / NO NETWORK / SPOTIFY OFFLINE colour

# Defaults match firmware compile-time values.
PAL_DEFAULTS = {
    "a": "F0CB7B",
    "g": "FFE0A0",
    "d": "553F26",
    "p": "F0E5C8",
    "s": "8A7E5C",
    "e": "C73E2C",
}

def derive_from_accent(accent_hex: str) -> dict[str, str]:
    """Auto-fill the 5 non-accent slots from a picked accent.
    Convenience for 'I just want everything to shift one tone'."""
    h = accent_hex.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    def hx(rgb): return "%02X%02X%02X" % rgb
    def clamp(v): return max(0, min(255, int(v)))
    return {
        "a": h.upper(),
        "g": hx((clamp(r * 1.2 + 20), clamp(g * 1.2 + 20), clamp(b * 1.2 + 20))),
        "d": hx((clamp(r * 0.35),     clamp(g * 0.35),     clamp(b * 0.35))),
        "p": "F0E5C8",
        "s": "8A7E5C",
        "e": "C73E2C",
    }

# Initialise persistent slot values via session_state so the pickers keep
# their colour across reruns instead of resetting to default on every click.
for k, v in PAL_DEFAULTS.items():
    st.session_state.setdefault(f"pal_{k}", "#" + v)

c1, c2, c3 = st.columns(3)
c1.color_picker("Accent (a)",   key="pal_a")
c2.color_picker("Glow (g)",     key="pal_g")
c3.color_picker("Dim (d)",      key="pal_d")
c4, c5, c6 = st.columns(3)
c4.color_picker("Text primary (p)",   key="pal_p")
c5.color_picker("Text secondary (s)", key="pal_s")
c6.color_picker("Alert (e)",          key="pal_e")

bcol1, bcol2 = st.columns(2)
if bcol1.button("Derive from accent", use_container_width=True,
                help="Auto-fills glow/dim/text/alert based on the current accent."):
    derived = derive_from_accent(st.session_state["pal_a"])
    for k, v in derived.items():
        st.session_state[f"pal_{k}"] = "#" + v
    st.rerun()

if bcol2.button("Push palette", use_container_width=True, type="primary"):
    parts = "|".join(
        f"{k}={st.session_state[f'pal_{k}'].lstrip('#').upper()}"
        for k in PAL_DEFAULTS
    )
    fire("palette", [f"PAL:{parts}"])

st.subheader("Custom title")
t_col, b_col = st.columns([3, 1])
custom_title = t_col.text_input(
    "Custom title", placeholder="Title to test split-flap + scroll",
    label_visibility="collapsed",
)
if b_col.button("Push title", use_container_width=True):
    if custom_title.strip():
        fire(f"title: {custom_title}", [f":title {custom_title.strip()}"])

st.subheader("Stress tests")
row([
    ("Long-title track", [
        ":next",
        "ST:play|TI:Methodisch inkorrekt - Folge 220 Hip Hip Hurra Wissenschaft|"
        "AR:Mi220|SO:spotify|VO:42|PO:0|DU:6000000|LV:35|TM:14:32",
    ]),
])


# ─── Raw protocol line ──────────────────────────────────────────────────────

st.subheader("Raw protocol line")
raw = st.text_input(
    "Send anything the bridge would normally send",
    placeholder="ST:play|TI:Custom|AR:Test|SO:spotify|VO:42|PO:0|DU:200000|LV:20",
    label_visibility="collapsed",
)
if st.button("Send", type="primary"):
    if raw.strip():
        fire("raw", [raw.strip()])


# ─── Activity log ───────────────────────────────────────────────────────────

st.subheader("Activity")
if st.session_state.log:
    st.code("\n".join(st.session_state.log), language="text")
else:
    st.caption("No commands sent yet. Click a button above.")
