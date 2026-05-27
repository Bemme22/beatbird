"""
beatbird.bridge — coordinator for the BeatBird speaker platform.

v2.1.0 — production hardening:
  - P0: Log level INFO by default (BEATBIRD_LOGLEVEL env override)
  - P0: SIGTERM handler for clean shutdown
  - P1: Persistent WS to CamillaDSP (via audio.camilladsp)
  - P1: Spotify close_session() instead of service restart
  - P1: Volume curve params from profile (min_db/max_db)
  - BT source with bidirectional volume sync + hard handoff
  - Explicit source priority: last-active-wins with mutual kill

Invoked by systemd as ``python -m beatbird.bridge``.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from enum import Enum

from beatbird.audio.camilladsp import CamillaDSP, pct_to_db, db_to_pct
from beatbird.audio.loudness import LoudnessController, LoudnessFilter
from beatbird.audio.sfx import SoundEffects
from beatbird.audio.spectrum import SpectrumAnalyzer
from beatbird.cover_processor import CoverProcessor
from beatbird.rss_fetcher import RssFetcher
from beatbird import settings_overrides
from beatbird.config import Profile, load_profile
from beatbird.display.base import (
    DisplayInterface, DisplayState, DisplaySystemStatus,
)
from beatbird.ha.mqtt import MqttBridge
from beatbird.hardware.base import HardwareInterface
from beatbird.sources.snapcast import SnapcastClient, get_local_wlan_mac
from beatbird.sources.spotify import SpotifyClient
from beatbird import system

log = logging.getLogger("beatbird.bridge")

STATUS_INTERVAL = 5.0
SPOTIFY_POLL_INTERVAL = 2.0
SNAPCAST_POLL_INTERVAL = 3.0

# Persistent runtime state — survives reboots through the writable path
# /var/lib/beatbird (ReadWritePaths on the bridge service). Currently
# stores the user's last volume so the safe-snap on cold boot doesn't
# always slam to 25 %.
STATE_FILE = "/var/lib/beatbird/state.json"


def sd_notify_local(msg: str) -> None:
    """Lightweight sd_notify — no python-systemd dependency. Reads
    NOTIFY_SOCKET from the env (systemd sets it on Type=notify units);
    no-ops when run outside systemd (tests, dev, journal-less hosts).
    Used for READY=1 at boot and WATCHDOG=1 in the main loop."""
    import os, socket
    sock_path = os.environ.get("NOTIFY_SOCKET")
    if not sock_path:
        return
    # Abstract sockets are prefixed with '@'; replace with NUL for connect.
    if sock_path.startswith("@"):
        sock_path = "\0" + sock_path[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(msg.encode("utf-8"), sock_path)
    except OSError:
        pass
LEVEL_POLL_INTERVAL = 0.1
STATE_PUSH_PLAYING = 0.2
STATE_PUSH_IDLE = 2.0
STANDBY_TIMEOUT_S = 60.0
# Restart go-librespot if /status hangs for this many consecutive polls.
# At SPOTIFY_POLL_INTERVAL=2s, 15 = 30s of unresponsiveness before action.
SPOTIFY_HEALTH_RESTART_THRESHOLD = 15
IDLE_MESSAGE_INTERVAL = 45.0  # how often to flip the standby flap text

# Short German airport-board-style lines shown on the standby screen via the
# split-flap label. Picked at random every IDLE_MESSAGE_INTERVAL while idle,
# mixed with RSS headlines if an idle.rss_url is configured.
#
# Constraints baked in:
#  - ≤ 22 chars so they fit at font_display_md without clipping the round
#    display's edges
#  - ASCII only (digraphs ae/oe/ue/ss instead of umlauts) — the split-flap
#    animates byte-by-byte and would corrupt multi-byte UTF-8 mid-cycle
IDLE_MESSAGES = [
    # Hard 17-char cap — anything longer gets clipped on the right edge of the
    # round display at font_display_md (22 px). Field-tested limit: 21 chars
    # ("VERSTAERKER KUEHLT AB") clipped to "VERSTAERKER KUEHLT A".
    "AUF EMPFANG",          # 11
    "BEREIT WENN DU",       # 14
    "WARTE AUF SIGNAL",     # 16
    "NIX LOS HIER",         # 12
    "STILLE FUER ALLE",     # 16
    "404 SOUND FEHLT",      # 15
    "TAFEL LEER",           # 10
    "STUMM UND GLUECK",     # 16
    "BIN GLEICH DA",        # 13
    "DJ HAT PAUSE",         # 12
    "INSERT BEATS",         # 12
    "TIEFE STILLE",         # 12
    "AUSGEFLOGEN",          # 11
    "GROOVE PUFFERT",       # 14
    "GROSSE PAUSE",         # 12
    "AUF DEM SPRUNG",       # 14
    "BAHN FREI",            #  9
    "VOGEL GELANDET",       # 14
    "AM GATE",              #  7
    "WARTESCHLEIFE",        # 13
    "MUSIK GESUCHT",        # 13
    "FREQUENZ FREI",        # 13
    "POWER NAP",            #  9
    "PEGEL NULL",           # 10
    "ZEIG MIR HITS",        # 13
    "STILLE EBBE",          # 11
    "WARTE NOCH KURZ",      # 15
    "OFFEN FUER MUSIK",     # 16
]


# ─── Source enum ─────────────────────────────────────────────────────────────

class Source(str, Enum):
    NONE      = "none"
    SPOTIFY   = "spotify"
    BLUETOOTH = "bluetooth"
    TOSLINK   = "toslink"
    SNAPCAST  = "snapcast"


class Playback(str, Enum):
    PLAYING = "Playing"
    PAUSED  = "Paused"
    STOPPED = "Stopped"


# ─── Factory functions ───────────────────────────────────────────────────────

def _build_display(profile: Profile) -> DisplayInterface | None:
    d = profile.display
    if d.type == "none":
        return None
    if d.type == "amoled":
        from beatbird.display.amoled import AmoledDisplay
        return AmoledDisplay(
            serial_device=d.serial_device,
            spectrum_bands=d.spectrum_bands,
            accent_color=d.accent_color,
            accent_glow=d.accent_glow,
            accent_dim=d.accent_dim,
            text_primary=d.text_primary,
            text_secondary=d.text_secondary,
            accent_alert=d.accent_alert,
        )
    if d.type == "led-button":
        from beatbird.display.led_button import LedButtonDisplay
        return LedButtonDisplay(
            led_pin=d.led_pin or 18,
            led_count=d.led_count or 12,
            button_pin=d.button_pin or 17,
            brightness=d.led_brightness,
            spectrum_bands=d.spectrum_bands,
        )
    log.error("unknown display type: %s", d.type)
    return None


def _build_hardware(profile: Profile) -> HardwareInterface:
    if profile.soundcard.driver.startswith("louder-hat"):
        from beatbird.hardware.louder_hat import from_profile
        return from_profile(profile.soundcard)
    if profile.soundcard.driver == "innomaker-amp-pro":
        from beatbird.hardware.innomaker import InnomakerAMPPro
        return InnomakerAMPPro()
    from beatbird.hardware.base import NullHardware
    return NullHardware()


def _build_loudness(profile: Profile, dsp: CamillaDSP) -> LoudnessController | None:
    if not profile.audio.loudness.enabled or not profile.audio.loudness.filters:
        return None
    # Hardcoded canonical base gains. NEVER read these from the live
    # CamillaDSP config — that path created a positive-feedback loop
    # because what's stored in CDSP is `base + offset * max_boost`,
    # not `base`. Each bridge restart re-read the boosted value as
    # the new base, so the bass crept up a few dB every reboot. Field
    # reports of 'Bass auf max' were exactly this.
    known_base = {
        "bass_shelf":   {"type": "Lowshelf",  "freq": 120,  "base_gain": 10, "q": 0.6},
        "sub_punch":    {"type": "Peaking",   "freq": 45,   "base_gain": 5,  "q": 0.7},
        "timpani_body": {"type": "Peaking",   "freq": 70,   "base_gain": 3,  "q": 1.0},
        "fullness":     {"type": "Peaking",   "freq": 200,  "base_gain": 3,  "q": 1.0},
        "air_lift":     {"type": "Highshelf", "freq": 8000, "base_gain": 0,  "q": 0.7},
    }

    filters: list[LoudnessFilter] = []
    for f in profile.audio.loudness.filters:
        base = known_base.get(f.name)
        if not base:
            log.warning("loudness: unknown filter %r, skipping", f.name)
            continue
        filters.append(LoudnessFilter(name=f.name, max_boost=f.max_boost_db, **base))
    if not filters:
        return None
    return LoudnessController(dsp, filters, curve=profile.audio.loudness.curve)


def _build_bluetooth(profile: Profile, on_active, on_volume, get_bridge_volume,
                     on_newly_connected):
    """Build BT source tracker if bluetooth source is configured."""
    if not profile.sources.bluetooth.enabled:
        return None
    try:
        from beatbird.sources.bluetooth import BluetoothSource
        return BluetoothSource(
            on_became_active=on_active,
            on_volume_from_phone=on_volume,
            get_bridge_volume=get_bridge_volume,
            on_newly_connected=on_newly_connected,
        )
    except ImportError:
        log.warning("bluetooth module not available")
        return None


# ─── Main bridge ─────────────────────────────────────────────────────────────

class BeatBirdBridge:
    def __init__(self, profile: Profile):
        self.profile = profile
        self.vol_min_db = profile.audio.volume.min_db
        self.vol_max_db = profile.audio.volume.max_db
        self.vol_gamma  = profile.audio.volume.curve_gamma

        self.dsp = CamillaDSP()
        self.spotify = SpotifyClient() if profile.sources.spotify.enabled else None
        # Snapcast source detector. Server host comes from
        # BEATBIRD_SNAPCAST_SERVER (set in /etc/beatbird/env from secrets/),
        # falling back to profile.sources.snapcast.server. The committed
        # profile uses a 192.168.1.10 placeholder — real IPs stay out of
        # the public repo per the personal-data-out-of-repo convention.
        self.snapcast: SnapcastClient | None = None
        if profile.sources.snapcast.enabled:
            snap_host = (os.environ.get("BEATBIRD_SNAPCAST_SERVER", "").strip()
                         or profile.sources.snapcast.server)
            my_mac = get_local_wlan_mac()
            if snap_host and my_mac:
                self.snapcast = SnapcastClient(host=snap_host, my_mac=my_mac)
                log.info("Snapcast detector: server=%s, my_mac=%s",
                         snap_host, my_mac)
            else:
                log.warning("Snapcast enabled but server (%r) or MAC (%r) missing",
                            snap_host, my_mac)
        self.hardware = _build_hardware(profile)
        self.display = _build_display(profile)
        self.loudness = _build_loudness(profile, self.dsp)

        # UI sound effects — short blips for boot, volume, play/pause,
        # skip, BT-connect, standby. Routes through CamillaDSP via the
        # Loopback dmix so SFX inherit master volume + EQ. Lazy: failures
        # to find aplay or the sounds dir auto-disable without crashing.
        self.sfx = SoundEffects(
            enabled=profile.audio.sfx.enabled,
            device=profile.audio.sfx.device,
        )

        # BT source (wired up after init so callbacks can reference self)
        self.bt = _build_bluetooth(
            profile,
            on_active=self._on_bt_active,
            on_volume=self._on_bt_volume,
            get_bridge_volume=lambda: self.current_volume,
            on_newly_connected=self._on_bt_newly_connected,
        )

        # FFT spectrum
        self.spectrum: SpectrumAnalyzer | None = None
        if profile.display.type == "amoled" and profile.display.spectrum_bands > 0:
            self.spectrum = SpectrumAnalyzer(bands=profile.display.spectrum_bands)

        # MQTT
        mqtt_password = os.environ.get("MQTT_PASS", "")
        self.mqtt = MqttBridge(
            profile,
            mqtt_password=mqtt_password,
            on_set_volume=self.set_volume,
            on_set_playback=self._handle_mqtt_playback,
        )

        # ── Live state ──
        self.current_volume = 0
        self.current_volume_db = 0.0
        self.playback = Playback.STOPPED
        self.source = Source.NONE
        self.song_title = ""
        self.song_artist = ""
        self.song_pos_ms = 0
        self.song_dur_ms = 1
        self.signal_level = 0
        self.bt_device_alias = ""
        self._sp_volume_steps = 65535
        self._prev_track_uri = ""
        self._stopped_since: float | None = None

        # Album cover background. Profile-gated because the 466×466 JPEG
        # composite is too heavy for ESP32-S3 — display stutters during
        # cover swap. Disabled by default; flip display.cover_background.
        # enabled=true once the firmware side gets faster (smaller cover,
        # pre-decoded RGB565, partial-redraw, etc.).
        cover_enabled = profile.display.cover_background.enabled
        self.cover_proc: CoverProcessor | None = (
            CoverProcessor() if cover_enabled else None
        )
        if not cover_enabled:
            log.info("cover background disabled (profile flag)")

        # RSS feed for standby idle text. Optional — empty url means
        # local IDLE_MESSAGES only. Runs in its own daemon thread, so
        # the bridge main loop never blocks on a slow feed. The actual
        # fetcher gets started inside _apply_overrides below so the
        # override (if any) wins over profile defaults from the first
        # tick — no double start, no transient profile-default state.
        self.rss: RssFetcher | None = None
        self._idle_max_chars = profile.idle.max_chars
        self._rss_weight = profile.idle.rss_weight

        # Runtime overrides from the web UI settings page. We poll the
        # file's mtime once per status tick (every 5 s) and re-apply on
        # change — no signal handling, no service restart needed.
        self._overrides_mtime: float | None = settings_overrides.mtime()
        self._apply_overrides(settings_overrides.load(), initial=True)

        # ── Standby ──
        # last_playback_time = monotonic timestamp of last PLAYING observation.
        # After STANDBY_TIMEOUT_S of non-PLAYING, enter standby: display → clock,
        # /player/close frees the Spotify Connect slot so no other device can
        # silently take over the speaker at night.
        self.last_playback_time: float = time.monotonic()
        self.in_standby: bool = False
        # Standby flap text rotation — pick a new random message every
        # IDLE_MESSAGE_INTERVAL while idle, avoiding consecutive repeats.
        self._last_idle_msg: str = ""
        self._idle_msg_t: float = 0.0

        # ── librespot health watchdog ──
        # Counts consecutive get_state() failures (HTTP timeouts or refused).
        # systemd already auto-restarts on crash; this catches the case where
        # the process is alive but its HTTP API is wedged.
        self._spotify_fail_count: int = 0

        # ── First-volume-sync direction guard ──
        # go-librespot starts with volume=65535 (max) per its config — that's
        # so its internal scaling preserves full dynamic range, with the real
        # gain handled by CamillaDSP. But the first time we see a valid
        # Spotify volume after bridge start, the naive bidirectional sync
        # would propagate sp_pct=100 to CamillaDSP and overwrite the
        # persistent DSP volume → audible MAX volume for the user. Instead,
        # on the first observation, push the DSP value to Spotify.
        self._spotify_initial_sync_done: bool = False

        # ── Power button ──
        self.power_button = None
        if profile.hardware.power_button.enabled:
            from beatbird.hardware.power_button import PowerButton
            self.power_button = PowerButton(
                gpio=profile.hardware.power_button.gpio,
                long_press_s=profile.hardware.power_button.long_press_s,
                on_warn=self._on_power_warn,
                on_cancel=self._on_power_cancel,
                on_confirm=self._on_power_confirm,
            )
        self._shutdown_warn_active = False

        # ── System stats ──
        self.sys_amp: dict[str, str] = {}
        self.sys_cpu = 0.0
        self.sys_wifi = 0
        self.sys_dsp = False
        self.sys_spotify = False
        self.sys_gateway = True             # gateway ping ok? (default optimistic)
        self.sys_bt_pairing = False         # adapter currently discoverable (web-UI session)
        self._last_bt_pairing = False       # edge-detect — flips so we override standby flap once
        self._sp_stuck_recent_t = 0.0       # monotonic of last stuck-restart fire

        # ── Timing ──
        self.t_last_status = 0.0
        self.t_last_spotify = 0.0
        self.t_last_snapcast = 0.0
        self.t_last_level = 0.0
        self.t_last_state_push = 0.0
        # Spotify stuck-state watchdog. Pattern: go-librespot's local API
        # keeps responding but its Spotify-cloud session is broken — track
        # loads (we see title/artist), but state.paused stays true and
        # position_ms never advances. Pre-watchdog this needed a manual
        # `systemctl restart go-librespot`. Now we detect and auto-heal.
        self._sp_last_progress_ms: int = -1
        self._sp_last_progress_t:  float = 0.0
        self._sp_stuck_restarts:    int = 0
        # Last observed snapcast play state — used to suppress redundant
        # source flips on every poll tick.
        self._snapcast_playing = False

    # ─── Weather poller ──────────────────────────────────────────────────────

    def _start_weather_poller(self) -> None:
        """Spawn the Open-Meteo background poller if configured. Pushes
        WX: lines directly to the display via push_raw.

        Coordinates are read from env vars (BEATBIRD_WEATHER_LAT/LON)
        — they are personal data and never live in the committed profile
        YAML. Install hook reads them from `secrets/location.coords`."""
        wcfg = self.profile.weather
        if not wcfg.enabled or not self.display:
            return
        try:
            lat = float(os.environ.get("BEATBIRD_WEATHER_LAT", "").strip())
            lon = float(os.environ.get("BEATBIRD_WEATHER_LON", "").strip())
        except ValueError:
            log.warning(
                "weather enabled but BEATBIRD_WEATHER_LAT/LON unset or invalid — "
                "create secrets/location.coords (one line: 'lat,lon') and re-run install"
            )
            return
        try:
            from beatbird.weather import start_in_thread
        except Exception as e:
            log.warning("weather poller unavailable: %s", e)
            return
        try:
            start_in_thread(
                lat=lat, lon=lon,
                serial_writer=self.display.push_raw,
                interval_s=wcfg.interval_minutes * 60,
            )
            log.info("weather poller started (%.4f, %.4f, every %d min)",
                     lat, lon, wcfg.interval_minutes)
        except Exception as e:
            log.error("weather poller failed to start: %s", e)

    # ─── Source handoff ──────────────────────────────────────────────────────

    def _transition_source(self, new_source: Source) -> None:
        """Hard handoff: kill the old source before activating the new one."""
        old = self.source
        if old == new_source:
            return
        if old == Source.SPOTIFY and new_source == Source.BLUETOOTH:
            if self.spotify:
                self.spotify.close_session()
                log.info("Handoff: Spotify → BT (session closed)")
        elif old == Source.BLUETOOTH and new_source == Source.SPOTIFY:
            if self.bt:
                from beatbird.sources.bluetooth import disconnect_all_bt
                disconnect_all_bt()
                log.info("Handoff: BT → Spotify (BT disconnected)")
        self.source = new_source

    def _on_bt_active(self, alias: str) -> None:
        """BT became the active audio source."""
        log.info("BT source active: %s", alias)
        self._transition_source(Source.BLUETOOTH)
        self.bt_device_alias = alias
        # Confirmation jingle so the user hears the pairing took
        # without having to look at the display.
        self.sfx.play("bt_connected")

    def _on_bt_volume(self, pct: int) -> None:
        """Phone slider moved → sync to CamillaDSP."""
        log.info("BT phone volume → %d%%", pct)
        self.set_volume(pct)

    def _persist_bt_state(self) -> None:
        """Sync /var/lib/bluetooth to the disk via beatbird-bt-sync so
        bonds survive overlayroot=tmpfs reboot. Best-effort — failures
        are logged at INFO and don't block the caller. The script is
        only installed on Pis that ran `make bt-persist`; on others
        the sudoers entry won't exist and the call will fail cleanly
        with "no askpass".

        Called after every observed BT state mutation (pair completion,
        trust flag flip). Cheap enough to run inline — the tar is
        ~hundreds of bytes for a typical bond pair, and the
        overlayroot-chroot setup is ~200 ms."""
        try:
            r = subprocess.run(
                ["sudo", "-n", "/usr/local/sbin/beatbird-bt-sync"],
                capture_output=True, timeout=20, text=True,
            )
            # Combine both streams — the sync script logs progress via
            # stdout while overlayroot-chroot's banner + any inner tar
            # errors land on stderr. On failure we want the whole
            # picture; on success the stdout log line is enough but a
            # multi-line dump doesn't hurt.
            combined = "\n".join(s.strip() for s in (r.stdout, r.stderr) if s.strip())
            if r.returncode == 0:
                log.info("BT persist: %s",
                         combined.replace("\n", " | ") or "ok")
            else:
                log.warning("BT persist failed (rc=%d): %s",
                            r.returncode,
                            combined.replace("\n", " | ") or "(no output)")
        except Exception as e:
            log.debug("BT persist call failed: %s", e)

    def _on_bt_newly_connected(self, alias: str) -> None:
        """Paired device just came online. Shows a 'PAIRED — <alias>' toast
        on the display so the user gets clear feedback right after the
        phone-side pairing flow finishes — before they've started any
        music. Without this, the display sits on the standby flap until
        the first audio packet arrives and there's no signal that the
        link is actually up. SFX is handled separately by _on_bt_active
        when audio starts flowing.

        Also triggers a BT-state persistence sync — the auto-trust
        applied in BluetoothSource.poll() flipped Trusted=true in the
        overlay tmpfs, which would die on reboot without this push to
        disk."""
        log.info("BT newly connected: %s", alias)
        self._persist_bt_state()
        # If we're in an active pairing window, close it now — the user
        # got what they came for. Saves them from the 60 s discoverable
        # timer running out (with the PAIRING badge stuck on screen),
        # and avoids leaving the speaker visible to other phones in the
        # area after a successful pair. Sets the local flag too so the
        # display switches off the badge on the next push instead of
        # waiting for the 5 s _refresh_system cycle; _push_system_now
        # right after sends the SYS:bt=0 to the firmware immediately.
        if self.sys_bt_pairing:
            try:
                from beatbird.sources.bluetooth import set_discoverable
                set_discoverable(False)
                self.sys_bt_pairing = False
                self._last_bt_pairing = False
                log.info("BT pairing window closed after successful pair")
                try:
                    self._push_system_now()
                except Exception:
                    pass
            except Exception as e:
                log.debug("close pairing window failed: %s", e)
        if not self.display:
            return
        # Exit standby first so the firmware switches back to the player
        # chrome; CenterStage is hidden inside standby mode and the toast
        # would render into a hidden parent if we sent it the other way
        # around. _push_state_now flushes ST:stop, which is what flips
        # the firmware out of standby.
        if self.in_standby:
            self._exit_standby("bt connected")
            self._push_state_now()
        # Keep the toast short — CenterStage's toast_buf is 32 chars.
        clean = (alias or "").strip()
        if len(clean) > 22:
            clean = clean[:22]
        try:
            # ASCII-only — push_toast strips non-ASCII chars on its side
            # so an em-dash would just drop out and leave a double space.
            self.display.push_toast(f"PAIRED - {clean}", duration_ms=2500)
        except Exception as e:
            log.warning("push_toast failed: %s", e)

    # ─── Startup / shutdown ─────────────────────────────────────────────────

    def start(self) -> None:
        log.info(
            "BeatBird bridge starting (profile=%s, driver=%s, display=%s)",
            self.profile.identity.speaker_id,
            self.profile.soundcard.driver,
            self.profile.display.type,
        )
        if self.display:
            self.display.setup(
                on_command=self._handle_display_command,
                on_volume=self.set_volume,
            )
            # Push the BT-pairing QR URL early so the standby screen has
            # it cached before the user opens a discoverable window.
            # Use the runtime hostname (lowercased) rather than the
            # profile's identity.hostname: the profile value is what we
            # set the system to during install, but a mismatch is
            # possible (legacy installs, manual hostname changes) and
            # mDNS resolves the runtime name, not the profile one.
            # Lowercased because some Android/iOS URL parsers are picky
            # about case in mDNS lookups.
            if self.bt is not None and self.profile.web.enabled:
                import socket as _sock
                host = _sock.gethostname().split(".")[0].lower()
                port = self.profile.web.port or 8080
                qr_url = f"http://{host}.local:{port}/"
                try:
                    self.display.push_qr_url(qr_url)
                    log.info("pushed BT pair QR URL: %s", qr_url)
                except Exception as e:
                    log.debug("push_qr_url failed: %s", e)
        if self.spectrum:
            self.spectrum.start()
        if self.power_button:
            self.power_button.start()
        self._start_weather_poller()
        self.mqtt.start()

        # Push the friendly_name into the BlueZ adapter Alias so the
        # phone's BT picker shows e.g. "Zipp Mini 2" instead of the
        # kernel-default hostname. Matters in multi-room households —
        # three speakers all showing as "BeatPi" is unusable.
        if self.bt is not None:
            try:
                from beatbird.sources.bluetooth import set_adapter_alias
                set_adapter_alias(self.profile.identity.friendly_name)
            except Exception as e:
                log.debug("set bt alias failed: %s", e)

            # One-shot sweep: trust every paired device that isn't
            # already trusted. Fixes pre-existing bonds created before
            # auto-trust-on-connect landed (bt-agent itself doesn't
            # set Trusted, so anything paired with the old code stays
            # untrusted and BlueZ silently rejects the reconnect).
            try:
                from beatbird.sources.bluetooth import trust_all_paired
                flipped = trust_all_paired()
                # Trust changes wrote to /var/lib/bluetooth which is in
                # the overlay tmpfs — push to disk so they survive the
                # next reboot. Only when something actually changed.
                if flipped:
                    self._persist_bt_state()
            except Exception as e:
                log.debug("trust_all_paired failed: %s", e)

        # Initial sync. Order of preference:
        # 1) CamillaDSP's persistent volume if it's within the profile's
        #    sane range (i.e. someone has used the speaker before).
        # 2) /var/lib/beatbird/state.json — our own last-known-good
        #    persisted value. Survives a reboot even when CDSP's state
        #    got wiped (overlay tmpfs, fresh image, etc.).
        # 3) SAFE_FIRST_BOOT_PCT (25 %) as the conservative default.
        SAFE_FIRST_BOOT_PCT = 25
        persisted = self._load_persistent_state()
        persisted_pct = persisted.get("volume_pct")
        if not isinstance(persisted_pct, int) or not 0 <= persisted_pct <= 100:
            persisted_pct = None

        db = self.dsp.get_volume_db()
        if db is None:
            # DSP unreachable — fall back to persisted state if we have one,
            # else the conservative default.
            target = persisted_pct if persisted_pct is not None else SAFE_FIRST_BOOT_PCT
            log.warning(
                "DSP volume unreadable at start, %s to %d%%",
                "restoring persisted" if persisted_pct is not None else "defaulting",
                target,
            )
            self.current_volume = target
            self.current_volume_db = pct_to_db(
                target, self.vol_min_db, self.vol_max_db, self.vol_gamma,
            )
            self.dsp.set_volume_db(self.current_volume_db)
        elif db > self.vol_max_db + 0.5:
            # DSP is at boot default (0 dB max). Prefer our persisted value
            # over the blind 25 % snap.
            target = persisted_pct if persisted_pct is not None else SAFE_FIRST_BOOT_PCT
            log.warning(
                "DSP volume %.1f dB exceeds profile max %.1f dB (stale state?), "
                "%s to %d%%",
                db, self.vol_max_db,
                "restoring persisted" if persisted_pct is not None else "snapping",
                target,
            )
            self.current_volume = target
            self.current_volume_db = pct_to_db(
                target, self.vol_min_db, self.vol_max_db, self.vol_gamma,
            )
            self.dsp.set_volume_db(self.current_volume_db)
        else:
            self.current_volume_db = db
            self.current_volume = db_to_pct(
                db, self.vol_min_db, self.vol_max_db, self.vol_gamma,
            )

        # Apply loudness compensation once so the DSP filter gains reflect the
        # current volume from the moment audio starts flowing. Without this,
        # the filters stay at their YAML base_gain until the first volume
        # change, which means quiet listening sounds thin until the user
        # nudges the volume.
        if self.loudness:
            self.loudness.apply(self.current_volume)

        # Welcome jingle. Plays through CamillaDSP at the restored
        # master volume so a quiet startup stays quiet — the signature
        # is 'ready', not 'announce yourself'.
        self.sfx.play("boot")

    def stop(self) -> None:
        log.info("shutting down")
        if self.spectrum:
            self.spectrum.stop()
        if self.power_button:
            self.power_button.stop()
        self.mqtt.stop()
        self.dsp.close()
        if self.display:
            self.display.close()

    # ─── Persistent state (last-known-good volume) ──────────────────────────

    def _load_persistent_state(self) -> dict:
        """Read /var/lib/beatbird/state.json — returns {} if missing or
        unreadable. Never throws; persistence is a polish, not critical."""
        try:
            import json
            with open(STATE_FILE) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}
            return data
        except (OSError, ValueError):
            return {}

    def _save_persistent_state(self) -> None:
        """Atomically rewrite the state file with the current bridge values.
        Atomic = write to a sibling .tmp, fsync, rename — survives a
        crash during the write."""
        try:
            import json, os, tempfile
            data = {
                "volume_pct":   int(self.current_volume),
                "volume_db":    float(self.current_volume_db),
            }
            dirn = os.path.dirname(STATE_FILE) or "."
            fd, tmp = tempfile.mkstemp(prefix="state.", suffix=".tmp", dir=dirn)
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, STATE_FILE)
            except Exception:
                try: os.unlink(tmp)
                except OSError: pass
                raise
        except Exception as e:
            log.debug("persistent state save failed: %s", e)

    # ─── Volume (single source of truth: CamillaDSP) ────────────────────────

    def set_volume(self, pct: int) -> None:
        pct = max(0, min(100, pct))
        db = pct_to_db(pct, self.vol_min_db, self.vol_max_db, self.vol_gamma)
        self.dsp.set_volume_db(db)
        self.current_volume = pct
        self.current_volume_db = db
        log.info("Volume → %d%% (%.1f dB)", pct, db)
        if self.loudness:
            self.loudness.apply(pct)
        # Volume tick — throttled inside the sfx module so a rotary
        # gesture (which fires many set_volume calls per second) plays
        # at most ~10 ticks/sec instead of buzzing.
        self.sfx.play("volume")
        # Mirror to the active source so their slider stays in sync
        if self.source == Source.SPOTIFY and self.spotify:
            val = round(pct / 100.0 * self._sp_volume_steps)
            self.spotify.set_volume(pct, self._sp_volume_steps)
        elif self.source == Source.BLUETOOTH and self.bt:
            self.bt.push_volume_to_phone(pct)
        # Persist last-known-good so the next boot can restore instead of
        # safe-snapping to 25 %. Best-effort — failures are logged at DEBUG
        # and don't disturb the user-visible volume change.
        self._save_persistent_state()

    # ─── Display / MQTT callbacks ───────────────────────────────────────────

    def _handle_display_command(self, cmd: str) -> None:
        log.info("display → CMD:%s", cmd)
        if self._shutdown_warn_active:
            return  # user holding power button — don't accept display input
        # BT_PAIR is the one command we want to stay in standby for: the
        # standby screen hosts the QR-code overlay and the PAIRING <name>
        # flap text, both of which are the whole point of opening the
        # discoverable window. Exiting standby would yank the user back
        # to the player chrome and force them to find the QR via another
        # path.
        if self.in_standby and cmd != "BT_PAIR":
            self._exit_standby("user command")

        # WAKE: tap-to-wake from the standby screen. Side-effect-free — the
        # exit_standby call above is the whole point. Don't forward it to
        # AVRCP / Spotify; those would either no-op or misinterpret.
        if cmd == "WAKE":
            return

        # BT_PAIR: swipe-down settings-menu → "Pair Bluetooth" button on
        # the display. Same code path as the web UI's pairing button —
        # bluez goes discoverable for 60 s, bt-agent auto-accepts the
        # incoming request, the existing SYS:bt= flag drives the PAIRING
        # overlay back on the display. Lets a non-tech household member
        # pair their phone without finding the web UI.
        if cmd == "BT_PAIR":
            if self.bt is None:
                log.warning("BT_PAIR but bluetooth source is disabled in profile")
                return
            try:
                from beatbird.sources.bluetooth import set_discoverable
                set_discoverable(True, timeout_s=60)
                # Optimistically push the bt_pairing flag so the standby
                # screen swaps in the QR + caption right now instead of
                # waiting up to 5 s for the next _refresh_system tick.
                # _push_system_now sends a fresh SYS line with bt=1.
                self.sys_bt_pairing = True
                self._last_bt_pairing = True
                self._push_system_now()
            except Exception as e:
                log.error("BT_PAIR failed: %s", e)
            return

        # SFX feedback. Driven by the logical command, not the backend,
        # so BT and Spotify both feel the same. PLAYPAUSE resolves to
        # play/pause based on the current view of state — the actual
        # backend round-trip can correct us later if we guessed wrong.
        if cmd == "NEXT":
            self.sfx.play("skip_next")
        elif cmd == "PREV":
            self.sfx.play("skip_prev")
        elif cmd == "PLAY":
            self.sfx.play("play")
        elif cmd == "PAUSE":
            self.sfx.play("pause")
        elif cmd == "PLAYPAUSE":
            if self.playback == Playback.PLAYING:
                self.sfx.play("pause")
            else:
                self.sfx.play("play")

        if self.source == Source.BLUETOOTH and self.bt:
            from beatbird.sources.bluetooth import send_avrcp
            active = self.bt.active_device()
            if active and send_avrcp(active.mac, cmd):
                pass
            else:
                log.debug("AVRCP failed for %s", cmd)
            self._push_state_now()
            return

        if not self.spotify:
            return

        # Explicit pause/resume — never a server-side toggle. go-librespot's
        # internal state can lag the bridge's view, so PLAYPAUSE could resolve
        # the wrong direction and either no-op or pause-then-resume.
        if cmd == "PLAYPAUSE":
            # self.playback is up to SPOTIFY_POLL_INTERVAL stale — fetch a
            # synchronous fresh view to avoid resolving the wrong direction
            # (e.g. another device toggled state since our last poll).
            fresh = self.spotify.get_state()
            if fresh is not None and not fresh.stopped:
                is_playing = not fresh.paused
            else:
                is_playing = (self.playback == Playback.PLAYING)
            if is_playing:
                self.spotify.pause()
                self.playback = Playback.PAUSED
            else:
                self.spotify.play()
                self.playback = Playback.PLAYING
                self.last_playback_time = time.monotonic()
        elif cmd == "PLAY":
            self.spotify.play()
            self.playback = Playback.PLAYING
            self.last_playback_time = time.monotonic()
        elif cmd == "PAUSE":
            self.spotify.pause()
            self.playback = Playback.PAUSED
        elif cmd == "NEXT":
            self.spotify.next()
        elif cmd == "PREV":
            self.spotify.prev()
        elif cmd == "STOP":
            self.spotify.close_session()

        # Optimistic push — next regular poll (≤2s) confirms or corrects.
        self._push_state_now()

    def _handle_mqtt_playback(self, value: str) -> None:
        if self.source == Source.BLUETOOTH:
            # Can't start/stop BT playback from MQTT — phone controls that
            return
        if not self.spotify:
            return
        if value == "Playing":
            self.spotify.play()
        elif value == "Paused":
            self.spotify.pause()
        elif value == "Stopped":
            self.spotify.close_session()

    # ─── Polling ────────────────────────────────────────────────────────────

    def _poll_spotify(self) -> None:
        if not self.spotify:
            return
        state = self.spotify.get_state()
        if state is None:
            self._spotify_fail_count += 1
            # Threshold reached exactly once → kick the service. Counter
            # resets only after a successful poll so we don't restart in a
            # tight loop if the restart itself doesn't recover.
            if self._spotify_fail_count == SPOTIFY_HEALTH_RESTART_THRESHOLD:
                log.warning(
                    "librespot unresponsive for ~%ds, restarting service",
                    int(self._spotify_fail_count * SPOTIFY_POLL_INTERVAL),
                )
                try:
                    subprocess.run(
                        ["systemctl", "restart", "go-librespot"],
                        capture_output=True, timeout=10,
                    )
                except Exception as e:
                    log.error("librespot restart failed: %s", e)
            if self.source == Source.SPOTIFY:
                self.playback = Playback.STOPPED
                self.source = Source.NONE
            return
        self._spotify_fail_count = 0

        if state.volume_steps > 0:
            self._sp_volume_steps = state.volume_steps

        # Bidirectional volume sync (Spotify app slider → CamillaDSP)
        if not state.stopped and state.volume is not None:
            sp_pct = round(state.volume * 100 / self._sp_volume_steps)
            if not self._spotify_initial_sync_done:
                # First observation after bridge start: CamillaDSP's persistent
                # volume wins. Push it to Spotify instead of letting Spotify's
                # initial=65535 (max) cascade into DSP and blast the speaker.
                self._spotify_initial_sync_done = True
                if abs(sp_pct - self.current_volume) > 3:
                    log.info(
                        "Initial Spotify sync: pushing CamillaDSP %d%% → Spotify (was %d%%)",
                        self.current_volume, sp_pct,
                    )
                    self.spotify.set_volume(self.current_volume, self._sp_volume_steps)
            elif abs(sp_pct - self.current_volume) > 3:
                log.info("Spotify volume → %d%% (syncing to CamillaDSP)", sp_pct)
                db = pct_to_db(sp_pct, self.vol_min_db, self.vol_max_db, self.vol_gamma)
                self.dsp.set_volume_db(db)
                self.current_volume = sp_pct
                self.current_volume_db = db
                if self.loudness:
                    self.loudness.apply(sp_pct)

        # State + metadata
        if state.stopped:
            if self._stopped_since is None:
                self._stopped_since = time.monotonic()
            if time.monotonic() - self._stopped_since < 8.0:
                if self.source != Source.SNAPCAST:
                    self.playback = Playback.STOPPED
                return
            # Don't clobber playback/title/artist/source if Snapcast is
            # currently the active source. With Spotify stopped and
            # Snapcast streaming, the old behaviour reset song_title to
            # "" every poll tick — which the Snapcast poll then re-set
            # to the real track title, triggering a split-flap every 3 s.
            # Also kept the energy ring intermittent because playback
            # flipped between PLAYING ↔ STOPPED every cycle.
            if self.source == Source.SNAPCAST:
                return
            self.playback = Playback.STOPPED
            if self.source == Source.SPOTIFY:
                self.source = Source.NONE
            self.song_title = ""
            self.song_artist = ""
            self.song_pos_ms = 0
        else:
            self._stopped_since = None
            # Source takeover only when Spotify is actively PLAYING — NOT
            # when merely paused. Without this guard, the BT handoff
            # livelocks: bridge hands off Spotify→BT, calls close_session,
            # go-librespot transitions through `paused` for a poll or two
            # before reaching `stopped`, the next Spotify poll sees
            # not-stopped → snatches the source back → disconnects BT
            # → phone reconnects → loop. Symptom for the user: phone
            # disconnects after ~2 s, reconnect fails until forget+repair
            # because every attempt hits the same livelock.
            if not state.paused and self.source != Source.SPOTIFY:
                self._transition_source(Source.SPOTIFY)
            self.playback = Playback.PAUSED if state.paused else Playback.PLAYING

        self.song_pos_ms = state.position_ms
        self.song_dur_ms = max(1, state.duration_ms)
        if state.title != self.song_title or state.artist != self.song_artist:
            self.song_title = state.title
            self.song_artist = state.artist
        if state.track_uri and state.track_uri != self._prev_track_uri:
            self._prev_track_uri = state.track_uri
            if state.title:
                log.info("Now playing: %s — %s", state.title, state.artist)
            # Background-thread cover fetch + push. If the URL is missing
            # (some librespot builds don't expose it), this is a no-op
            # and the firmware keeps whatever cover was last shown.
            self._kick_cover_fetch(state.track_uri, state.album_cover_url)

        # Stuck-state watchdog. When state.paused is FALSE (i.e. Spotify
        # thinks playback is running) but position_ms doesn't advance
        # between polls, go-librespot is wedged — usually after losing
        # the AP heartbeat. Manual workaround was a service restart; we
        # do that automatically after a generous grace period.
        self._spotify_stuck_check(state)

    def _poll_bluetooth(self) -> None:
        if not self.bt:
            return
        state = self.bt.poll()
        active = state.streaming_device

        if active:
            self.bt_device_alias = active.alias
            if self.source != Source.BLUETOOTH:
                # _on_bt_active already called by the BluetoothSource hook
                pass
            # AVRCP status, if the phone publishes it, beats our heuristic
            # "transport is active => playing" — some sender apps keep the
            # transport open while paused, and bluez exposes the player's
            # own pause/play state. Fall back to PLAYING when Status is
            # missing.
            if active.status == "paused":
                self.playback = Playback.PAUSED
            elif active.status == "stopped":
                self.playback = Playback.STOPPED
            else:
                self.playback = Playback.PLAYING
            # Title/Artist from AVRCP if the phone publishes them, else
            # the device alias so the slot isn't empty (e.g. Audible /
            # some podcast apps don't expose Track metadata at all).
            self.song_title  = active.title or active.alias
            self.song_artist = active.artist
            self.song_pos_ms = active.position_ms
            self.song_dur_ms = max(1, active.duration_ms)
        else:
            if self.source == Source.BLUETOOTH:
                self.source = Source.NONE
                self.playback = Playback.STOPPED
                self.song_title = ""
                self.song_artist = ""
                self.bt_device_alias = ""

    # ─── Runtime tunables (web UI settings page) ────────────────────────────

    def _apply_idle_settings(self, rss_url: str, rss_refresh_minutes: int,
                              rss_weight: float) -> None:
        """Reconfigure the standby idle source. No-op if the parameters
        match the running fetcher so a palette-only override-poll doesn't
        thrash the RSS thread every 5 seconds."""
        cur_url = self.rss.url if self.rss else ""
        cur_refresh = (self.rss.refresh_s // 60) if self.rss else 0
        if cur_url == rss_url and cur_refresh == rss_refresh_minutes:
            self._rss_weight = rss_weight
            return
        if self.rss is not None:
            self.rss.stop()
            self.rss = None
        if rss_url:
            self.rss = RssFetcher(
                rss_url, refresh_minutes=rss_refresh_minutes,
                max_chars=self._idle_max_chars,
            )
            self.rss.start()
            log.info("rss idle source: %s", rss_url)
        self._rss_weight = rss_weight

    def _apply_overrides(self, data: dict, initial: bool = False) -> None:
        """Layer web-UI overrides on top of profile defaults. Called once
        at startup (initial=True) and again whenever the overrides file
        mtime changes.

        Palette is computed as profile-defaults overlaid with override
        slots and pushed as a complete set — clearing the override slot
        (POST {"palette": {}}) reverts to profile colours without a
        bridge restart. The display layer dedupes the resulting PAL:
        line via _palette_sent only if nothing actually changed."""
        d = self.profile.display
        profile_palette: dict[str, str | None] = {
            "a": d.accent_color,
            "g": d.accent_glow,
            "d": d.accent_dim,
            "p": d.text_primary,
            "s": d.text_secondary,
            "e": d.accent_alert,
        }
        ov_palette = data.get("palette") if isinstance(data.get("palette"), dict) else {}
        effective_palette = {k: ov_palette.get(k) or profile_palette.get(k)
                             for k in profile_palette}
        if self.display:
            try:
                self.display.set_palette(effective_palette)
                if not initial:
                    log.info("overrides: palette effective %s", effective_palette)
            except Exception as e:
                log.warning("overrides palette apply failed: %s", e)

        # Idle: override wins, else profile defaults.
        idle = data.get("idle") if isinstance(data.get("idle"), dict) else {}
        rss_url = idle.get("rss_url", self.profile.idle.rss_url)
        rss_refresh = int(idle.get("rss_refresh_minutes", self.profile.idle.rss_refresh_minutes))
        rss_weight = float(idle.get("rss_weight", self.profile.idle.rss_weight))
        self._apply_idle_settings(rss_url, rss_refresh, rss_weight)
        if idle and not initial:
            log.info("overrides: idle updated %s", idle)

    def _poll_overrides(self) -> None:
        """File-mtime check, applies if changed. Called from main loop
        once per status tick — no syscall storm, no signal plumbing."""
        m = settings_overrides.mtime()
        if m is None or m == self._overrides_mtime:
            return
        self._overrides_mtime = m
        self._apply_overrides(settings_overrides.load())

    def _kick_cover_fetch(self, uri: str, url: str) -> None:
        """Daemon-thread cover processor + display.push_cover. Returns
        immediately so the poll loop doesn't wait on URL download (~100-
        500 ms) + Pillow processing (~100-200 ms). Multiple in-flight
        requests are fine — the processor's URI cache dedupes.

        No-op when profile.display.cover_background.enabled is false —
        the current ESP32-S3 firmware stutters under the full-screen
        JPEG composite, so we keep the feature off-by-default."""
        if not self.display or not url or self.cover_proc is None:
            return

        def _worker():
            try:
                jpeg = self.cover_proc.get(uri, url)
                if jpeg:
                    self.display.push_cover(jpeg)
            except Exception as e:
                log.warning("cover fetch/push for %s failed: %s", uri, e)

        import threading
        threading.Thread(target=_worker, daemon=True, name="cover-fetch").start()

    def _log_wifi_snapshot(self, reason: str) -> None:
        """Dump current WiFi state to the journal when we detect trouble.
        Pairs with the periodic telemetry from beatbird-wifi-watchdog: the
        watchdog logs RSSI continuously, this logs the bridge's *reason*
        for caring at this instant. Together they let post-mortems answer
        'was the link actually bad when the session died, or did it die
        for other reasons while the link was fine?'."""
        try:
            iface = ""
            for entry in os.listdir("/sys/class/net"):
                if entry.startswith("wlan"):
                    state_file = f"/sys/class/net/{entry}/operstate"
                    try:
                        with open(state_file) as f:
                            if f.read().strip() == "up":
                                iface = entry
                                break
                    except OSError:
                        pass
                    if not iface:
                        iface = entry
            link = subprocess.run(
                ["iw", "dev", iface or "wlan0", "link"],
                capture_output=True, text=True, timeout=2,
            ).stdout if iface else ""
            link_one = " | ".join(
                l.strip() for l in link.splitlines()
                if any(k in l for k in ("Connected to", "signal:", "tx bitrate:", "SSID:"))
            ) or "no link"
            log.warning("wifi-snapshot (%s): iface=%s %s", reason, iface or "?", link_one)
        except Exception as e:
            log.warning("wifi-snapshot (%s) failed: %s", reason, e)

    def _spotify_stuck_check(self, state) -> None:
        """Detect go-librespot's "AP heartbeat lost, track-load fails"
        state and auto-restart the service. Trigger conditions:
          - state.paused == False (Spotify wants playback)
          - state.title is set (a track is loaded)
          - position_ms hasn't advanced in STUCK_GRACE_S seconds

        Grace period is generous so a real pause-and-think (e.g. someone
        scrubbing the slider) doesn't trip it. After the restart we log
        what happened so the journal makes the cause obvious.
        """
        STUCK_GRACE_S = 30.0
        now = time.monotonic()
        # Reset baseline whenever state should NOT be progressing.
        # state could be None on call-from-other-paths; guard.
        if state is None or state.paused or state.stopped or not state.title:
            self._sp_last_progress_ms = -1
            self._sp_last_progress_t  = now
            return

        # Position changed at least 250 ms? Healthy.
        if (self._sp_last_progress_ms < 0
                or abs(state.position_ms - self._sp_last_progress_ms) > 250):
            self._sp_last_progress_ms = state.position_ms
            self._sp_last_progress_t  = now
            return

        # No progress within grace — restart.
        if now - self._sp_last_progress_t >= STUCK_GRACE_S:
            self._sp_stuck_restarts += 1
            self._sp_stuck_recent_t = now   # drives SYS:ss= → "SPOTIFY RECONNECTING" overlay
            log.warning(
                "Spotify stuck (no position progress for %.0fs while "
                "playback=PLAYING, track=%r) — restarting go-librespot "
                "[restart #%d]",
                now - self._sp_last_progress_t, state.title,
                self._sp_stuck_restarts,
            )
            self._log_wifi_snapshot("spotify-stuck")
            try:
                subprocess.run(
                    ["sudo", "systemctl", "restart", "go-librespot"],
                    capture_output=True, timeout=10,
                )
            except Exception as e:
                log.error("go-librespot restart failed: %s", e)
            # Reset so we don't fire again immediately while the service
            # is mid-restart and reports no state.
            self._sp_last_progress_ms = -1
            self._sp_last_progress_t  = now

    def _poll_snapcast(self) -> None:
        """Snapserver poll. Behaviour:
          - When our snap-group is streaming AND Spotify isn't actively
            playing, source flips to SNAPCAST and stays there.
          - last_playback_time refreshes on every tick while playing, so
            the idle-timeout doesn't trip on a long Snapcast session.
          - Per-client volume (MA UI) is mirrored into the displayed
            volume so the ring matches what MA shows.
          - Group name is shown as the title — for MA's `ma_<mac>` naming
            it's not pretty, but it identifies the stream.
        Audio routing itself is handled by snapclient.service — bridge
        is observation + display only."""
        if not self.snapcast:
            return
        state = self.snapcast.get_state()
        if not state:
            return

        playing = bool(state["playing"])

        # Spotify wins if it's actively pushing audio (covers the
        # standard "this speaker plays Spotify" case).
        if playing and self.source == Source.SPOTIFY and self.playback == Playback.PLAYING:
            return

        if playing:
            if self.source != Source.SNAPCAST or not self._snapcast_playing:
                log.info("Snapcast source active (group=%s)", state["group_name"])
                self._transition_source(Source.SNAPCAST)
            self.playback = Playback.PLAYING
            self.last_playback_time = time.monotonic()
            # Real track metadata if MA published it on any stream;
            # otherwise fall back to a sensible label so the slot isn't
            # empty.
            title  = state.get("title") or ""
            artist = state.get("artist") or ""
            if not title:
                title = "Multiroom"
            if self.song_title != title or self.song_artist != artist:
                self.song_title  = title
                self.song_artist = artist
            # Mirror per-client volume to the display so the ring tracks
            # MA-side changes too. Only push when it actually changed.
            v = max(0, min(100, int(state["volume_pct"])))
            if v != self.current_volume:
                self.current_volume = v
                # Push to State without touching CDSP master — CDSP master
                # is independent and stays under local rotary control.
        elif self._snapcast_playing:
            log.info("Snapcast source idle")
            if self.source == Source.SNAPCAST:
                self.source = Source.NONE
                self.playback = Playback.STOPPED
                self.song_title = ""
                self.song_artist = ""
        self._snapcast_playing = playing

    def _refresh_system(self) -> None:
        self.sys_cpu = system.cpu_temp()
        self.sys_wifi = system.wifi_rssi()
        self.sys_amp = self.hardware.read_status()
        self.sys_dsp = system.service_active("camilladsp")
        self.sys_spotify = system.service_active("go-librespot")
        # gateway_reachable does one ping with 1.5s timeout — fine at the
        # 5s _refresh_system cadence. Source of the SYS:gw= flag the
        # display turns into a "NO NETWORK" overlay.
        self.sys_gateway = system.gateway_reachable()
        # BT discoverable mode is a transient user action (web UI opens
        # a 60 s window via bluetoothctl). Polled at the same cadence as
        # the other system stats so the firmware learns about it within
        # ~5 s of the user pressing the pair button.
        if self.bt is not None:
            try:
                from beatbird.sources.bluetooth import is_discoverable
                self.sys_bt_pairing = is_discoverable()
            except Exception as e:
                log.debug("bt discoverable check: %s", e)
                self.sys_bt_pairing = False
        else:
            self.sys_bt_pairing = False

        # Edge-triggered standby flap override. Player screen handles the
        # PAIRING badge via CenterStage; the standby screen hijacks its
        # idle-text label for the duration so the indicator is visible
        # without forcing a screen switch. On falling edge we push a
        # fresh idle message so the screen doesn't stay frozen on
        # "PAIRING MODE" until the next 45 s rotation.
        if self.sys_bt_pairing != self._last_bt_pairing:
            if self.sys_bt_pairing and self.in_standby and self.display:
                try:
                    self.display.push_idle_message(self._pairing_idle_text())
                    self._idle_msg_t = time.monotonic()
                except Exception as e:
                    log.debug("push pairing idle msg: %s", e)
            elif not self.sys_bt_pairing and self.in_standby:
                # Pairing window closed — kick a normal idle rotation so
                # the screen doesn't sit on the stale PAIRING text.
                try:
                    self._send_idle_message()
                except Exception as e:
                    log.debug("post-pairing idle rotate: %s", e)
            self._last_bt_pairing = self.sys_bt_pairing

        db = self.dsp.get_volume_db()
        if db is not None:
            self.current_volume_db = db
            new_pct = db_to_pct(db, self.vol_min_db, self.vol_max_db, self.vol_gamma)
            if new_pct != self.current_volume:
                self.current_volume = new_pct

    # ─── Pushing ────────────────────────────────────────────────────────────

    def _push_state_now(self) -> None:
        # Don't clobber the shutdown-warn screen while user is holding the button.
        if self._shutdown_warn_active:
            return
        if self.in_standby:
            playback_str = "standby"
            spectrum = None
        else:
            state_map = {
                Playback.PLAYING: "play", Playback.PAUSED: "pause",
                Playback.STOPPED: "stop",
            }
            playback_str = state_map.get(self.playback, "stop")
            spectrum = self.spectrum.get_bands() if self.spectrum else None
        state = DisplayState(
            playback=playback_str,
            source=self.source.value,
            title="" if self.in_standby else self.song_title,
            artist="" if self.in_standby else self.song_artist,
            volume=self.current_volume,
            position_ms=0 if self.in_standby else self.song_pos_ms,
            duration_ms=1 if self.in_standby else self.song_dur_ms,
            signal_level=self.signal_level,
            time_hhmm=time.strftime("%H:%M"),
            spectrum=spectrum,
        )
        if self.display:
            self.display.push_state(state)

    def _idle_timeout(self) -> float:
        """How long to sit on the player screen before switching to standby.

        The default 60s grace is right when a track is loaded and the user
        might come back to resume — but for a freshly-booted speaker with
        no source yet, holding on an empty player screen for a minute
        looks broken. Cases where the player screen has nothing meaningful
        on it drop down to short timeouts so the clock+weather standby
        screen comes up quickly instead.
        """
        # Nothing connected / never played anything this session — the
        # player screen would just be empty slots. Standby is more useful.
        if self.source == Source.NONE or not self.song_title:
            return 10.0
        # Track was loaded but playback stopped (queue ended, or remote
        # stopped). Moderate window so a quick resume still feels live.
        if self.playback == Playback.STOPPED:
            return 30.0
        # Paused with a track loaded — keep the full grace, the user
        # likely walked away mid-song and will be back to resume.
        return STANDBY_TIMEOUT_S

    def _pairing_idle_text(self) -> str:
        """Standby flap text shown during a pairing window. Includes the
        speaker's friendly_name so a household with three speakers can
        tell at a glance which one accepted the pair-mode click. ASCII-
        sanitised + uppercase to match the airport-board styling and the
        split-flap byte-cycle constraints. The label is fixed-width with
        SCROLL_CIRCULAR, so a long name just marquees."""
        from beatbird.rss_fetcher import _sanitise   # same sanitiser as RSS
        name = _sanitise(self.profile.identity.friendly_name)
        return f"PAIRING {name}" if name else "PAIRING MODE"

    def _send_idle_message(self) -> None:
        """Pick the next standby flap line and push it to the display.

        Pool composition:
          - IDLE_MESSAGES (hard-coded German airport-board style)
          - RSS headlines, if a feed is configured AND has items

        When RSS is populated, profile.idle.rss_weight (0..1) decides how
        often we pick from the RSS pool vs the local one. Last-shown line
        is never picked again immediately, so two consecutive rotations
        are guaranteed different even with a tiny RSS pool.
        """
        import random
        if not self.display:
            return
        # During a pairing window, the flap label is owned by the
        # PAIRING-MODE override. Skip the regular rotation so a 45 s tick
        # doesn't replace it with a random airport-board line mid-window.
        if self.sys_bt_pairing:
            msg = self._pairing_idle_text()
            try:
                self.display.push_idle_message(msg)
                self._last_idle_msg = msg
                self._idle_msg_t = time.monotonic()
            except Exception as e:
                log.warning("push_idle_message (pairing) failed: %s", e)
            return
        rss_pool = self.rss.headlines if self.rss else []
        # Weighted source choice — fall back to local pool if RSS is
        # empty (boot, fetch failure) so the screen always has content.
        use_rss = rss_pool and random.random() < self._rss_weight
        pool = rss_pool if use_rss else IDLE_MESSAGES
        choices = [m for m in pool if m != self._last_idle_msg] or pool
        msg = random.choice(choices)
        self._last_idle_msg = msg
        self._idle_msg_t = time.monotonic()
        try:
            self.display.push_idle_message(msg)
        except Exception as e:
            log.warning("push_idle_message failed: %s", e)

    def _enter_standby(self, reason: str = "idle timeout") -> None:
        log.info("entering standby (%s)", reason)
        # Goodnight sound *before* the close_session, while audio is
        # still flowing through CamillaDSP. close_session is what
        # silences Spotify, so playing the SFX after would route into
        # the void on the next bridge tick.
        self.sfx.play("standby")
        self.in_standby = True
        if self.spotify:
            try:
                self.spotify.close_session()
            except Exception as e:
                log.warning("close_session on standby failed: %s", e)
        self._push_state_now()
        # Kick off the standby flap text immediately so the screen lands
        # with content already on it, not "STANDBY" → flap-replace later.
        self._send_idle_message()

    def _exit_standby(self, reason: str) -> None:
        log.info("exit standby (%s)", reason)
        self.in_standby = False
        self.last_playback_time = time.monotonic()

    # ─── Power button callbacks ─────────────────────────────────────────────

    def _on_power_warn(self) -> None:
        # Fires while the user is holding the button. Push a "shutdown_warn"
        # state so a firmware that supports it can render a halt-confirm
        # screen; older firmware ignores the unknown ST value but the TI: line
        # is still displayed, so the user gets at least textual feedback.
        self._shutdown_warn_active = True
        self._push_shutdown_state("shutdown_warn", "Halten zum Ausschalten")

    def _on_power_cancel(self) -> None:
        self._shutdown_warn_active = False
        self._push_state_now()  # back to normal rendering

    def _on_power_confirm(self) -> None:
        self._push_shutdown_state("shutdown", "Ausschalten…")
        try:
            subprocess.Popen(
                ["sudo", "-n", "/sbin/poweroff"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            log.error("poweroff failed: %s", e)

    def _push_shutdown_state(self, playback: str, title: str) -> None:
        if not self.display:
            return
        state = DisplayState(
            playback=playback,
            source=self.source.value,
            title=title,
            artist="",
            volume=self.current_volume,
            position_ms=0,
            duration_ms=1,
            signal_level=0,
            time_hhmm=time.strftime("%H:%M"),
            spectrum=None,
        )
        self.display.push_state(state)

    def _push_system_now(self) -> None:
        # stuck-restart counts as "recent" for 60s — long enough for the
        # display to show "SPOTIFY RECONNECTING" through the actual reconnect.
        stuck_recent = (
            self._sp_stuck_recent_t > 0
            and (time.monotonic() - self._sp_stuck_recent_t) < 60.0
        )
        if self.display:
            self.display.push_system(DisplaySystemStatus(
                cpu_temp=self.sys_cpu,
                wifi_rssi=self.sys_wifi,
                amp_statuses=self.sys_amp,
                dsp_active=self.sys_dsp,
                spotify_active=self.sys_spotify,
                gateway_reachable=self.sys_gateway,
                spotify_stuck_recent=stuck_recent,
                bt_pairing=self.sys_bt_pairing,
            ))
        self.mqtt.publish_status({
            "cpu_temp":    round(self.sys_cpu, 1),
            "amp_stereo":  self.sys_amp.get("stereo", "---"),
            "amp_sub":     self.sys_amp.get("sub", "---"),
            "camilladsp":  "active" if self.sys_dsp else "stopped",
            "spotify":     "active" if self.sys_spotify else "stopped",
            "playback":    self.playback.value,
            "source":      self.source.value,
            "song_title":  self.song_title,
            "song_artist": self.song_artist,
            "volume":      self.current_volume,
            "volume_db":   round(self.current_volume_db, 1),
            "wifi_rssi":   self.sys_wifi,
            "bt_device":   self.bt_device_alias or "—",
        })

    # ─── Main loop ──────────────────────────────────────────────────────────

    def run(self) -> None:
        # Send READY=1 BEFORE start() — systemd's TimeoutStartSec only
        # cares about the gap from fork to READY=1. start() does display
        # init / weather poller / mqtt connect which can each take a
        # second or two; the watchdog (WatchdogSec=90) in the main loop
        # catches a real hang. Sending READY early just says "the python
        # process is alive and entering the main flow".
        sd_notify_local("READY=1")
        self.start()
        try:
            while True:
                sd_notify_local("WATCHDOG=1")
                now = time.monotonic()

                if self.display:
                    self.display.poll()

                # System stats (every 5s)
                if now - self.t_last_status >= STATUS_INTERVAL:
                    self.t_last_status = now
                    try:
                        self._refresh_system()
                        self._push_system_now()
                    except Exception as e:
                        log.error("status refresh: %s", e)
                    # Piggyback on the 5s tick to check whether the web UI
                    # has written new settings — stat() is cheap and we
                    # only reload the JSON when mtime changes.
                    try:
                        self._poll_overrides()
                    except Exception as e:
                        log.error("overrides poll: %s", e)

                # Source polling (every 2s)
                if now - self.t_last_spotify >= SPOTIFY_POLL_INTERVAL:
                    self.t_last_spotify = now
                    try:
                        self._poll_spotify()
                    except Exception as e:
                        log.error("spotify poll: %s", e)
                    try:
                        self._poll_bluetooth()
                    except Exception as e:
                        log.error("bt poll: %s", e)

                # Snapcast source detection (every 3s — cheap TCP poll)
                if self.snapcast and now - self.t_last_snapcast >= SNAPCAST_POLL_INTERVAL:
                    self.t_last_snapcast = now
                    try:
                        self._poll_snapcast()
                    except Exception as e:
                        log.error("snapcast poll: %s", e)
                        self._log_wifi_snapshot("snapcast-poll-error")

                    # Standby transitions: track last PLAYING observation, enter
                    # standby after a content-adaptive idle timeout, exit on
                    # any new playback. See _idle_timeout() for the policy.
                    if self.playback == Playback.PLAYING:
                        self.last_playback_time = now
                        if self.in_standby:
                            self._exit_standby("playback resumed")
                    elif (
                        not self.in_standby
                        and now - self.last_playback_time >= self._idle_timeout()
                    ):
                        try:
                            self._enter_standby()
                        except Exception as e:
                            log.error("standby enter: %s", e)

                    # Rotate the standby flap text every IDLE_MESSAGE_INTERVAL
                    # while idle. Keeps the screen from looking frozen — a
                    # fresh airport-board line flips in every ~45s.
                    if (
                        self.in_standby
                        and now - self._idle_msg_t >= IDLE_MESSAGE_INTERVAL
                    ):
                        self._send_idle_message()

                # Signal level
                level_interval = (
                    LEVEL_POLL_INTERVAL
                    if self.playback == Playback.PLAYING
                    else 2.0
                )
                if now - self.t_last_level >= level_interval:
                    self.t_last_level = now
                    try:
                        self.signal_level = self.dsp.get_signal_level()
                    except Exception:
                        pass

                # State push
                push_interval = (
                    STATE_PUSH_PLAYING
                    if self.playback == Playback.PLAYING
                    else STATE_PUSH_IDLE
                )
                if now - self.t_last_state_push >= push_interval:
                    self.t_last_state_push = now
                    try:
                        self._push_state_now()
                    except Exception as e:
                        log.error("state push: %s", e)

                time.sleep(0.05)
        finally:
            self.stop()


# ─── Entrypoint ──────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=os.environ.get("BEATBIRD_LOGLEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    profile = load_profile()
    bridge = BeatBirdBridge(profile)

    def _sig(*_):
        log.info("signal received, exiting")
        bridge.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    bridge.run()


if __name__ == "__main__":
    main()
