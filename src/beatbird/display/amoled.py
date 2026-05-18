"""
beatbird.display.amoled — Waveshare ESP32-S3 AMOLED 1.43 over USB serial.

Protocol (Pi → ESP32):  one line per message, pipe-separated KV pairs.
  State:    ST:play|TI:…|AR:…|SO:…|VO:..|PO:..|DU:..|LV:..|TM:..|FX:[..,..]
  System:   SYS:cp=..|ht=ok|hs=ok|ds=1|sv=1|wi=-58
  Palette:  PAL:F0CB7B           ← sent once after each (re)connect
  Boot:     BOOT:stage|progress
  Legacy:   VOL:45  SOURCE:spotify  STATE:PLAY

Protocol (ESP32 → Pi):
  VOL:0-100         (volume arc rotated)
  CMD:PLAY|PAUSE|NEXT|PREV|STOP|PLAYPAUSE
  CMD:SOURCE:bluetooth    (source picker selected)
  TEMP:XX.X         (QMI8658 head temperature)
  [hb] …            (heartbeat — ignored)
"""

from __future__ import annotations

import glob
import logging
import time

import serial
import serial.tools.list_ports

from beatbird.display.base import (
    CommandCallback,
    DisplayInterface,
    DisplayState,
    DisplaySystemStatus,
    VolumeCallback,
)

log = logging.getLogger("beatbird.display.amoled")


def _find_port(preferred: str = "auto") -> str | None:
    if preferred not in ("auto", "", None):
        return preferred
    # 1. Our own udev symlink
    for cand in ("/dev/beatbird-display",):
        if glob.glob(cand):
            return cand
    # 2. VID 0x303A (Espressif)
    for port in serial.tools.list_ports.comports():
        if port.vid == 0x303A:
            log.info("found ESP32-S3 at %s", port.device)
            return port.device
    # 3. First /dev/ttyACM* fallback
    for cand in sorted(glob.glob("/dev/ttyACM*")):
        log.info("fallback to %s", cand)
        return cand
    return None


class AmoledDisplay(DisplayInterface):
    def __init__(
        self,
        serial_device: str = "auto",
        baud: int = 115200,
        spectrum_bands: int = 16,
        accent_color: str = "F0CB7B",
    ):
        self.serial_device_hint = serial_device
        self.baud = baud
        self.spectrum_bands = spectrum_bands
        self.accent_color = accent_color.lstrip("#").upper()
        self.ser: serial.Serial | None = None
        self.on_command: CommandCallback | None = None
        self.on_volume: VolumeCallback | None = None
        self._last_connect_attempt = 0.0
        self._reconnect_delay = 5.0
        # Re-send palette on every reconnect — the ESP32 may have rebooted
        self._palette_sent = False
        # Heartbeat watchdog: ESP32 sends `[hb]` every 10s. If we stop seeing
        # them while the serial port is still "open", it's a CDC zombie —
        # write() returns OK but bytes never reach the device. Force reopen.
        # 60s = miss 6 in a row before reacting; tighter values triggered
        # false-positive reconnects under bursty bridge→ESP traffic.
        self._last_hb_received = 0.0
        self._hb_timeout = 60.0

    # ─── Lifecycle ──────────────────────────────────────────────────────────

    def setup(
        self,
        on_command: CommandCallback | None = None,
        on_volume: VolumeCallback | None = None,
    ) -> None:
        self.on_command = on_command
        self.on_volume = on_volume
        self._try_connect()

    def close(self) -> None:
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        self._palette_sent = False
        self._last_hb_received = 0.0

    def _try_connect(self) -> bool:
        now = time.monotonic()
        if now - self._last_connect_attempt < self._reconnect_delay:
            return False
        self._last_connect_attempt = now

        port = _find_port(self.serial_device_hint)
        if not port:
            log.debug("no ESP32 port available")
            return False

        try:
            self.ser = serial.Serial(port, self.baud, timeout=0.1)
            time.sleep(2)  # let ESP32 finish USB CDC init
            log.info("connected to %s", port)
            self._palette_sent = False
            self._last_hb_received = time.monotonic()
            self._send_palette()
            return True
        except serial.SerialException as e:
            log.error("serial open failed: %s", e)
            self.ser = None
            return False

    # ─── Palette ────────────────────────────────────────────────────────────

    def _send_palette(self) -> None:
        """Push the speaker accent colour to the ESP32. Idempotent — safe to
        call multiple times."""
        if self._palette_sent:
            return
        self._send(f"PAL:{self.accent_color}")
        self._palette_sent = True
        log.info("palette sent: %s", self.accent_color)

    def set_accent_color(self, hex_color: str) -> None:
        """Update the accent colour at runtime (e.g. after a profile reload)."""
        self.accent_color = hex_color.lstrip("#").upper()
        self._palette_sent = False
        self._send_palette()

    # ─── Sending ────────────────────────────────────────────────────────────

    def _send(self, line: str) -> None:
        if not self.ser or not self.ser.is_open:
            if not self._try_connect():
                return
        try:
            self.ser.write(f"{line}\n".encode())
            self.ser.flush()
            log.debug("TX: %s", line)
        except serial.SerialException as e:
            log.error("TX failed: %s", e)
            self.ser = None
            self._palette_sent = False

    def push_state(self, state: DisplayState) -> None:
        parts = [
            f"ST:{state.playback}",
            f"TI:{state.title}",
            f"AR:{state.artist}",
            f"SO:{state.source}",
            f"VO:{state.volume}",
            f"PO:{state.position_ms}",
            f"DU:{state.duration_ms}",
            f"LV:{state.signal_level}",
            f"TM:{state.time_hhmm}",
        ]
        # Only emit FX: when the analyzer has produced non-zero data. If it's
        # silently failing (e.g. ALSA Loopback already opened by CamillaDSP,
        # numpy missing), suppress the field so the firmware falls back to
        # its LV:-driven energy ring instead of rendering 12 dead dots.
        if state.spectrum is not None and any(b > 0 for b in state.spectrum):
            bands = list(state.spectrum[: self.spectrum_bands])
            while len(bands) < self.spectrum_bands:
                bands.append(0)
            parts.append("FX:" + ",".join(str(b) for b in bands))
        self._send("|".join(parts))

    def push_system(self, status: DisplaySystemStatus) -> None:
        amp_fields = "|".join(
            f"h{k[0]}={v}" for k, v in (status.amp_statuses or {}).items()
        )
        line = (
            f"SYS:cp={round(status.cpu_temp, 1)}"
            + (f"|{amp_fields}" if amp_fields else "")
            + f"|ds={1 if status.dsp_active else 0}"
            + f"|sv={1 if status.spotify_active else 0}"
            + f"|wi={status.wifi_rssi}"
        )
        self._send(line)

    # ─── Reading ────────────────────────────────────────────────────────────

    def poll(self) -> None:
        if not self.ser or not self.ser.is_open:
            return
        try:
            while self.ser.in_waiting:
                raw = self.ser.readline().decode("utf-8", errors="replace").strip()
                if raw:
                    self._handle_rx(raw)
        except serial.SerialException:
            self.ser = None
            self._palette_sent = False
            self._last_hb_received = 0.0
            return

        # Heartbeat watchdog — bytes haven't arrived for too long despite the
        # port reporting open. Reopen on next _send() / poll() cycle.
        if (
            self.ser and self.ser.is_open
            and self._last_hb_received > 0.0
            and time.monotonic() - self._last_hb_received > self._hb_timeout
        ):
            log.warning(
                "no heartbeat for >%.0fs, reopening serial",
                self._hb_timeout,
            )
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
            self._palette_sent = False
            self._last_hb_received = 0.0

    def _handle_rx(self, raw: str) -> None:
        if raw.startswith("VOL:"):
            try:
                vol = int(raw[4:])
                if self.on_volume:
                    self.on_volume(max(0, min(100, vol)))
            except ValueError:
                pass
        elif raw.startswith("CMD:"):
            cmd = raw[4:].upper()
            if self.on_command:
                self.on_command(cmd)
        elif raw.startswith("TEMP:"):
            pass  # IMU head temp — not used in bridge logic yet
        elif raw.startswith("[hb]"):
            self._last_hb_received = time.monotonic()
            log.debug("hb: %s", raw)
        else:
            log.debug("unknown RX: %s", raw)
