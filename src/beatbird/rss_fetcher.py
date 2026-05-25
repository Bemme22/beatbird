"""
rss_fetcher.py — Background thread pulling headlines from an RSS/Atom feed
into a length-capped, ASCII-sanitised pool for the standby flap text.

Stdlib only (urllib + xml.etree) so no extra Pi dependency. Sanitiser
matches the one in DisplayAMOLED.push_idle_message: Latin-1 accented
letters → digraphs (ä→AE …), then strip anything else. Items longer
than `max_chars` after sanitising are dropped — easier than mid-word
truncation; news feeds publish 50+ headlines so the survivors are
enough to keep the standby panel varied.
"""
from __future__ import annotations

import html
import logging
import re
import threading
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)


# Same digraph table the display sanitiser uses — kept in sync so a
# headline that's accepted here always survives the push to firmware.
_DIGRAPHS = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "AE", "Ö": "OE", "Ü": "UE",
    "é": "e",  "è": "e",  "ê": "e",  "É": "E",
    "á": "a",  "à": "a",  "â": "a",
    "í": "i",  "ì": "i",  "î": "i",
    "ó": "o",  "ò": "o",  "ô": "o",
    "ú": "u",  "ù": "u",  "û": "u",
    "ñ": "n",  "ç": "c",
}
_DIGRAPH_TR = str.maketrans(_DIGRAPHS)


def _sanitise(s: str) -> str:
    """Translate accented chars to digraphs, decode HTML entities,
    collapse whitespace, uppercase. Returns "" for anything that ends
    up empty after stripping non-ASCII."""
    s = html.unescape(s)
    s = s.translate(_DIGRAPH_TR)
    s = "".join(c for c in s if 32 <= ord(c) < 127)
    s = re.sub(r"\s+", " ", s).strip().upper()
    return s


class RssFetcher:
    """Polls one RSS/Atom URL in a daemon thread. Bridge reads the
    `headlines` list whenever it picks the next standby flap line.

    Thread-safety: `headlines` is replaced atomically (single attribute
    rebind) — readers see either the old or new list, never a partial
    update. No lock needed.
    """

    def __init__(self, url: str, refresh_minutes: int = 30, max_chars: int = 17) -> None:
        self.url = url
        self.refresh_s = max(60, refresh_minutes * 60)
        self.max_chars = max_chars
        self.headlines: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ─── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread or not self.url:
            return
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="rss-fetcher",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ─── Worker loop ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        # One immediate fetch so the standby panel doesn't sit on the
        # local-only pool for the first 30 min after boot.
        while not self._stop.is_set():
            try:
                self._refresh()
            except Exception as e:
                log.warning("rss fetch %s failed: %s", self.url, e)
            self._stop.wait(self.refresh_s)

    def _refresh(self) -> None:
        req = urllib.request.Request(
            self.url, headers={"User-Agent": "BeatBird/1.0 (+rss-fetcher)"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            log.warning("rss xml parse error: %s", e)
            return

        # RSS 2.0: channel/item/title    Atom: entry/title
        # findall covers both via the same xpath when the namespace is
        # the default — fallback to local-name lookup for namespaced Atom.
        titles: list[str] = []
        for t in root.iter():
            tag = t.tag.split("}", 1)[-1]   # strip namespace if any
            if tag == "title" and t.text:
                titles.append(t.text)

        # Tagesschau-style feeds have 30-80 char headlines. Dropping
        # anything > max_chars would leave us with almost nothing. Truncate
        # with a trailing "..." instead (no Unicode ellipsis — split-flap
        # animates byte-by-byte and can't handle multi-byte glyphs).
        keep: list[str] = []
        seen: set[str] = set()
        for raw_title in titles:
            s = _sanitise(raw_title)
            if not s:
                continue
            if len(s) > self.max_chars:
                s = s[: self.max_chars - 3].rstrip() + "..."
            if s in seen:
                continue
            seen.add(s)
            keep.append(s)

        self.headlines = keep
        log.info("rss: %d headlines kept (≤%d chars) from %d items in %s",
                 len(keep), self.max_chars, len(titles), self.url)
