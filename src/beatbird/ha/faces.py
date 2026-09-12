"""
faces.py — standby "faces": one glanceable decision per screen.

A *face* is one thing worth looking up for, rendered in the standby clock's
geometry (small label above, one big value, small detail below). HA publishes
them as retained JSON on ``<topic>/<id>``; this module caches them, expires
them and hands the bridge a ready-to-send serial line.

Why the bridge stores *decisions* and not sensor values: every case that is
actually wanted is a derived condition — a rate of change, a comparison of two
sensors, a state transition, a deviation from expected. HA owns the history,
the templates and the statistics; a line-based ASCII protocol has no business
computing dew points. One HA-side rule then serves the whole fleet.
(See ``HA.md``, *Display concept*.)

Payload (all keys optional except ``id``)::

    {"id":"luften","prio":40,"big":"3.4","unit":"K",
     "top":"LÜFTEN","bot":"Taupunkt außen 9.8 · innen 13.2","ttl":3600}

An **empty payload clears the face** — that is the MQTT idiom for deleting a
retained message, so a publisher that clears its retained topic and a publisher
that says "nothing to show" end up on the same path.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Iterable

log = logging.getLogger("beatbird.faces")


# Field budgets. The display is 466 px wide and round, so these are generous
# rather than tight; they exist to stop one bad payload from pushing a
# multi-kilobyte line into a firmware read buffer.
MAX_ID = 16
MAX_BIG = 10
MAX_UNIT = 4
MAX_TOP = 20
MAX_BOT = 48

# Cap on how many faces we keep. Well above the "a short rotation of quiet
# faces" the concept calls for; a speaker showing 12 things is already wrong.
MAX_FACES = 8

# Upper bound on a single face's lifetime, so a publisher that sends a silly
# ttl (or none at all, meaning "forever") cannot pin a face on screen for good.
DEFAULT_TTL_S = 3600
MAX_TTL_S = 24 * 3600


# Umlauts and friends → ASCII digraphs. The firmware's split-flap animates
# byte-by-byte and would tear a multi-byte UTF-8 sequence mid-cycle, so text
# destined for the panel is folded to ASCII on the Pi — same reason and same
# mapping as the idle line in display/amoled.py. Digraphs are how you write
# German on a station board anyway.
_ASCII = str.maketrans({
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "AE", "Ö": "OE", "Ü": "UE",
    "é": "e",  "è": "e",  "ê": "e",  "É": "E",
    "á": "a",  "à": "a",  "â": "a",
    "í": "i",  "ì": "i",  "î": "i",
    "ó": "o",  "ò": "o",  "ô": "o",
    "ú": "u",  "ù": "u",  "û": "u",
    "ñ": "n",  "ç": "c",
    "·": "-",  "–": "-",  "—": "-",
    "°": "deg",
})


def _clean(value: object, limit: int) -> str:
    """Fold to ASCII, strip the protocol's own delimiters, truncate.

    ``|`` and newlines are what separates fields and lines on the wire, so a
    payload carrying them would desynchronise the parser rather than merely
    look wrong. Dropping them here keeps the firmware parser trivial — it never
    has to know about escaping.
    """
    text = str(value if value is not None else "")
    text = text.translate(_ASCII)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.replace("|", " ").replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())[:limit]


@dataclass
class Face:
    """One standby face. ``deadline`` is a monotonic timestamp."""

    id: str
    prio: int
    big: str
    unit: str
    top: str
    bot: str
    deadline: float

    def line(self) -> str:
        """Render the serial form. Empty fields are omitted, not sent blank —
        the firmware treats a missing token as 'unchanged/none' anyway, and it
        keeps the common case short."""
        parts = [f"id={self.id}", f"prio={self.prio}", f"big={self.big}"]
        if self.unit:
            parts.append(f"unit={self.unit}")
        if self.top:
            parts.append(f"top={self.top}")
        if self.bot:
            parts.append(f"bot={self.bot}")
        return "FACE:" + "|".join(parts)


class FaceStore:
    """Cache of the currently valid faces, newest payload per id wins."""

    def __init__(self, max_faces: int = MAX_FACES, dwell_s: int = 8):
        self._faces: dict[str, Face] = {}
        self._max_faces = max_faces
        # Seconds per face in the standby rotation. Rides on the batch
        # terminator rather than a config line of its own: it is only ever
        # needed together with a set of faces, and sending it atomically with
        # them removes any window where the firmware has faces but no cadence.
        self._dwell_s = dwell_s

    # ─── Ingest ─────────────────────────────────────────────────────────────

    def update(self, face_id: str, payload: str, now: float | None = None) -> bool:
        """Apply one MQTT message. Returns True if the visible set changed.

        A blank payload (retained-message delete) or ``ttl <= 0`` removes the
        face. Malformed JSON is dropped with a warning rather than raising:
        this runs on the MQTT callback thread, and one bad publisher must not
        take the bridge's message loop down.
        """
        now = time.monotonic() if now is None else now
        face_id = _clean(face_id, MAX_ID)
        if not face_id:
            return False

        if not payload or not payload.strip():
            return self._drop(face_id)

        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            log.warning("face %s: payload is not JSON, ignored", face_id)
            return False
        if not isinstance(data, dict):
            log.warning("face %s: payload is not an object, ignored", face_id)
            return False

        try:
            ttl = int(float(data.get("ttl", DEFAULT_TTL_S)))
        except (ValueError, TypeError):
            ttl = DEFAULT_TTL_S
        if ttl <= 0:
            return self._drop(face_id)
        ttl = min(ttl, MAX_TTL_S)

        try:
            prio = int(float(data.get("prio", 0)))
        except (ValueError, TypeError):
            prio = 0

        face = Face(
            id=face_id,
            prio=max(0, min(100, prio)),
            big=_clean(data.get("big", ""), MAX_BIG),
            unit=_clean(data.get("unit", ""), MAX_UNIT),
            top=_clean(data.get("top", ""), MAX_TOP),
            bot=_clean(data.get("bot", ""), MAX_BOT),
            deadline=now + ttl,
        )
        if not face.big:
            # A face with nothing far-readable on it is the one thing the whole
            # concept rules out, so it is a publisher bug, not a display state.
            log.warning("face %s: no 'big' value, ignored", face_id)
            return False

        previous = self._faces.get(face_id)
        self._faces[face_id] = face
        self._evict(now)
        return previous is None or _visible(previous) != _visible(face)

    def _drop(self, face_id: str) -> bool:
        return self._faces.pop(face_id, None) is not None

    def _evict(self, now: float) -> None:
        """Keep the store bounded: expire first, then drop the lowest
        priorities. Expiry before priority so a stale high-prio face can never
        squeeze out a fresh low-prio one."""
        for fid in [f for f, x in self._faces.items() if x.deadline <= now]:
            del self._faces[fid]
        if len(self._faces) <= self._max_faces:
            return
        for face in sorted(self._faces.values(), key=_order)[self._max_faces:]:
            log.info("face %s dropped: store full", face.id)
            del self._faces[face.id]

    # ─── Read ───────────────────────────────────────────────────────────────

    def active(self, now: float | None = None) -> list[Face]:
        """Valid faces, highest priority first; ties broken by id so the
        rotation order is stable between calls (a set that reshuffles itself
        every tick reads as a glitch)."""
        now = time.monotonic() if now is None else now
        self._evict(now)
        return sorted(self._faces.values(), key=_order)

    def lines(self, now: float | None = None) -> list[str]:
        """Serial lines for the current set, in display order, followed by a
        terminator so the firmware knows the set is complete and can drop
        anything it still holds."""
        return [f.line() for f in self.active(now)] + [f"FACE:end|dwell={self._dwell_s}"]

    def __len__(self) -> int:
        return len(self._faces)


def _order(face: Face) -> tuple[int, str]:
    return (-face.prio, face.id)


def _visible(face: Face) -> tuple:
    """The part a viewer would notice — deliberately excludes ``deadline`` so a
    re-publish of an unchanged face (HA re-sends retained state on every
    reconnect) does not churn the display."""
    return (face.prio, face.big, face.unit, face.top, face.bot)


def face_id_from_topic(topic: str, prefix: str) -> str | None:
    """``beatbird/hints/luften`` + ``beatbird/hints`` → ``luften``.

    Only direct children count: a deeper topic is someone else's namespace, not
    a face with a slash in its name.
    """
    prefix = prefix.rstrip("/")
    if not topic.startswith(prefix + "/"):
        return None
    rest = topic[len(prefix) + 1:]
    return rest if rest and "/" not in rest else None


def render(faces: Iterable[Face]) -> list[str]:
    """Convenience for callers that already hold a face list."""
    return [f.line() for f in faces] + ["FACE:end"]
