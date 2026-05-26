"""
weather.py — Open-Meteo weather poller for the BeatBird bridge.

Polls https://api.open-meteo.com every 30 minutes for current weather +
today's high/low, formats a single-line WX: serial message for the ESP32
firmware, and pushes it through the existing serial write channel.

Open-Meteo is free, no API key required. WMO weather codes are mapped
to the six icon variants implemented in screen_standby.cpp.

The bridge spawns this in a dedicated thread that owns an asyncio loop
(BeatBird's main bridge loop is sync, threading + asyncio.run keeps the
boundary clean).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Awaitable, Callable, Optional

log = logging.getLogger("beatbird.weather")


# ─── WMO weather-code → icon id ─────────────────────────────────────────────
# Icon ids match the enum in firmware/include/state.h (State::WeatherIcon).
# Keep these in sync if either side changes.

ICON_CLEAR   = 0
ICON_PARTLY  = 1
ICON_CLOUDY  = 2
ICON_FOG     = 3   # rendered as CLOUDY for v1 — no dedicated fog icon yet
ICON_RAIN    = 4
ICON_SNOW    = 5
ICON_THUNDER = 6


def wmo_to_icon(code: int) -> int:
    """Map an Open-Meteo WMO weather code to a firmware icon id.

    Reference: https://open-meteo.com/en/docs (search "weather_code")
    """
    if code == 0:                  return ICON_CLEAR
    if code in (1, 2):             return ICON_PARTLY
    if code == 3:                  return ICON_CLOUDY
    if code in (45, 48):           return ICON_FOG
    if 51 <= code <= 57:           return ICON_RAIN     # drizzle
    if 61 <= code <= 67:           return ICON_RAIN
    if 71 <= code <= 77:           return ICON_SNOW
    if 80 <= code <= 82:           return ICON_RAIN     # rain showers
    if code in (85, 86):           return ICON_SNOW     # snow showers
    if 95 <= code <= 99:           return ICON_THUNDER
    log.warning("unknown WMO code %d — falling back to PARTLY", code)
    return ICON_PARTLY


# ─── Poller ─────────────────────────────────────────────────────────────────


class WeatherPoller:
    """Periodic Open-Meteo poller that pushes WX: lines through a callback.

    Args:
        lat / lon: location coords in decimal degrees.
        serial_writer: callable (line: str) -> None; the trailing newline
            is added here, do NOT include it in your writer.
        interval_s: poll interval in seconds (default 1800 = 30 min).
        http_timeout_s: per-request timeout (default 10 s).
    """

    ENDPOINT = "https://api.open-meteo.com/v1/forecast"

    def __init__(
        self,
        lat: float,
        lon: float,
        serial_writer: Callable[[str], None],
        interval_s: int = 30 * 60,
        http_timeout_s: float = 20.0,
    ) -> None:
        self.lat = lat
        self.lon = lon
        self._write = serial_writer
        self.interval_s = interval_s
        self.http_timeout_s = http_timeout_s
        self._last_line: Optional[str] = None

    # Fast-retry backoff while we still have no data. If the provider
    # is down at bridge startup, the long interval_s (30 min default)
    # leaves the firmware showing a blank weather block for the whole
    # window even after the API recovers. Drop to short polls while
    # _last_line is None so we catch the recovery within a minute or
    # two, then revert to the configured interval once we've got data.
    COLD_RETRY_S = 60.0

    async def run(self) -> None:
        """Run forever. Single failure does not crash the loop — just logs
        and waits for the next tick. Uses a short retry interval while we
        haven't yet received a successful response, then settles into the
        configured interval_s for steady-state polling."""
        log.info("starting weather poller at (%.4f, %.4f), interval %ds",
                 self.lat, self.lon, self.interval_s)
        while True:
            try:
                line = await self._poll_once()
                if line and line != self._last_line:
                    self._write(line + "\n")
                    self._last_line = line
            except Exception:
                log.exception("weather poll failed; will retry next tick")
            await asyncio.sleep(
                self.interval_s if self._last_line else self.COLD_RETRY_S,
            )

    async def _poll_once(self) -> Optional[str]:
        # Use httpx if available (async), otherwise fall back to requests in a thread.
        try:
            import httpx
        except ImportError:
            return await self._poll_once_requests()

        params = {
            "latitude":  self.lat,
            "longitude": self.lon,
            "current":   "temperature_2m,weather_code",
            "daily":     "temperature_2m_max,temperature_2m_min",
            "timezone":  "auto",
        }
        async with httpx.AsyncClient(timeout=self.http_timeout_s) as c:
            r = await c.get(self.ENDPOINT, params=params)
            r.raise_for_status()
            data = r.json()
        return self._build_line(data)

    async def _poll_once_requests(self) -> Optional[str]:
        """Fallback using `requests` (already a bridge dep) on an executor."""
        import requests
        params = {
            "latitude":  self.lat,
            "longitude": self.lon,
            "current":   "temperature_2m,weather_code",
            "daily":     "temperature_2m_max,temperature_2m_min",
            "timezone":  "auto",
        }
        loop = asyncio.get_running_loop()
        def _fetch():
            r = requests.get(self.ENDPOINT, params=params, timeout=self.http_timeout_s)
            r.raise_for_status()
            return r.json()
        data = await loop.run_in_executor(None, _fetch)
        return self._build_line(data)

    def _build_line(self, data: dict) -> Optional[str]:
        try:
            cur   = data["current"]
            daily = data["daily"]
            temp = round(cur["temperature_2m"])
            code = int(cur["weather_code"])
            hi   = round(daily["temperature_2m_max"][0])
            lo   = round(daily["temperature_2m_min"][0])
        except (KeyError, IndexError, TypeError, ValueError) as e:
            log.error("malformed Open-Meteo response: %s; payload=%r", e, data)
            return None

        icon = wmo_to_icon(code)
        line = f"WX:t={temp}|c={icon}|h={hi}|l={lo}"
        log.info("weather: %s (raw_code=%d)", line, code)
        return line


# ─── Thread launcher (for sync bridge) ──────────────────────────────────────


def start_in_thread(
    lat: float,
    lon: float,
    serial_writer: Callable[[str], None],
    interval_s: int = 30 * 60,
) -> threading.Thread:
    """Spawn the poller in its own daemon thread with its own asyncio loop.

    Returns the thread handle (usually ignored — the thread is daemonic).
    """
    poller = WeatherPoller(
        lat=lat, lon=lon,
        serial_writer=serial_writer,
        interval_s=interval_s,
    )

    def _runner():
        try:
            asyncio.run(poller.run())
        except Exception:
            log.exception("weather thread crashed")

    t = threading.Thread(target=_runner, daemon=True, name="weather")
    t.start()
    return t


# ─── Standalone test ────────────────────────────────────────────────────────


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("usage: python -m beatbird.weather <latitude> <longitude>")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)

    def _print_writer(line: str) -> None:
        print(line, end="")

    async def main() -> None:
        wp = WeatherPoller(
            lat=float(sys.argv[1]),
            lon=float(sys.argv[2]),
            serial_writer=_print_writer,
        )
        line = await wp._poll_once()
        if line:
            _print_writer(line + "\n")
        else:
            print("(no line produced)")

    asyncio.run(main())
