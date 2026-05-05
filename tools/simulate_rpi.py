#!/usr/bin/env python3
"""
BeatBird Display – Raspberry Pi Simulator
Verbindet sich per USB-Serial mit dem ESP32 und simuliert den RPi-Controller.

Abhängigkeiten:
    pip install pyserial

Verwendung:
    python simulate_rpi.py                    # Auto-Port-Erkennung
    python simulate_rpi.py --port COM3        # Fester Port
    python simulate_rpi.py --demo             # Demo-Modus (Playlist abspielen)
    python simulate_rpi.py --port COM3 --demo
"""

import argparse
import math
import random
import threading
import time

import serial
import serial.tools.list_ports


# =============================================================================
# Konfiguration
# =============================================================================
BAUD_RATE   = 115200
BASS_INTERVAL = 0.1   # Sekunden zwischen BASS-Updates im Demo-Modus

DEMO_PLAYLIST = [
    {"title": "Blue Jeans",          "artist": "Lana Del Rey",     "duration": 15, "bpm": 82},
    {"title": "Blinding Lights",     "artist": "The Weeknd",       "duration": 15, "bpm": 171},
    {"title": "Bohemian Rhapsody",   "artist": "Queen",            "duration": 15, "bpm": 72},
    {"title": "Levitating",          "artist": "Dua Lipa",         "duration": 15, "bpm": 103},
    {"title": "As It Was",           "artist": "Harry Styles",     "duration": 15, "bpm": 174},
]


# =============================================================================
# Serial-Verbindung
# =============================================================================
def find_esp32_port():
    """Sucht automatisch nach einem ESP32/CH340/CP210x-Port."""
    keywords = ["CP210", "CH340", "UART", "USB Serial", "ESP32", "Silicon Labs"]
    ports = serial.tools.list_ports.comports()
    for p in ports:
        desc = f"{p.description} {p.manufacturer or ''}"
        if any(k.lower() in desc.lower() for k in keywords):
            return p.device
    # Fallback: erster verfügbarer Port
    if ports:
        return ports[0].device
    return None


def open_serial(port, baud=BAUD_RATE, timeout=1):
    ser = serial.Serial(port, baud, timeout=timeout)
    time.sleep(0.1)          # kurz warten bis bereit
    ser.reset_input_buffer()
    return ser


# =============================================================================
# Lese-Thread (ESP32 → PC)
# =============================================================================
class SerialReader(threading.Thread):
    def __init__(self, ser):
        super().__init__(daemon=True)
        self.ser   = ser
        self.running = True

    def run(self):
        while self.running:
            try:
                line = self.ser.readline()
                if line:
                    text = line.decode("utf-8", errors="replace").rstrip()
                    if text:
                        # Zeigt ESP32-Output in anderem Format an
                        print(f"\r  [ESP32] {text}")
                        print(">>> ", end="", flush=True)
            except serial.SerialException:
                break

    def stop(self):
        self.running = False


# =============================================================================
# Kommando-Helfer
# =============================================================================
def send(ser, cmd: str):
    """Sendet eine Kommandozeile an den ESP32."""
    line = (cmd.strip() + "\n").encode("utf-8")
    ser.write(line)
    print(f"  → {cmd.strip()}")


# =============================================================================
# Demo-Modus
# =============================================================================
def demo_bass(t, bpm):
    """Simuliert einen Bass-Level basierend auf Zeit und BPM."""
    beat_period = 60.0 / bpm          # Sekunden pro Beat
    phase = (t % beat_period) / beat_period  # 0..1 innerhalb eines Beats
    # Kurzer harter Kick + langsamer Abfall
    kick = math.exp(-phase * 8)
    # Leichtes Rauschen dazu
    noise = random.uniform(0, 15)
    level = int(kick * 80 + noise)
    return max(0, min(100, level))


def run_demo(ser):
    print("\n=== Demo-Modus gestartet (Strg+C zum Beenden) ===\n")

    send(ser, "STATE:PLAY")
    time.sleep(0.3)

    try:
        for track in DEMO_PLAYLIST:
            print(f"\n--- Jetzt: {track['title']} – {track['artist']} ---")
            send(ser, f"SONG:{track['title']}|{track['artist']}")
            time.sleep(0.2)

            end_time = time.time() + track["duration"]
            t = 0.0
            while time.time() < end_time:
                bass = demo_bass(t, track["bpm"])
                send(ser, f"BASS:{bass}")
                time.sleep(BASS_INTERVAL)
                t += BASS_INTERVAL

        print("\n--- Playlist beendet ---")
        send(ser, "STATE:STANDBY")

    except KeyboardInterrupt:
        print("\n[Demo unterbrochen]")
        send(ser, "STATE:PAUSE")


# =============================================================================
# Interaktiver Modus
# =============================================================================
HELP_TEXT = """
╔══════════════════════════════════════════════════════╗
║         BeatBird RPi Simulator – Befehle             ║
╠══════════════════════════════════════════════════════╣
║  play                    STATE:PLAY                  ║
║  pause / stop            STATE:PAUSE / STOP          ║
║  standby                 STATE:STANDBY               ║
║  song <titel>|<artist>   Songtitel setzen            ║
║  vol <0-100>             Lautstärke setzen           ║
║  bass <0-100>            Bass-Level setzen           ║
║  status                  Systemstatus abfragen       ║
║  demo                    Demo-Playlist starten       ║
║  raw <befehl>            Rohbefehl senden            ║
║  help / ?                Diese Hilfe                 ║
║  quit / exit             Beenden                     ║
╚══════════════════════════════════════════════════════╝
"""

def run_interactive(ser):
    print(HELP_TEXT)

    while True:
        try:
            user = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[Beendet]")
            break

        if not user:
            continue

        cmd  = user.lower()
        args = user[len(cmd.split()[0]):].strip()   # rest nach erstem Wort

        if cmd in ("quit", "exit"):
            break
        elif cmd in ("help", "?"):
            print(HELP_TEXT)
        elif cmd == "play":
            send(ser, "STATE:PLAY")
        elif cmd in ("pause", "stop"):
            send(ser, f"STATE:{cmd.upper()}")
        elif cmd == "standby":
            send(ser, "STATE:STANDBY")
        elif cmd.startswith("song "):
            send(ser, f"SONG:{args}")
        elif cmd.startswith("vol "):
            send(ser, f"VOL:{args}")
        elif cmd.startswith("bass "):
            send(ser, f"BASS:{args}")
        elif cmd == "status":
            send(ser, "STATUS")
        elif cmd == "demo":
            demo_thread = threading.Thread(target=run_demo, args=(ser,), daemon=True)
            demo_thread.start()
            demo_thread.join()
        elif cmd.startswith("raw "):
            send(ser, args)
        else:
            print(f"  Unbekannt: '{user}'. Tippe 'help' für Hilfe.")


# =============================================================================
# Main
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="BeatBird RPi Simulator")
    parser.add_argument("--port",  default=None, help="Serieller Port (z.B. COM3 oder /dev/ttyUSB0)")
    parser.add_argument("--baud",  default=BAUD_RATE, type=int, help=f"Baudrate (Standard: {BAUD_RATE})")
    parser.add_argument("--demo",  action="store_true", help="Demo-Modus direkt starten")
    args = parser.parse_args()

    # Port bestimmen
    port = args.port
    if not port:
        port = find_esp32_port()
        if not port:
            print("Kein serieller Port gefunden. Bitte --port angeben.")
            return

    print(f"Verbinde mit {port} @ {args.baud} Baud ...")
    try:
        ser = open_serial(port, args.baud)
    except serial.SerialException as e:
        print(f"Fehler: {e}")
        return

    print(f"Verbunden.\n")

    # Lese-Thread starten
    reader = SerialReader(ser)
    reader.start()

    try:
        if args.demo:
            run_demo(ser)
        else:
            run_interactive(ser)
    finally:
        reader.stop()
        ser.close()
        print("Verbindung getrennt.")


if __name__ == "__main__":
    main()
