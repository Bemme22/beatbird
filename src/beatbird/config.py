"""
beatbird.config — profile loading and validation.

The single source of truth for how a BeatBird speaker is configured. Reads
the YAML profile pointed to by $BEATBIRD_PROFILE (set via the systemd
EnvironmentFile at /etc/beatbird/env) and validates its structure.

All other modules receive a validated ``Profile`` instance — no direct YAML
access elsewhere.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator


# ─── Sub-models ──────────────────────────────────────────────────────────────

class Identity(BaseModel):
    hostname: str = "beatbird"
    friendly_name: str = "BeatBird Speaker"
    speaker_id: str = "beatbird_generic"


class Soundcard(BaseModel):
    driver: Literal[
        "louder-hat-plus-2x",
        "louder-hat-plus-1x",
        "louder-hat-triple",
        "innomaker-amp-pro",
    ]
    primary_i2c: Optional[int] = None
    secondary_i2c: Optional[int] = None
    tertiary_i2c: Optional[int] = None
    sub_enabled: bool = False
    sub_crossover_hz: int = 150
    sub_digital_volume: int = 110
    analog_gain_db: float = -3.0

    @field_validator("primary_i2c", "secondary_i2c", "tertiary_i2c", mode="before")
    @classmethod
    def _hex_or_int(cls, v):
        if v is None or isinstance(v, int):
            return v
        if isinstance(v, str):
            return int(v, 0)  # honours 0x prefix
        raise TypeError("i2c addr must be int or hex string")


class LoudnessFilter(BaseModel):
    name: str
    max_boost_db: float


class Loudness(BaseModel):
    enabled: bool = True
    filters: list[LoudnessFilter] = Field(default_factory=list)


class VolumeConfig(BaseModel):
    min_db: float = -60.0
    max_db: float = -10.0


class Audio(BaseModel):
    camilladsp_config: str = "_stub"
    sample_rate: int = 48000
    format: str = "S32LE"
    volume: VolumeConfig = Field(default_factory=VolumeConfig)
    loudness: Loudness = Field(default_factory=Loudness)


class Display(BaseModel):
    type: Literal["amoled", "led-button", "none"] = "none"
    variant: Optional[str] = None
    serial_device: str = "auto"
    spectrum_bands: int = 16
    led_pin: Optional[int] = None
    led_count: Optional[int] = None
    button_pin: Optional[int] = None
    led_brightness: int = 128


class WiFi(BaseModel):
    ssid: str = ""
    country: str = "DE"
    use_usb_dongle: bool = False
    disable_onboard_radio: bool = False
    disable_bluetooth: bool = False


class MQTT(BaseModel):
    enabled: bool = True
    host: str = "localhost"
    port: int = 1883
    user: str = ""
    discovery_prefix: str = "homeassistant"
    base_topic: str = "beatbird"


class SpotifySource(BaseModel):
    enabled: bool = True
    device_name: str = "BeatBird"
    bitrate: int = 320
    normalisation: bool = True


class BluetoothSource(BaseModel):
    enabled: bool = False
    a2dp: bool = True


class ToslinkSource(BaseModel):
    enabled: bool = False
    device: str = "hw:1,0"


class SnapcastSource(BaseModel):
    enabled: bool = False
    server: str = ""
    latency_ms: int = 30


class Sources(BaseModel):
    spotify: SpotifySource = Field(default_factory=SpotifySource)
    bluetooth: BluetoothSource = Field(default_factory=BluetoothSource)
    toslink: ToslinkSource = Field(default_factory=ToslinkSource)
    snapcast: SnapcastSource = Field(default_factory=SnapcastSource)


class Web(BaseModel):
    enabled: bool = True
    port: int = 8080


# ─── Top-level ───────────────────────────────────────────────────────────────

class Profile(BaseModel):
    identity: Identity = Field(default_factory=Identity)
    platform: Literal["pi-zero-2w", "pi-3b-plus", "pi-4", "pi-5"] = "pi-zero-2w"
    soundcard: Soundcard
    audio: Audio = Field(default_factory=Audio)
    display: Display = Field(default_factory=Display)
    wifi: WiFi = Field(default_factory=WiFi)
    mqtt: MQTT = Field(default_factory=MQTT)
    sources: Sources = Field(default_factory=Sources)
    web: Web = Field(default_factory=Web)

    # ── Convenience properties derived from the profile ──

    @property
    def mqtt_topic_base(self) -> str:
        """Full MQTT topic root for this speaker."""
        base = self.mqtt.base_topic.rstrip("/")
        # If the base already contains the speaker_id, don't duplicate.
        if self.identity.speaker_id in base:
            return base
        return f"{base}/{self.identity.speaker_id}" if "/" in base else base

    @property
    def has_tas5825m(self) -> bool:
        return self.soundcard.driver.startswith("louder-hat")


# ─── Loader ──────────────────────────────────────────────────────────────────

_DEFAULT_PROFILE_PATH = "/etc/beatbird/current-profile.yml"


def load_profile(path: Optional[str | Path] = None) -> Profile:
    """Load and validate a profile from YAML.

    Resolution order:
      1. explicit ``path`` argument
      2. ``$BEATBIRD_PROFILE``
      3. ``./profiles/current.yml`` relative to CWD
      4. ``/etc/beatbird/current-profile.yml``
    """
    if path is None:
        path = (
            os.environ.get("BEATBIRD_PROFILE")
            or ("profiles/current.yml" if Path("profiles/current.yml").exists() else None)
            or _DEFAULT_PROFILE_PATH
        )
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Profile not found: {path}")
    with path.open() as f:
        raw = yaml.safe_load(f)
    return Profile.model_validate(raw)
