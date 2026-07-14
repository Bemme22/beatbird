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
        accent_glow: str | None = None,
        accent_dim: str | None = None,
        text_primary: str | None = None,
        text_secondary: str | None = None,
        accent_alert: str | None = None,
    ):
        self.serial_device_hint = serial_device
        self.baud = baud
        self.spectrum_bands = spectrum_bands
        self.accent_color = accent_color.lstrip("#").upper()
        # Optional extended-palette slots. None means "let firmware keep its
        # default for this slot"; the bridge omits the field from PAL: then.
        def _norm(c: str | None) -> str | None:
            if not c: return None
            return c.lstrip("#").upper()
        self.palette = {
            "g": _norm(accent_glow),
            "d": _norm(accent_dim),
            "p": _norm(text_primary),
            "s": _norm(text_secondary),
            "e": _norm(accent_alert),
        }
        self.ser: serial.Serial | None = None
        self.on_command: CommandCallback | None = None
        self.on_volume: VolumeCallback | None = None
        self._last_connect_attempt = 0.0
        self._reconnect_delay = 5.0
        # Re-send palette on every reconnect — the ESP32 may have rebooted
        self._palette_sent = False
        # Signature of the last palette we *logged*. Legitimate re-sends
        # (startup init + override-apply, plus the [boot] re-push after an
        # ESP32 reboot) otherwise spam identical "palette sent" lines; the
        # send still happens every time, only the log line dedups on content.
        self._last_logged_palette: str | None = None
        # QR URL is similarly volatile across firmware reboots. The bridge
        # pushes it once at start; on a mid-session ESP32 boot we re-send
        # via the [boot] handler so the standby screen has it ready
        # before any sys.bt_pairing transition.
        self._last_qr_url: str | None = None
        # Heartbeat watchdog: ESP32 sends `[hb]` every 10s. If we stop seeing
        # them while the serial port is still "open", it's a CDC zombie —
        # write() returns OK but bytes never reach the device. Force reopen.
        # 60s = miss 6 in a row before reacting; tighter values triggered
        # false-positive reconnects under bursty bridge→ESP traffic.
        self._last_hb_received = 0.0
        self._hb_timeout = 60.0
        # Self-reported by the firmware via "FW:<version>" on boot. Used by
        # the OTA updater (bin/beatbird-firmware-update) to skip flashing
        # when the running version already matches the latest release tag.
        self.firmware_version: str | None = None

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

    def set_palette(self, slots: dict[str, str]) -> None:
        """Replace one or more palette slots at runtime and re-send to the
        firmware. `slots` keys are a/g/d/p/s/e per the PAL: protocol;
        values are hex strings (with or without leading '#'). Missing
        keys keep their current value. Used by the web UI settings page
        for live palette swap without a bridge restart."""
        def _norm(c: str | None) -> str | None:
            if not c: return None
            return c.lstrip("#").upper()

        if "a" in slots and slots["a"]:
            self.accent_color = _norm(slots["a"])
        for k in ("g", "d", "p", "s", "e"):
            if k in slots and slots[k]:
                self.palette[k] = _norm(slots[k])
        # Force re-send next poll cycle.
        self._palette_sent = False
        self._send_palette()

    def _send_palette(self) -> None:
        """Push the speaker palette to the ESP32 once per (re)connect. If any
        extended-palette slot is configured, emit the new key=value form so
        the firmware applies all six tokens; otherwise fall back to the
        legacy single-hex form. Both forms are accepted by the firmware
        (handle_palette_line auto-detects via the presence of '=')."""
        if self._palette_sent:
            return
        extras = [(k, v) for k, v in self.palette.items() if v]
        if extras:
            parts = [f"a={self.accent_color}"] + [f"{k}={v}" for k, v in extras]
            self._send("PAL:" + "|".join(parts))
        else:
            self._send(f"PAL:{self.accent_color}")
        self._palette_sent = True
        sig = "a=%s%s" % (self.accent_color,
                          "".join(f" {k}={v}" for k, v in extras))
        if sig != self._last_logged_palette:
            self._last_logged_palette = sig
            log.info("palette sent: %s", sig)

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

    @staticmethod
    def _esc_field(text: str) -> str:
        """Sanitise a state-line field value so it can't break the
        pipe-separated, newline-terminated protocol the ESP32 parser
        expects. Pathological cases that crashed (or would have
        crashed) the parser before this:

          - Track title contains '|' (e.g. "Killing In The Name | Live")
            → ESP32 would split it as if the second half was a new
            KEY:value field and ignore the rest.
          - Title contains '\\n' (rare but possible from some podcast
            feeds) → would terminate the line early and drop trailing
            fields like LV: and TM:.

        Both are replaced with U+2014 (em dash) — a printable
        substitute that's already in the firmware's Departure Mono
        font and visually conveys 'this was a separator'."""
        if not text:
            return ""
        return text.replace("|", "—").replace("\n", " ").replace("\r", " ")

    def push_state(self, state: DisplayState) -> None:
        parts = [
            f"ST:{state.playback}",
            f"TI:{self._esc_field(state.title)}",
            f"AR:{self._esc_field(state.artist)}",
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

    def push_raw(self, line: str) -> None:
        # Strip trailing newline — _send adds one. Multi-line input is ignored
        # past the first newline (caller should send line-by-line).
        line = line.rstrip("\r\n")
        if line:
            self._send(line)

    # Map German umlauts and other Latin-1 chars to ASCII digraphs.
    # Departure Mono has the glyphs, but the firmware-side split-flap
    # animates byte-by-byte and would corrupt multi-byte UTF-8 mid-cycle.
    # Digraphs are the classic airport-board way to write German anyway.
    _IDLE_TRANSLATE = str.maketrans({
        "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
        "Ä": "AE", "Ö": "OE", "Ü": "UE",
        "é": "e",  "è": "e",  "ê": "e",  "É": "E",
        "á": "a",  "à": "a",  "â": "a",
        "í": "i",  "ì": "i",  "î": "i",
        "ó": "o",  "ò": "o",  "ô": "o",
        "ú": "u",  "ù": "u",  "û": "u",
        "ñ": "n",  "ç": "c",
    })

    # ─── Album cover background ─────────────────────────────────────────────
    # Chunk size 600 raw bytes → ~800 base64 chars per line. Stays comfortably
    # below the firmware's per-line read buffer (256+grow path in serial_rx)
    # and the USB-CDC default TX FIFO. With 600 B/chunk a typical 5-30 KB
    # processed cover lands in 10-50 lines, taking well under a second over
    # USB-CDC. The firmware accumulates chunks into a PSRAM buffer and
    # decodes the JPEG on `IMG:end`.
    _COVER_CHUNK_BYTES = 600

    def push_cover(self, jpeg_bytes: bytes) -> None:
        if not jpeg_bytes:
            return
        import base64
        size = len(jpeg_bytes)
        self._send(f"IMG:start|size={size}")
        # Iterate raw bytes in fixed chunks and base64-encode each — easier
        # than encoding the whole thing then splitting (base64 output is
        # 4/3 the size of input; the math is cleaner from the input side).
        seq = 0
        for off in range(0, size, self._COVER_CHUNK_BYTES):
            chunk = jpeg_bytes[off:off + self._COVER_CHUNK_BYTES]
            b64 = base64.b64encode(chunk).decode("ascii")
            self._send(f"IMG:{seq}:{b64}")
            seq += 1
        self._send("IMG:end")
        log.info("cover pushed: %d bytes in %d chunks", size, seq)

    def push_halftone(self, grid: bytes, n: int) -> None:
        """Send the Pi-downsampled cover as an n×n RGB halftone grid (HT:).
        Used by the halftone-renderer firmware (vs push_cover's IMG: JPEG, kept
        for older firmware). Same chunked base64 transport, raw RGB payload."""
        if not grid or n <= 0:
            return
        import base64
        size = len(grid)
        self._send(f"HT:start|n={n}")
        seq = 0
        for off in range(0, size, self._COVER_CHUNK_BYTES):
            b64 = base64.b64encode(grid[off:off + self._COVER_CHUNK_BYTES]).decode("ascii")
            self._send(f"HT:{seq}:{b64}")
            seq += 1
        self._send("HT:end")
        log.info("halftone pushed: %d×%d (%d bytes, %d chunks)", n, n, size, seq)

    def push_qr_url(self, url: str) -> None:
        """Cache the BT-pairing QR URL on the firmware. The standby
        screen renders it as a scannable QR code only while
        sys.bt_pairing is true; the bridge pushes this once at start
        (and again on the [boot] marker if the ESP32 rebooted) so the
        URL is ready before the user opens the discoverable window.

        Protocol: QR:<url>  (newline-terminated, ASCII only)
        """
        clean = "".join(c for c in (url or "") if 32 <= ord(c) < 127).strip()
        if not clean:
            return
        self._last_qr_url = clean
        self._send(f"QR:{clean}")

    def push_toast(self, text: str, duration_ms: int = 2000) -> None:
        """Send a transient banner via the firmware's CenterStage slot.
        Used by the bridge to surface events that don't fit the periodic
        state push, e.g. 'PAIRED — Pixel 7' on BT connect. Translates
        German chars to ASCII digraphs like push_idle_message — the
        CenterStage label is rendered in the same font family with the
        same byte-by-byte fade animation, so multi-byte UTF-8 would
        corrupt mid-cycle.

        Protocol: TOAST:<duration_ms>:<text>  (newline-terminated)
        """
        translated = text.translate(self._IDLE_TRANSLATE)
        clean = "".join(c for c in translated if 32 <= ord(c) < 127).strip()
        if not clean:
            return
        # Firmware's toast_buf is 32 bytes — clamp on this side too so a
        # long alias gets visibly truncated rather than silently rejected.
        if len(clean) > 30:
            clean = clean[:30]
        d = max(300, min(10000, int(duration_ms)))
        self._send(f"TOAST:{d}:{clean}")

    def push_idle_message(self, text: str) -> None:
        # Translate accented chars to ASCII digraphs first, then strip any
        # remaining non-ASCII. STBY: line is newline-terminated; the
        # firmware also caps at MAX_LEN=48 on its side.
        translated = text.translate(self._IDLE_TRANSLATE)
        clean = "".join(c for c in translated if 32 <= ord(c) < 127).strip()
        if not clean:
            return
        self._send(f"STBY:{clean}")

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
            + f"|gw={1 if status.gateway_reachable else 0}"
            + f"|ss={1 if status.spotify_stuck_recent else 0}"
            + f"|bt={1 if status.bt_pairing else 0}"
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
        elif raw.startswith("[boot]"):
            # ESP32 rebooted mid-session — its accent_color is back to the
            # Theme::Color::ACCENT_DEFAULT and connected_to_pi is false. The
            # _palette_sent idempotency flag was set by our own startup, so a
            # plain _send_palette() would no-op. Reset it.
            log.info("ESP32 boot marker received, re-sending palette")
            self._palette_sent = False
            self._send_palette()
            # Same logic for the QR URL — firmware-side cache is gone after
            # the boot, so push it again so the standby screen has it
            # ready for the next bt_pairing transition.
            if self._last_qr_url:
                self._send(f"QR:{self._last_qr_url}")
        elif raw.startswith("cover_rx:"):
            # Diagnostic echo from firmware's IMG: parser. Format:
            # "cover_rx: got <received>/<expected>". INFO so it surfaces
            # in journalctl alongside the matching "cover pushed:" line.
            log.info("display %s", raw)
        elif raw.startswith("FW:"):
            # Firmware version self-report on boot. Stored so the updater can
            # skip flashing if the running version already matches the latest
            # release tag.
            # The firmware re-announces FW: on each boot/handshake (2-3× at
            # startup); only act on an actual version change so the log and
            # the state file aren't rewritten every time.
            new_ver = raw[3:].strip()
            if new_ver != self.firmware_version:
                self.firmware_version = new_ver
                log.info("Display firmware version: %s", new_ver)
                # Persist to a tiny file the OTA script reads. Best-effort —
                # off-Pi (dev machine) the path won't exist and that's fine.
                try:
                    import os
                    os.makedirs("/var/lib/beatbird", exist_ok=True)
                    with open("/var/lib/beatbird/firmware-version", "w") as f:
                        f.write(new_ver + "\n")
                except OSError:
                    pass
        else:
            log.debug("unknown RX: %s", raw)
