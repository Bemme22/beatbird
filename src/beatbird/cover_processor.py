"""
cover_processor.py — fetch + blur + darken + vignette + JPEG-recompress
album cover images for the display background.

Pi takes the raw 300-640 px JPEG that go-librespot exposes via
`track.album_cover_url` and turns it into a ~25-40 KB blurred-and-darkened
background that the firmware can decode + paint full-screen behind the
player UI. Doing the heavy lifting on the Pi means the ESP32 doesn't
need a blur kernel (would freeze its LVGL render thread for hundreds of
ms per track change) and a smaller payload over the USB-CDC serial link.

Pipeline per cover: ~100-200 ms on a Pi Zero 2W. Triggered from the bridge
on track-URI change. Cached by URI so a repeat / queue-loop doesn't
re-process.

Output: JPEG bytes, NOT decoded RGB. The firmware uses LVGL's JPEG
decoder (LV_USE_TJPGD) so we pay the decode cost there once per cover.
"""
from __future__ import annotations

import io
import logging
from collections import OrderedDict
from typing import Optional

log = logging.getLogger(__name__)


class CoverProcessor:
    """Stateful processor with an LRU cache keyed by track URI.

    Parameters tuned for the AMOLED 466x466 round display:
      - output_size = 466 (matches LV_SDL_HOR_RES / panel resolution)
      - blur_radius ~ 12 px (heavy enough to read as background; not so
        heavy that brand colours wash out)
      - darken = 0.45 (cream UI text stays legible)
      - vignette_strength = 0.4 (edges fade ~40 % darker than centre,
        further pulling focus to the player chrome)
      - jpeg_quality = 75 (typical 25-40 KB output for blurred content)
    """

    def __init__(
        self,
        output_size: int = 466,
        blur_radius: float = 12.0,
        # Tuned on the sim with the actual UI in front of a real Spotify
        # cover (Modestep). Darken 0.35 keeps brand colours visible but
        # cream-yellow UI stays high-contrast; vignette 1.0 fades the
        # edges to almost-black which pulls focus to the centre track
        # text. Lower vignette = more cover visible at the corners.
        darken: float = 0.35,
        vignette_strength: float = 1.0,
        jpeg_quality: int = 75,
        cache_max: int = 20,
    ) -> None:
        self.output_size = output_size
        self.blur_radius = blur_radius
        self.darken = darken
        self.vignette_strength = vignette_strength
        self.jpeg_quality = jpeg_quality
        self._cache: "OrderedDict[str, bytes]" = OrderedDict()
        self._cache_max = cache_max
        self._pil_ok: Optional[bool] = None
        self._requests = None

    # ─── Lazy imports — Pillow + requests aren't on the bridge venv until
    # 70-bridge.sh adds them. Probe at first call so a missing dep falls
    # back to "no cover" instead of crashing the bridge at import time.

    def _check_imports(self) -> bool:
        if self._pil_ok is None:
            try:
                from PIL import Image, ImageDraw, ImageFilter  # noqa: F401
                self._pil_ok = True
            except ImportError:
                log.error("Pillow not installed — cover processor disabled")
                self._pil_ok = False
        if self._requests is None:
            try:
                import requests
                self._requests = requests
            except ImportError:
                log.error("requests not installed — cover processor disabled")
                self._pil_ok = False
        return bool(self._pil_ok)

    # ─── Public API ─────────────────────────────────────────────────────────

    def get(self, uri: str, url: str) -> Optional[bytes]:
        """Return JPEG bytes for `uri`. Downloads from `url` and processes
        on cache miss. None on any failure — the bridge falls back to a
        plain black background in that case."""
        if not uri or not url:
            return None
        cached = self._cache.get(uri)
        if cached is not None:
            self._cache.move_to_end(uri)
            return cached
        if not self._check_imports():
            return None
        try:
            raw = self._download(url)
            if not raw:
                return None
            processed = self._process(raw)
            self._store(uri, processed)
            log.info("cover processed: %s → %d KB", uri, len(processed) // 1024)
            return processed
        except Exception as e:
            log.warning("cover_processor failed for %s: %s", uri, e)
            return None

    def cache_size(self) -> int:
        return len(self._cache)

    # ─── Internals ──────────────────────────────────────────────────────────

    def _download(self, url: str) -> Optional[bytes]:
        try:
            r = self._requests.get(url, timeout=5)
            r.raise_for_status()
            return r.content
        except Exception as e:
            log.warning("cover download %s failed: %s", url, e)
            return None

    def _process(self, raw: bytes) -> bytes:
        from PIL import Image, ImageDraw, ImageFilter

        img = Image.open(io.BytesIO(raw)).convert("RGB")
        # Album covers are square; lanczos resize covers both up- and
        # down-sampling cleanly. Source can be 300px up to 640px.
        img = img.resize((self.output_size, self.output_size), Image.LANCZOS)
        img = img.filter(ImageFilter.GaussianBlur(self.blur_radius))
        # Linear darken — single point() over all channels.
        d = self.darken
        img = img.point(lambda v: int(v * d))
        if self.vignette_strength > 0:
            img = self._apply_vignette(img)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.jpeg_quality, optimize=True)
        return buf.getvalue()

    def _apply_vignette(self, img):
        """Radial darken: centre keeps full image, edges fade toward
        `vignette_strength` darker. Implemented as a soft-edged white
        ellipse blurred and used as a composite mask between img and a
        darker copy."""
        from PIL import Image, ImageDraw, ImageFilter

        w, h = img.size
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, w, h], fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(min(w, h) * 0.30))
        edge = img.point(lambda v: int(v * (1.0 - self.vignette_strength)))
        return Image.composite(img, edge, mask)

    def _store(self, uri: str, data: bytes) -> None:
        self._cache[uri] = data
        while len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)
