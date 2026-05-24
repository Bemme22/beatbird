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
row([
    ("BEREIT WENN DU WILLST", [":flap BEREIT WENN DU WILLST"]),
    ("404 SOUND FEHLT",       [":flap 404 SOUND FEHLT"]),
    ("DJ HAT PAUSE",          [":flap DJ HAT PAUSE"]),
])

st.subheader("Boot sequence")
row([
    ("Boot progress lines", [":boot-progress"]),
])

st.subheader("Live palette swap")
col1, col2 = st.columns([1, 3])
hex_color = col1.color_picker("Accent", "#F0CB7B", label_visibility="collapsed")
if col2.button("Push palette", use_container_width=True):
    fire(f"palette {hex_color}", [f":palette {hex_color.lstrip('#').upper()}"])

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
