# BeatBird Display Simulator

Native LVGL+SDL build of the display firmware. Lets you iterate on
screens, animations, palettes, and the protocol without flashing the
actual ESP32 every change. The same UI source code (`firmware/amoled-1.43/src/ui/`)
compiles for both ESP32 and native — only `main.cpp`, `src/sh8601/`,
and Arduino/ESP-IDF includes are swapped for `src/sim/sim_main.cpp`
and `include/sim/arduino_shim.h`.

```
┌────────────────────┐     stdin / TCP :7777    ┌─────────────────┐
│ Streamlit panel    │ ───────── lines ──────▶  │ sim binary      │
│ scripts/sim_web.py │  (same grammar as the    │ SDL window 466² │
│ http :8080         │  bridge sends via USB)   │ (LVGL native)   │
└────────────────────┘                          └─────────────────┘
                                                   │
                                          drives the same screens
                                          ScreenBoot / ScreenPlayer / ScreenStandby
```

## Host requirements

Linux box with X11 — devstation is the canonical home. SDL2 + Node.js
(for the Departure Mono font generator) are the only system deps;
PlatformIO lives in a Python venv.

```bash
sudo apt update
sudo apt install -y git build-essential python3-pip python3-venv pkg-config \
                    libsdl2-dev nodejs npm openssh-server
python3 -m venv ~/.pio-venv
~/.pio-venv/bin/pip install -U platformio Pillow streamlit
mkdir -p ~/.local/bin
ln -sf ~/.pio-venv/bin/pio ~/.local/bin/pio
```

Then either clone the repo (HTTPS works without keys) or use an
existing checkout:

```bash
git clone https://github.com/Bemme22/beatbird.git ~/beatbird
```

## Build

The Departure Mono font `.c` files are gitignored — regenerate them
once after a fresh clone. The `[env:sim]` PlatformIO target then
compiles in ~95 seconds (cold) or ~2 seconds (incremental).

```bash
cd ~/beatbird/firmware/amoled-1.43
python3 fonts/build.py            # one-time per clone — downloads OTF + lv_font_conv
~/.local/bin/pio run -e sim       # rebuild on any code change
```

Output: `.pio/build/sim/program` (~4.6 MB ELF).

## Run

Two processes — both daemonised so closing the SSH session doesn't kill
them. Output goes to `/tmp/sim.log` and `/tmp/sim_web.log` for
post-mortem.

```bash
cd ~/beatbird/firmware/amoled-1.43

# 1. sim binary — opens the SDL window in the local X11 session (VNC works).
#    DISPLAY=:0 is the GDM-X session that VNC mirrors.
nohup env DISPLAY=:0 .pio/build/sim/program > /tmp/sim.log 2>&1 < /dev/null & disown

# 2. Streamlit control panel — buttons that POST to the sim's TCP :7777
nohup ~/.pio-venv/bin/streamlit run scripts/sim_web.py \
      --server.address 0.0.0.0 --server.port 8080 --server.headless true \
      > /tmp/sim_web.log 2>&1 < /dev/null & disown
```

Then point a browser anywhere on the LAN at `http://<this-host>:8080`.

To stop:

```bash
pkill -f sim/program
pkill -f streamlit
```

## What you can drive

The Streamlit panel groups buttons by intent:

| Section | What it does |
|---|---|
| **Playback** | `:play` / `:pause` / `:stop` / `:next` shortcuts |
| **Power state** | force `:standby` (clock + weather + flap), `:wake` |
| **Connectivity** | flip SYS flags for SPOTIFY OFFLINE / RECONNECTING / NO NETWORK / WIFI WEAK / healthy |
| **Sources** | spotify / bluetooth / snapcast — exercises source-marker palette per source |
| **Shutdown sequence** | the long-press-warn + halting-now screens |
| **Volume** | 0 / 25 / 50 / 75 / 100 presets — tests vol-arc + WOBBLE on energy |
| **Weather** | clear / partly / cloudy / fog / rain / snow / thunder — animated standby icons |
| **Standby flap text** | hand-typed line via the flap pipeline |
| **Live palette swap** | 6 colour pickers (a/g/d/p/s/e) + "derive from accent" helper |
| **Custom title** | bypass everything, send `ST:play\|TI:…` directly |
| **Album cover (push test)** | runs the Pi-side CoverProcessor in-process, slider-tunes blur/darken/vignette, chunks the JPEG via IMG: to the sim |
| **Stress tests** | long-title track + raw-protocol-line escape hatch |

Everything maps to the same protocol the bridge would send on a real
speaker — see [docs/protocol.md] for the wire-level grammar.

## Iterating on UI code

Sim startup is ~5 seconds. Edit a screen file, rebuild, restart:

```bash
# After editing src/ui/screens/screen_player.cpp:
pkill -f sim/program
~/.local/bin/pio run -e sim && \
    nohup env DISPLAY=:0 .pio/build/sim/program > /tmp/sim.log 2>&1 < /dev/null & disown
```

Streamlit hot-reloads its own file — change `scripts/sim_web.py`, the
browser tab offers a "Source file changed. Rerun?" banner.

## Debug & diagnostics

- **Sim stdout** lands in `/tmp/sim.log`. `printf` from C++ shows up
  there, as do LVGL log lines.
- **LVGL log verbosity** is set in `include/lv_conf.h`
  (`LV_LOG_LEVEL`). Bump to `INFO` if a decoder or driver is silently
  failing.
- **Window blank** usually means SDL couldn't open `$DISPLAY`. Confirm
  via `who` (should list a `:0` session) and that the sim was started
  with `DISPLAY=:0`.
- **No paste in VNC terminal** — RealVNC + x11vnc need `autocutsel`
  running inside the X session: `DISPLAY=:0 autocutsel -fork && DISPLAY=:0 autocutsel -selection PRIMARY -fork`.
  Persist via `~/.vnc/xstartup`.

## Extending

Adding a new scenario button:

1. If the firmware already handles the protocol (e.g. `STBY:`), just
   add an entry to the appropriate `row(...)` block in `sim_web.py` —
   the line you'd send is the same one the bridge sends.
2. If it's a new protocol verb, add a `:cmd` macro in the REPL handler
   in `src/sim/sim_main.cpp` first so both stdin and Streamlit can
   trigger it.

Adding firmware support for something only the bridge does (a new
state field, a new dirty bit): write it in `src/proto/serial_rx.cpp`
and `include/state.h` first; the sim picks it up automatically because
it compiles the same files.
