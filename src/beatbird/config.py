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
import re
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
    # Curve selecting how the per-filter boost decays as volume rises.
    # "legacy" = ((80-vol)/75)^1.5, fades to 0 by vol=80, max at vol≤5.
    # "smoothstep" = cubic plateau 0..10%, smooth fall to 0 at vol=75.
    #   Closer to "bass reaches max quickly, then only mids/highs grow" UX.
    # Profiles must opt in to "smoothstep" — keeps existing speaker tunings
    # untouched until they're individually validated against the new curve.
    curve: Literal["legacy", "smoothstep"] = "legacy"


class VolumeConfig(BaseModel):
    min_db: float = -60.0
    max_db: float = -10.0
    # Volume taper. 1.0 = linear dB mapping (legacy — feels broken because dB
    # is already log: bottom 30% of slider is mostly inaudible). 2.0 = Sonos-
    # style audio taper, lower half of slider is finely resolved.
    # Profiles must opt in explicitly so adopting the new curve is a per-
    # speaker decision, not a silent breaking change for existing setups.
    # See beatbird.audio.camilladsp.pct_to_db for the formula.
    curve_gamma: float = 1.0


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

    # ── Single accent colour (current PAL: protocol) ──
    # Bridge sends `PAL:rrggbb` once per ESP32 (re)connect; firmware derives
    # accent_dim from it. Default: champagne gold — sits well on the Zipp
    # Mini 2 turquoise/cream enclosure.
    # Format: 6-char hex string, with or without leading "#".
    accent_color: str = "F0CB7B"

    # ── Extended palette (stored, not yet transmitted) ──
    # Multi-colour theme for future protocol/firmware support — currently
    # only `accent_color` is sent. When the palette feature ships, these
    # five drive secondary highlights, body/label text, and alert states.
    # All optional, hex format (with or without "#").
    accent_glow:    Optional[str] = None   # bright variant for emphasis
    accent_dim:     Optional[str] = None   # explicit dim shade (else derived)
    text_primary:   Optional[str] = None   # body text (else firmware default)
    text_secondary: Optional[str] = None   # labels, source line
    accent_alert:   Optional[str] = None   # error/warning highlights

    # LED+button display (Lounge / LT300)
    led_pin: Optional[int] = None
    led_count: Optional[int] = None
    button_pin: Optional[int] = None
    led_brightness: int = 128

    @field_validator("accent_color", mode="before")
    @classmethod
    def _normalise_accent_color(cls, v):
        if v is None or v == "":
            return "F0CB7B"
        if not isinstance(v, str):
            raise TypeError("accent_color must be a hex string")
        s = v.strip().lstrip("#").upper()
        if not re.fullmatch(r"[0-9A-F]{6}", s):
            raise ValueError(f"accent_color must be 6 hex chars (got {v!r})")
        return s

    @field_validator(
        "accent_glow", "accent_dim",
        "text_primary", "text_secondary", "accent_alert",
        mode="before",
    )
    @classmethod
    def _normalise_optional_palette(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        if not isinstance(v, str):
            raise TypeError("palette colour must be a hex string")
        s = v.strip().lstrip("#").upper()
        if not re.fullmatch(r"[0-9A-F]{6}", s):
            raise ValueError(f"palette colour must be 6 hex chars (got {v!r})")
        return s


class PowerButton(BaseModel):
    enabled: bool = False
    gpio: int = 3              # GPIO3 = canonical Pi power button (Wake-on-low works from halt state)
    long_press_s: float = 2.0  # hold this long to actually shut down


class Hardware(BaseModel):
    power_button: PowerButton = Field(default_factory=PowerButton)


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
    hardware: Hardware = Field(default_factory=Hardware)
    wifi: WiFi = Field(default_factory=WiFi)
    mqtt: MQTT = Field(default_factory=MQTT)
    sources: Sources = Field(default_factory=Sources)
    web: Web = Field(default_factory=Web)

    @property
    def mqtt_topic_base(self) -> str:
        base = self.mqtt.base_topic.rstrip("/")
        if self.identity.speaker_id in base:
            return base
        return f"{base}/{self.identity.speaker_id}" if "/" in base else base

    @property
    def has_tas5825m(self) -> bool:
        return self.soundcard.driver.startswith("louder-hat")


# ─── Loader ──────────────────────────────────────────────────────────────────

_DEFAULT_PROFILE_PATH = "/etc/beatbird/current-profile.yml"


def load_profile(path: Optional[str | Path] = None) -> Profile:
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
