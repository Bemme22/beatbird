"""
sim_web.py — tiny HTTP control panel for the BeatBird LVGL/SDL simulator.

The sim binary (firmware/amoled-1.43/.pio/build/sim/program) listens on TCP
:7777 for newline-separated commands — same grammar as its stdin REPL.
This script serves a one-page HTML on :8080 with buttons that POST a scenario
name; on POST, we open a TCP connection to the sim and write the matching
protocol line.

Run on the same host as the sim (so localhost:7777 just works), then point
any browser at http://<that-host>:8080 — including from Windows over the LAN.

  python3 scripts/sim_web.py
  python3 scripts/sim_web.py --sim-host devstation --sim-port 7777 --port 8080

stdlib only, no FastAPI dependency — keeps it runnable on a fresh dev box.
"""

from __future__ import annotations

import argparse
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


# ─── Scenario catalogue ─────────────────────────────────────────────────────
# Each entry: button label → list of newline-separated lines to send.
# Multi-line is just convenience — same effect as several single-line clicks.

SCENARIOS: dict[str, list[str]] = {
    # System state shortcuts (forwarded to sim's `:cmd` REPL handler)
    "Play":            [":play"],
    "Pause":           [":pause"],
    "Stop":            [":stop"],
    "Next track":      [":next"],
    "Standby":         [":standby"],
    "Wake":            [":wake"],

    # Connectivity warnings — each toggles ONE SYS flag and re-sends
    "Spotify offline": [":offline"],
    "Reconnecting":    [":reconnect"],
    "No network":      [":no-network"],
    "WiFi weak":       [":weak-wifi"],
    "All healthy":     [":healthy"],

    # Flap text samples
    "Flap: BEREIT WENN DU WILLST": [":flap BEREIT WENN DU WILLST"],
    "Flap: 404 SOUND FEHLT":       [":flap 404 SOUND FEHLT"],
    "Flap: DJ HAT PAUSE":          [":flap DJ HAT PAUSE"],

    # A long-title scenario to stress-test scroll + split-flap
    "Long-title track": [
        ":next",
        "ST:play|TI:Methodisch inkorrekt - Folge 220 Hip Hip Hurra Wissenschaft|"
        "AR:Mi220|SO:spotify|VO:42|PO:0|DU:6000000|LV:35|TM:14:32",
    ],
}


def send_to_sim(host: str, port: int, lines: list[str]) -> None:
    payload = ("\n".join(lines) + "\n").encode("ascii", "replace")
    with socket.create_connection((host, port), timeout=2.0) as s:
        s.sendall(payload)


def render_page() -> bytes:
    # Group buttons by their visual cluster for readability. Order matters.
    sections = [
        ("Playback",      ["Play", "Pause", "Stop", "Next track"]),
        ("Power state",   ["Standby", "Wake"]),
        ("Connectivity",  ["Spotify offline", "Reconnecting", "No network",
                           "WiFi weak", "All healthy"]),
        ("Flap text",     ["Flap: BEREIT WENN DU WILLST", "Flap: 404 SOUND FEHLT",
                           "Flap: DJ HAT PAUSE"]),
        ("Stress tests",  ["Long-title track"]),
    ]

    parts: list[str] = []
    parts.append("""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>BeatBird Sim Control</title>
<style>
  :root { color-scheme: dark; }
  body  { font-family: ui-monospace, Menlo, Consolas, monospace;
          background: #0d0d0d; color: #f0cb7b; margin: 0; padding: 32px;
          font-size: 15px; }
  h1    { font-size: 18px; letter-spacing: 2px; margin: 0 0 24px;
          text-transform: uppercase; }
  h2    { font-size: 12px; letter-spacing: 2px; color: #888;
          text-transform: uppercase; margin: 24px 0 8px; }
  .row  { display: flex; flex-wrap: wrap; gap: 8px; }
  button{ background: #1a1a1a; color: #f0cb7b; border: 1px solid #333;
          padding: 10px 16px; font: inherit; cursor: pointer;
          border-radius: 4px; min-width: 160px; text-align: left; }
  button:hover { background: #262626; border-color: #f0cb7b; }
  button:active{ background: #f0cb7b; color: #0d0d0d; }
  #log  { margin-top: 32px; font-size: 12px; color: #666;
          white-space: pre-wrap; max-height: 200px; overflow-y: auto; }
  .raw  { margin-top: 16px; }
  .raw input { width: 480px; background: #1a1a1a; color: #f0cb7b;
               border: 1px solid #333; padding: 8px; font: inherit; }
</style></head><body>
<h1>BeatBird Sim Control</h1>
""")

    for title, names in sections:
        parts.append(f'<h2>{title}</h2><div class="row">')
        for name in names:
            parts.append(f'<button data-cmd="{name}">{name}</button>')
        parts.append("</div>")

    parts.append("""
<h2>Raw protocol line</h2>
<form class="raw" onsubmit="sendRaw(event)">
  <input id="raw" placeholder="e.g. ST:play|TI:Custom|AR:Test|SO:spotify|VO:42|PO:0|DU:200000|LV:20" autocomplete="off">
</form>

<div id="log"></div>

<script>
const log = document.getElementById('log');
function push(line) {
  const ts = new Date().toTimeString().slice(0,8);
  log.textContent = `${ts}  ${line}\\n` + log.textContent;
}
async function fire(payload) {
  push('→ ' + payload.split('\\n').join(' | '));
  const r = await fetch('/cmd', {method: 'POST', body: payload,
                                  headers: {'Content-Type':'text/plain'}});
  if (!r.ok) push('  ERROR ' + r.status + ' ' + (await r.text()));
}
document.querySelectorAll('button[data-cmd]').forEach(b => {
  b.addEventListener('click', () => fire(SCENARIO_LOOKUP[b.dataset.cmd]));
});
function sendRaw(e) {
  e.preventDefault();
  const v = document.getElementById('raw').value.trim();
  if (v) fire(v);
}
""")

    # Inline scenario lookup as JSON so the JS can resolve clicks → payload.
    import json
    parts.append("const SCENARIO_LOOKUP = ")
    parts.append(json.dumps({k: "\n".join(v) for k, v in SCENARIOS.items()}))
    parts.append(";\n</script></body></html>")

    return "".join(parts).encode("utf-8")


def make_handler(sim_host: str, sim_port: int):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter access log
            return

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                body = render_page()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404); self.end_headers()

        def do_POST(self) -> None:
            if self.path != "/cmd":
                self.send_response(404); self.end_headers(); return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8", "replace")
            lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
            if not lines:
                self.send_response(400); self.end_headers()
                self.wfile.write(b"empty"); return
            try:
                send_to_sim(sim_host, sim_port, lines)
            except OSError as e:
                self.send_response(502)
                self.send_header("Content-Type", "text/plain"); self.end_headers()
                self.wfile.write(f"sim TCP {sim_host}:{sim_port} - {e}".encode())
                return
            self.send_response(204); self.end_headers()
    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description="BeatBird sim web control panel")
    ap.add_argument("--sim-host", default="127.0.0.1")
    ap.add_argument("--sim-port", type=int, default=7777)
    ap.add_argument("--port",     type=int, default=8080,
                    help="HTTP port (default 8080). Bind 0.0.0.0 — reachable on LAN.")
    args = ap.parse_args()

    handler = make_handler(args.sim_host, args.sim_port)
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), handler)
    print(f"sim_web serving http://0.0.0.0:{args.port}  →  "
          f"sim TCP {args.sim_host}:{args.sim_port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
