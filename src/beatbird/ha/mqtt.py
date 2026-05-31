"""
beatbird.ha.mqtt — MQTT client with Home Assistant auto-discovery.

Takes a Profile and exposes the speaker as a set of HA entities: a bunch of
sensors, a number control for volume, and a select for playback state.

All topics derive from ``profile.mqtt_topic_base``. The availability topic
uses LWT so HA marks the device offline as soon as the Pi drops.
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from beatbird.config import Profile

log = logging.getLogger("beatbird.mqtt")


class MqttBridge:
    def __init__(
        self,
        profile: Profile,
        mqtt_password: str = "",
        on_set_volume: Callable[[int], None] | None = None,
        on_set_playback: Callable[[str], None] | None = None,
    ):
        self.profile = profile
        self.password = mqtt_password
        self.on_set_volume = on_set_volume
        self.on_set_playback = on_set_playback

        self.client = None
        self.available = False

        self.topic_base = profile.mqtt_topic_base
        self.topic_status = f"{self.topic_base}/status"
        self.topic_event = f"{self.topic_base}/event"
        self.topic_availability = f"{self.topic_base}/availability"
        self.topic_set_volume = f"{self.topic_base}/set/volume"
        self.topic_set_playback = f"{self.topic_base}/set/playback"

    # ─── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self.profile.mqtt.enabled:
            log.info("MQTT disabled in profile")
            return
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.warning("paho-mqtt not installed — MQTT disabled")
            return

        client = mqtt.Client(
            client_id=self.profile.identity.speaker_id,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        if self.profile.mqtt.user:
            client.username_pw_set(self.profile.mqtt.user, self.password)
        client.will_set(self.topic_availability, "offline", qos=1, retain=True)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        try:
            client.connect_async(self.profile.mqtt.host, self.profile.mqtt.port, keepalive=60)
            client.loop_start()
            self.client = client
            log.info("MQTT connecting to %s:%d", self.profile.mqtt.host, self.profile.mqtt.port)
        except Exception as e:
            log.error("MQTT init failed: %s", e)

    def stop(self) -> None:
        if not self.client:
            return
        try:
            self.client.publish(self.topic_availability, "offline", qos=1, retain=True)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception as e:
            log.debug("mqtt stop: %s", e)
        self.client = None

    # ─── Callbacks ──────────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            log.info("MQTT connected")
            self.available = True
            client.publish(self.topic_availability, "online", qos=1, retain=True)
            self._publish_discovery()
            client.subscribe(f"{self.topic_base}/set/#")
        else:
            self.available = False
            log.warning("MQTT connect failed: rc=%s", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.available = False
        log.warning("MQTT disconnected (rc=%s)", reason_code)

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode("utf-8", errors="replace").strip()
        if topic == self.topic_set_volume:
            try:
                vol = int(float(payload))
                if self.on_set_volume:
                    self.on_set_volume(max(0, min(100, vol)))
            except ValueError:
                pass
        elif topic == self.topic_set_playback:
            if self.on_set_playback:
                self.on_set_playback(payload)

    # ─── Publishing ─────────────────────────────────────────────────────────

    def publish_status(self, data: dict) -> None:
        if not (self.client and self.available):
            return
        try:
            self.client.publish(self.topic_status, json.dumps(data), qos=0, retain=True)
        except Exception as e:
            log.error("publish: %s", e)

    # Discrete, NON-retained event for the HA logbook/timeline. The Pi runs
    # overlayroot=tmpfs, so its journal is wiped on every reboot — this MQTT
    # event stream is where intermittent-bug evidence (phantom pause, BT
    # flaps) actually persists, because HA keeps the history. ``kind`` must
    # match one of the event_types enumerated in _publish_discovery() or HA's
    # MQTT event entity silently drops it; everything else lands as attributes.
    EVENT_TYPES = [
        "boot", "source_change", "playback_change",
        "bt_connect", "bt_disconnect", "bt_handoff", "error",
    ]

    def publish_event(self, kind: str, message: str = "", **fields) -> None:
        if not (self.client and self.available):
            return
        payload = {"kind": kind, "message": message, **fields}
        try:
            self.client.publish(self.topic_event, json.dumps(payload), qos=1, retain=False)
        except Exception as e:
            log.error("publish_event: %s", e)

    # ─── HA auto-discovery ──────────────────────────────────────────────────

    def _device_block(self) -> dict:
        return {
            "identifiers": [self.profile.identity.speaker_id],
            "name": self.profile.identity.friendly_name,
            "manufacturer": "DIY (Libratone conversion)",
            "model": f"BeatBird ({self.profile.soundcard.driver})",
            "sw_version": "2.0",
        }

    def _publish_discovery(self) -> None:
        did = self.profile.identity.speaker_id
        avail = {
            "availability_topic": self.topic_availability,
            "payload_available": "online",
            "payload_not_available": "offline",
        }
        device = self._device_block()
        prefix = self.profile.mqtt.discovery_prefix

        sensors = [
            {"k": "cpu_temp",    "n": "CPU Temperature", "cls": "temperature",     "u": "°C",  "icon": "mdi:thermometer"},
            {"k": "amp_stereo",  "n": "Amp Stereo",      "icon": "mdi:amplifier"},
            {"k": "amp_sub",     "n": "Amp Sub",         "icon": "mdi:amplifier"},
            {"k": "camilladsp",  "n": "CamillaDSP",      "icon": "mdi:tune-variant"},
            {"k": "spotify",     "n": "Spotify Connect", "icon": "mdi:spotify"},
            {"k": "playback",    "n": "Playback State",  "icon": "mdi:play-circle-outline"},
            {"k": "source",      "n": "Active Source",   "icon": "mdi:speaker"},
            {"k": "song_title",  "n": "Song Title",      "icon": "mdi:music-note"},
            {"k": "song_artist", "n": "Song Artist",     "icon": "mdi:account-music"},
            {"k": "volume",      "n": "Volume",          "u": "%",    "icon": "mdi:volume-high"},
            {"k": "volume_db",   "n": "Volume dB",       "u": "dB",   "icon": "mdi:volume-medium"},
            {"k": "wifi_rssi",   "n": "WiFi Signal",     "cls": "signal_strength", "u": "dBm", "icon": "mdi:wifi"},
        ]
        for s in sensors:
            cfg = {
                "unique_id": f"{did}_{s['k']}",
                "name": s["n"],
                "state_topic": self.topic_status,
                "value_template": f"{{{{ value_json.{s['k']} }}}}",
                "icon": s["icon"],
                **avail,
                "device": device,
            }
            if "cls" in s: cfg["device_class"] = s["cls"]
            if "u" in s:   cfg["unit_of_measurement"] = s["u"]
            if s["k"] in ("cpu_temp", "volume", "volume_db", "wifi_rssi"):
                cfg["state_class"] = "measurement"
            self.client.publish(
                f"{prefix}/sensor/{did}/{cfg['unique_id']}/config",
                json.dumps(cfg), qos=1, retain=True,
            )

        # Volume number
        self.client.publish(
            f"{prefix}/number/{did}/{did}_volume_set/config",
            json.dumps({
                "unique_id": f"{did}_volume_set",
                "name": "Volume Control",
                "command_topic": self.topic_set_volume,
                "state_topic": self.topic_status,
                "value_template": "{{ value_json.volume }}",
                "min": 0, "max": 100, "step": 1,
                "unit_of_measurement": "%",
                "icon": "mdi:volume-high",
                "device": device, **avail,
            }), qos=1, retain=True,
        )

        # Playback select
        self.client.publish(
            f"{prefix}/select/{did}/{did}_playback_set/config",
            json.dumps({
                "unique_id": f"{did}_playback_set",
                "name": "Playback Control",
                "command_topic": self.topic_set_playback,
                "state_topic": self.topic_status,
                "value_template": "{{ value_json.playback }}",
                "options": ["Playing", "Paused", "Stopped"],
                "icon": "mdi:play-pause",
                "device": device, **avail,
            }), qos=1, retain=True,
        )

        # Event entity — a proper timeline in HA for the bug hunt. The Pi's
        # journal is volatile (overlayroot=tmpfs), so this is the only place a
        # phantom pause / BT flap leaves a durable, timestamped trace. The
        # rendered event_type (value_json.kind) must be in event_types; the
        # rest of the payload rides along as attributes via json_attributes.
        self.client.publish(
            f"{prefix}/event/{did}/{did}_events/config",
            json.dumps({
                "unique_id": f"{did}_events",
                "name": "Events",
                "state_topic": self.topic_event,
                "event_types": self.EVENT_TYPES,
                "value_template": "{{ value_json.kind }}",
                "json_attributes_topic": self.topic_event,
                "icon": "mdi:bug-outline",
                "device": device, **avail,
            }), qos=1, retain=True,
        )

        log.info("HA discovery: %d sensors + volume + playback + events published", len(sensors))
