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
from beatbird.sources.spotify import SpotifyClient
from beatbird import system

log = logging.getLogger("beatbird.bridge")

STATUS_INTERVAL = 5.0
SPOTIFY_POLL_INTERVAL = 2.0
LEVEL_POLL_INTERVAL = 0.1
STATE_PUSH_PLAYING = 0.2
STATE_PUSH_IDLE = 2.0


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
    return LoudnessController(dsp, filters) if filters else None


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

        self.dsp = CamillaDSP()
        self.spotify = SpotifyClient() if profile.sources.spotify.enabled else None
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

        # ── System stats ──
        self.sys_amp: dict[str, str] = {}
        self.sys_cpu = 0.0
        self.sys_wifi = 0
        self.sys_dsp = False
        self.sys_spotify = False

        # ── Timing ──
        self.t_last_status = 0.0
        self.t_last_spotify = 0.0
        self.t_last_level = 0.0
        self.t_last_state_push = 0.0

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
        self.mqtt.start()

        # Initial sync: CamillaDSP's saved volume wins.
        db = self.dsp.get_volume_db()
        if db is not None:
            self.current_volume_db = db
            self.current_volume = db_to_pct(db, self.vol_min_db, self.vol_max_db)

    def stop(self) -> None:
        log.info("shutting down")
        if self.spectrum:
            self.spectrum.stop()
        self.mqtt.stop()
        self.dsp.close()
        if self.display:
            self.display.close()

    # ─── Volume (single source of truth: CamillaDSP) ────────────────────────

    def set_volume(self, pct: int) -> None:
        pct = max(0, min(100, pct))
        db = pct_to_db(pct, self.vol_min_db, self.vol_max_db)
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
        if self.source == Source.BLUETOOTH and self.bt:
            from beatbird.sources.bluetooth import send_avrcp
            active = self.bt.active_device()
            if active and send_avrcp(active.mac, cmd):
                pass
            else:
                log.debug("AVRCP failed for %s", cmd)
        elif self.spotify:
            if cmd in ("PLAY", "PLAYPAUSE"):
                self.spotify.playpause()
            elif cmd == "PAUSE":
                self.spotify.pause()
            elif cmd == "NEXT":
                self.spotify.next()
            elif cmd == "PREV":
                self.spotify.prev()
            elif cmd == "STOP":
                self.spotify.close_session()
        # Immediate refresh
        time.sleep(0.3)
        self._poll_spotify()
        if self.bt:
            self._poll_bluetooth()
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
            if self.source == Source.SPOTIFY:
                self.playback = Playback.STOPPED
                self.source = Source.NONE
            return

        if state.volume_steps > 0:
            self._sp_volume_steps = state.volume_steps

        # Bidirectional volume sync (Spotify app slider → CamillaDSP)
        if not state.stopped and state.volume is not None:
            sp_pct = round(state.volume * 100 / self._sp_volume_steps)
            if abs(sp_pct - self.current_volume) > 3:
                log.info("Spotify volume → %d%% (syncing to CamillaDSP)", sp_pct)
                db = pct_to_db(sp_pct, self.vol_min_db, self.vol_max_db)
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

    def _refresh_system(self) -> None:
        self.sys_cpu = system.cpu_temp()
        self.sys_wifi = system.wifi_rssi()
        self.sys_amp = self.hardware.read_status()
        self.sys_dsp = system.service_active("camilladsp")
        self.sys_spotify = system.service_active("go-librespot")

        db = self.dsp.get_volume_db()
        if db is not None:
            self.current_volume_db = db
            new_pct = db_to_pct(db, self.vol_min_db, self.vol_max_db)
            if new_pct != self.current_volume:
                self.current_volume = new_pct

    # ─── Pushing ────────────────────────────────────────────────────────────

    def _push_state_now(self) -> None:
        state_map = {
            Playback.PLAYING: "play", Playback.PAUSED: "pause",
            Playback.STOPPED: "stop",
        }
        spectrum = self.spectrum.get_bands() if self.spectrum else None
        state = DisplayState(
            playback=state_map.get(self.playback, "stop"),
            source=self.source.value,
            title=self.song_title,
            artist=self.song_artist,
            volume=self.current_volume,
            position_ms=self.song_pos_ms,
            duration_ms=self.song_dur_ms,
            signal_level=self.signal_level,
            time_hhmm=time.strftime("%H:%M"),
            spectrum=spectrum,
        )
        if self.display:
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
