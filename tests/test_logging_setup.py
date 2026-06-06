"""Tests for beatbird.logging_setup — file rotation + graceful fallback.

Stdlib-only module, so these run in CI without the bridge's heavy deps. Each
test that calls configure_logging() restores the root logger afterwards
(configure_logging uses basicConfig(force=True), which mutates global state).
"""

import logging
from logging.handlers import RotatingFileHandler

import pytest

from beatbird import logging_setup as ls


@pytest.fixture(autouse=True)
def _restore_root_logging():
    root = logging.getLogger()
    saved = root.handlers[:]
    saved_level = root.level
    yield
    root.handlers[:] = saved
    root.setLevel(saved_level)


def test_make_handler_creates_parent_dir(tmp_path):
    path = tmp_path / "logs" / "bridge.log"
    h = ls.make_rotating_handler(str(path), max_bytes=4096, backup_count=2)
    assert isinstance(h, RotatingFileHandler)
    assert h.maxBytes == 4096
    assert h.backupCount == 2
    assert (tmp_path / "logs").is_dir()   # parent auto-created
    h.close()


def test_make_handler_returns_none_on_unwritable_path(tmp_path):
    # Parent is a regular file, not a directory → makedirs raises OSError.
    blocker = tmp_path / "iam_a_file"
    blocker.write_text("x")
    h = ls.make_rotating_handler(str(blocker / "bridge.log"))
    assert h is None


def test_rotation_caps_files(tmp_path):
    path = tmp_path / "bridge.log"
    h = ls.make_rotating_handler(str(path), max_bytes=200, backup_count=2)
    logger = logging.getLogger("rotation_test")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(h)
    try:
        for i in range(200):
            logger.info("line %d padding-padding-padding-padding", i)
    finally:
        logger.removeHandler(h)
        h.close()
    # backup_count=2 → bridge.log + .1 + .2, never .3.
    assert path.exists()
    assert (tmp_path / "bridge.log.1").exists()
    assert (tmp_path / "bridge.log.2").exists()
    assert not (tmp_path / "bridge.log.3").exists()


def test_configure_logging_stdout_only_by_default(monkeypatch):
    monkeypatch.delenv("BEATBIRD_LOG_FILE", raising=False)
    ls.configure_logging()
    handlers = logging.getLogger().handlers
    assert any(isinstance(h, logging.StreamHandler) for h in handlers)
    assert not any(isinstance(h, RotatingFileHandler) for h in handlers)


def test_configure_logging_adds_file_handler(tmp_path, monkeypatch):
    path = tmp_path / "bridge.log"
    monkeypatch.setenv("BEATBIRD_LOG_FILE", str(path))
    monkeypatch.setenv("BEATBIRD_LOG_BACKUP_COUNT", "5")
    monkeypatch.setenv("BEATBIRD_LOG_MAX_BYTES", "12345")
    ls.configure_logging()
    file_handlers = [h for h in logging.getLogger().handlers
                     if isinstance(h, RotatingFileHandler)]
    assert len(file_handlers) == 1
    fh = file_handlers[0]
    assert fh.backupCount == 5
    assert fh.maxBytes == 12345
    logging.getLogger("beatbird.bridge").info("hello file")
    assert path.exists()
    assert "hello file" in path.read_text()


def test_configure_logging_falls_back_when_file_unwritable(tmp_path, monkeypatch):
    blocker = tmp_path / "iam_a_file"
    blocker.write_text("x")
    monkeypatch.setenv("BEATBIRD_LOG_FILE", str(blocker / "bridge.log"))
    ls.configure_logging()   # must not raise
    handlers = logging.getLogger().handlers
    assert any(isinstance(h, logging.StreamHandler) for h in handlers)
    assert not any(isinstance(h, RotatingFileHandler) for h in handlers)


def test_env_int_handles_garbage(monkeypatch):
    monkeypatch.setenv("X_INT", "not-a-number")
    assert ls._env_int("X_INT", 99) == 99
    monkeypatch.setenv("X_INT", "7")
    assert ls._env_int("X_INT", 99) == 7
    monkeypatch.delenv("X_INT", raising=False)
    assert ls._env_int("X_INT", 99) == 99
