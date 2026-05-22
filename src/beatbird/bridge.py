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
from beatbird.audio.spectrum import SpectrumAnalyzer
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
LEVEL_POLL_INTERVAL = 0.1
STATE_PUSH_PLAYING = 0.2
STATE_PUSH_IDLE = 2.0
STANDBY_TIMEOUT_S = 60.0
# Restart go-librespot if /status hangs for this many consecutive polls.
# At SPOTIFY_POLL_INTERVAL=2s, 15 = 30s of unresponsiveness before action.
SPOTIFY_HEALTH_RESTART_THRESHOLD = 15


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
    known_base = {
        "bass_shelf":   {"type": "Lowshelf", "freq": 120, "base_gain": 10, "q": 0.6},
        "sub_punch":    {"type": "Peaking",  "freq": 45,  "base_gain": 5,  "q": 0.7},
        "timpani_body": {"type": "Peaking",  "freq": 70,  "base_gain": 3,  "q": 1.0},
        "fullness":     {"type": "Peaking",  "freq": 200, "base_gain": 3,  "q": 1.0},
    }
    # P1: Try to read base gains from CamillaDSP config
    cdsp_config = dsp.get_config()
    if cdsp_config:
        filters_cfg = cdsp_config.get("filters", {})
        for name in known_base:
            if name in filters_cfg:
                params = filters_cfg[name].get("parameters", {})
                if "gain" in params:
                    known_base[name]["base_gain"] = params["gain"]
                if "freq" in params:
                    known_base[name]["freq"] = params["freq"]
                if "q" in params:
                    known_base[name]["q"] = params["q"]
                if "type" in params:
                    known_base[name]["type"] = params["type"]
        log.info("Loudness base gains loaded from CamillaDSP config")

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


def _build_bluetooth(profile: Profile, on_active, on_volume):
    """Build BT source tracker if bluetooth source is configured."""
    if not profile.sources.bluetooth.enabled:
        return None
    try:
        from beatbird.sources.bluetooth import BluetoothSource
        return BluetoothSource(
            on_became_active=on_active,
            on_volume_from_phone=on_volume,
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

        # BT source (wired up after init so callbacks can reference self)
        self.bt = _build_bluetooth(
            profile,
            on_active=self._on_bt_active,
            on_volume=self._on_bt_volume,
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

        # ── Standby ──
        # last_playback_time = monotonic timestamp of last PLAYING observation.
        # After STANDBY_TIMEOUT_S of non-PLAYING, enter standby: display → clock,
        # /player/close frees the Spotify Connect slot so no other device can
        # silently take over the speaker at night.
        self.last_playback_time: float = time.monotonic()
        self.in_standby: bool = False

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

        # ── Timing ──
        self.t_last_status = 0.0
        self.t_last_spotify = 0.0
        self.t_last_snapcast = 0.0
        self.t_last_level = 0.0
        self.t_last_state_push = 0.0
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

    def _on_bt_volume(self, pct: int) -> None:
        """Phone slider moved → sync to CamillaDSP."""
        log.info("BT phone volume → %d%%", pct)
        self.set_volume(pct)

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
        if self.spectrum:
            self.spectrum.start()
        if self.power_button:
            self.power_button.start()
        self._start_weather_poller()
        self.mqtt.start()

        # Initial sync: CamillaDSP's persistent volume wins, with two safeguards.
        # 1) If the DSP is unreachable at start (rare race vs. systemd order),
        #    fall back to a quiet-but-audible default — better than silent
        #    (current_volume=0 from __init__ would persist) or current_volume
        #    leaking a stale value into the first Spotify sync push.
        # 2) If the DSP volume is above the profile's safe ceiling (max_db,
        #    typically -10 dB), the persistent state file is almost certainly
        #    fresh/wiped — CamillaDSP defaults to 0 dB in that case. Snap to a
        #    conservative "first boot" volume instead of letting the speaker
        #    blast at full output. The user can immediately turn it up if
        #    they want; that's recoverable. A first-boot blast is not.
        SAFE_FIRST_BOOT_PCT = 25
        db = self.dsp.get_volume_db()
        if db is None:
            log.warning(
                "DSP volume unreadable at start, defaulting to %d%%",
                SAFE_FIRST_BOOT_PCT,
            )
            self.current_volume = SAFE_FIRST_BOOT_PCT
            self.current_volume_db = pct_to_db(
                SAFE_FIRST_BOOT_PCT, self.vol_min_db, self.vol_max_db, self.vol_gamma,
            )
            self.dsp.set_volume_db(self.current_volume_db)
        elif db > self.vol_max_db + 0.5:
            log.warning(
                "DSP volume %.1f dB exceeds profile max %.1f dB (stale state?), "
                "snapping to %d%%",
                db, self.vol_max_db, SAFE_FIRST_BOOT_PCT,
            )
            self.current_volume = SAFE_FIRST_BOOT_PCT
            self.current_volume_db = pct_to_db(
                SAFE_FIRST_BOOT_PCT, self.vol_min_db, self.vol_max_db, self.vol_gamma,
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
        # Mirror to the active source so their slider stays in sync
        if self.source == Source.SPOTIFY and self.spotify:
            val = round(pct / 100.0 * self._sp_volume_steps)
            self.spotify.set_volume(pct, self._sp_volume_steps)
        elif self.source == Source.BLUETOOTH and self.bt:
            self.bt.push_volume_to_phone(pct)

    # ─── Display / MQTT callbacks ───────────────────────────────────────────

    def _handle_display_command(self, cmd: str) -> None:
        log.info("display → CMD:%s", cmd)
        if self._shutdown_warn_active:
            return  # user holding power button — don't accept display input
        if self.in_standby:
            self._exit_standby("user command")

        # WAKE: tap-to-wake from the standby screen. Side-effect-free — the
        # exit_standby call above is the whole point. Don't forward it to
        # AVRCP / Spotify; those would either no-op or misinterpret.
        if cmd == "WAKE":
            return

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
                self.playback = Playback.STOPPED
                return
            self.playback = Playback.STOPPED
            if self.source == Source.SPOTIFY:
                self.source = Source.NONE
            self.song_title = ""
            self.song_artist = ""
            self.song_pos_ms = 0
        else:
            self._stopped_since = None
            # Spotify is actively streaming — take over source
            if self.source != Source.SPOTIFY:
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
            self.playback = Playback.PLAYING
            self.song_title = active.alias
            self.song_artist = ""
            self.song_pos_ms = 0
            self.song_dur_ms = 1
        else:
            if self.source == Source.BLUETOOTH:
                self.source = Source.NONE
                self.playback = Playback.STOPPED
                self.song_title = ""
                self.song_artist = ""
                self.bt_device_alias = ""

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
            # Use the snap group name as a track-like label. MA labels
            # groups `ma_<MAC>`; trim that prefix for readability.
            label = state["group_name"] or "Snapcast"
            if label.startswith("ma_"):
                label = "Multiroom"
            if self.song_title != label:
                self.song_title = label
                self.song_artist = ""
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

    def _enter_standby(self, reason: str = "idle timeout") -> None:
        log.info("entering standby (%s)", reason)
        self.in_standby = True
        if self.spotify:
            try:
                self.spotify.close_session()
            except Exception as e:
                log.warning("close_session on standby failed: %s", e)
        self._push_state_now()

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
        if self.display:
            self.display.push_system(DisplaySystemStatus(
                cpu_temp=self.sys_cpu,
                wifi_rssi=self.sys_wifi,
                amp_statuses=self.sys_amp,
                dsp_active=self.sys_dsp,
                spotify_active=self.sys_spotify,
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
        self.start()
        try:
            while True:
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

                    # Standby transitions: track last PLAYING observation, enter
                    # standby after idle timeout, exit on any new playback.
                    if self.playback == Playback.PLAYING:
                        self.last_playback_time = now
                        if self.in_standby:
                            self._exit_standby("playback resumed")
                    elif (
                        not self.in_standby
                        and now - self.last_playback_time >= STANDBY_TIMEOUT_S
                    ):
                        try:
                            self._enter_standby()
                        except Exception as e:
                            log.error("standby enter: %s", e)

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
