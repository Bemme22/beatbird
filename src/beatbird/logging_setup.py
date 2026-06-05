"""Logging configuration for the BeatBird bridge.

Kept in its own stdlib-only module so it can be unit-tested without importing
the bridge (which pulls in paho-mqtt, the DSP websocket client, etc.).

The default sink is stdout, which systemd's journal captures and rotates.
Set ``BEATBIRD_LOG_FILE`` to ALSO write a size-rotated text log — handy on a
headless speaker where you want a file to ``scp`` off without journald, and
one that survives ``journalctl --vacuum``. It degrades gracefully: an
unwritable path falls back to stdout-only instead of crashing the bridge at
boot.

Env knobs:
  BEATBIRD_LOGLEVEL        log level (default INFO)
  BEATBIRD_LOG_FILE        path to also log to; unset = stdout only
  BEATBIRD_LOG_MAX_BYTES   per-file size cap before rotation (default 1 MiB)
  BEATBIRD_LOG_BACKUP_COUNT rotated files to keep (default 3)
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional

# journald already stamps the full date, so stdout lines stay time-only…
_STREAM_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_STREAM_DATEFMT = "%H:%M:%S"
# …but a file outlives a single day, so each line carries the date.
_FILE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_FILE_DATEFMT = "%Y-%m-%d %H:%M:%S"

DEFAULT_MAX_BYTES = 1 * 1024 * 1024   # 1 MiB per file
DEFAULT_BACKUP_COUNT = 3              # bridge.log + .1 .2 .3 → ~4 MiB cap


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def make_rotating_handler(
    path: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> Optional[RotatingFileHandler]:
    """Build a size-rotated file handler, or None if ``path`` isn't writable.

    Opens the file eagerly so a permission/dir error surfaces here and the
    caller can fall back to stdout — rather than blowing up on the first log
    line deep in the run loop.
    """
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8",
        )
    except OSError:
        return None
    handler.setFormatter(logging.Formatter(_FILE_FORMAT, _FILE_DATEFMT))
    return handler


def configure_logging() -> None:
    """Configure root logging for the bridge process.

    Always logs to stdout. If ``BEATBIRD_LOG_FILE`` is set, ALSO logs to a
    self-rotating file. Level comes from ``BEATBIRD_LOGLEVEL`` (default INFO).
    """
    level = os.environ.get("BEATBIRD_LOGLEVEL", "INFO")

    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter(_STREAM_FORMAT, _STREAM_DATEFMT))
    handlers: list[logging.Handler] = [stream]

    log_file = os.environ.get("BEATBIRD_LOG_FILE")
    file_failed = False
    if log_file:
        fh = make_rotating_handler(
            log_file,
            _env_int("BEATBIRD_LOG_MAX_BYTES", DEFAULT_MAX_BYTES),
            _env_int("BEATBIRD_LOG_BACKUP_COUNT", DEFAULT_BACKUP_COUNT),
        )
        if fh is not None:
            handlers.append(fh)
        else:
            file_failed = True

    # force=True so a re-invocation (tests, re-init) replaces handlers
    # instead of stacking duplicates.
    logging.basicConfig(level=level, handlers=handlers, force=True)

    if file_failed:
        logging.getLogger("beatbird.bridge").warning(
            "BEATBIRD_LOG_FILE=%s not writable — file logging disabled, stdout only",
            log_file,
        )
