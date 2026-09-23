"""Web UI status/derivation layer — the bits between the profile and what a
template actually renders.

Kept to the same boundary as the rest of tests/: no ALSA, no I2C, no serial.
Everything here is either pure or monkeypatched at the module seam, so the
suite still runs on a fresh clone with nothing installed.

These cover three defects found in the 2026-09-16 web audit:
  * the dashboard could not display Snapcast at all,
  * a merely-connected phone outranked a playing source,
  * the loudness allowlist was a hardcoded constant that had drifted.
"""

from pathlib import Path

import pytest

from beatbird import webserver as w
from beatbird.config import load_profile


PROFILES_DIR = Path(__file__).parent.parent / "profiles"


def _profile(name: str):
    return load_profile(PROFILES_DIR / f"{name}.yml")


@pytest.fixture(autouse=True)
def _clean_caches():
    """Drop the webserver's poll-dampening caches around every test.

    They are module-level by design (the point is that pollers share them),
    which means without this a test inherits whatever the previous one left
    behind — the first version of this suite had exactly that bug: a test
    wiring bt_connected=False poisoned the snapshot for the next one."""
    w._bt_cache = (0.0, {})
    w._service_cache = {}
    w._snap_cache = (0.0, {})
    yield
    w._bt_cache = (0.0, {})
    w._service_cache = {}
    w._snap_cache = (0.0, {})


@pytest.fixture
def as_speaker(monkeypatch):
    """Pin the webserver to one speaker's profile, bypassing the module cache."""
    def _use(name: str):
        prof = _profile(name)
        monkeypatch.setattr(w, "_get_profile", lambda: prof)
        return prof
    return _use


# ─── _tunable_filters — derived from the profile, not hardcoded ──────────────

@pytest.mark.parametrize("speaker,expected", [
    # beat-1 runs the four-filter voicing; air_lift is one of them and was
    # missing from the old hardcoded set, so it could not be tuned at all.
    ("beat-1",      {"bass_shelf", "sub_punch", "timpani_body", "air_lift"}),
    ("beatpimini",  {"bass_shelf", "sub_punch", "timpani_body", "air_lift"}),
    ("lounge",      {"bass_shelf", "sub_punch", "timpani_body"}),
    # The two-filter speakers were being offered sub_punch/fullness, neither of
    # which exists in their DSP config.
    ("zipp-mini-2", {"bass_shelf", "timpani_body"}),
    # robinpi runs the NATIVE CamillaDSP loudness since 15.09. — the bridge
    # patches nothing (loudness.enabled: false, filters: []), so nothing is
    # tunable here. Offering bass_shelf would re-open the double-boost path.
    ("robinpi",     set()),
])
def test_tunable_filters_follow_the_profile(as_speaker, speaker, expected):
    as_speaker(speaker)
    assert w._tunable_filters() == expected


def test_fullness_is_never_tunable(as_speaker):
    """`fullness` is static voicing — it appears in some CamillaDSP configs but
    in no profile's loudness list, so the bridge never patches it. Offering it
    in the loudness card implied the opposite."""
    for speaker in ("beat-1", "beatpimini", "lounge", "zipp-mini-2", "robinpi"):
        as_speaker(speaker)
        assert "fullness" not in w._tunable_filters(), speaker


def test_tunable_filters_never_include_structural_filters(as_speaker):
    """Crossovers/limiters/protection must stay out — patching one from the
    browser is how you send full range at a ribbon."""
    forbidden = {"xover_lp", "xover_hp", "sub_protect", "broadband_limiter",
                 "ribbon_limiter", "ribbon_protect_hp", "headroom"}
    for speaker in ("beat-1", "beatpimini", "lounge", "zipp-mini-2", "robinpi"):
        as_speaker(speaker)
        assert not (w._tunable_filters() & forbidden), speaker


# ─── _disk_free_root — stable key set ────────────────────────────────────────

def test_disk_keys_always_present():
    """All six keys exist even where /media/root-rw does not. A missing key
    resolves to Jinja Undefined, which is not None, so `is not none` passed on
    exactly the machines with no overlay and rendered an empty MB line."""
    d = w._disk_free_root()
    for key in ("root_total_mb", "root_used_mb", "root_free_mb",
                "upper_total_mb", "upper_used_mb", "upper_free_mb"):
        assert key in d


def test_overlay_line_hidden_without_an_upper_layer():
    """The guard the template actually uses, against the real dict."""
    from jinja2 import Environment
    tpl = Environment().from_string(
        "{% if d.upper_free_mb is not none %}shown{% else %}hidden{% endif %}")
    assert tpl.render(d=dict(w._disk_free_root(), upper_free_mb=None)) == "hidden"
    assert tpl.render(d=dict(w._disk_free_root(), upper_free_mb=120)) == "shown"


# ─── _status_for_template — source precedence ────────────────────────────────

def _wire(monkeypatch, *, spotify=None, snapcast=None, bt_connected=False):
    """Stub the three things the source decision reads."""
    monkeypatch.setattr(w, "get_status", lambda: {"spotify": spotify, "volume": 40})
    monkeypatch.setattr(w, "_snapcast_snapshot", lambda *a, **k: snapcast or {})

    class _Dev:
        def __init__(self, connected): self.connected = connected
    monkeypatch.setattr(w.bt, "list_paired_devices",
                        lambda: [_Dev(bt_connected)] if bt_connected else [])


PLAYING_SPOTIFY = {"stopped": False, "paused": False,
                   "title": "Weightless", "artist": "Marconi Union"}
PLAYING_SNAP = {"playing": True, "title": "Kitchen Mix", "artist": "MA"}


def test_snapcast_playing_is_reported(monkeypatch):
    """The template has always had a src-snapcast pill; nothing ever set it."""
    _wire(monkeypatch, snapcast=PLAYING_SNAP)
    s = w._status_for_template()
    assert s["source"] == "snapcast"
    assert s["playback"] == "Playing"
    assert (s["title"], s["artist"]) == ("Kitchen Mix", "MA")


def test_idle_bluetooth_does_not_outrank_playing_spotify(monkeypatch):
    """The regression this replaces: pill said Bluetooth while the title and
    artist below it came from Spotify."""
    _wire(monkeypatch, spotify=PLAYING_SPOTIFY, bt_connected=True)
    s = w._status_for_template()
    assert s["source"] == "spotify"
    assert s["title"] == "Weightless"


def test_idle_bluetooth_does_not_outrank_snapcast(monkeypatch):
    _wire(monkeypatch, snapcast=PLAYING_SNAP, bt_connected=True)
    assert w._status_for_template()["source"] == "snapcast"


def test_bluetooth_wins_when_nothing_else_plays(monkeypatch):
    _wire(monkeypatch, spotify={"stopped": True}, bt_connected=True)
    s = w._status_for_template()
    assert s["source"] == "bluetooth"
    # Not claimed as Playing: the web layer cannot see whether A2DP is
    # actually streaming, and guessing would be a lie on the glass.
    assert s["playback"] == "Stopped"
    assert (s["title"], s["artist"]) == ("", "")


def test_snapcast_configured_but_idle_is_not_a_source(monkeypatch):
    _wire(monkeypatch, spotify=PLAYING_SPOTIFY,
          snapcast={"playing": False, "title": "stale"})
    s = w._status_for_template()
    assert s["source"] == "spotify"
    assert s["title"] == "Weightless"


def test_nothing_playing(monkeypatch):
    _wire(monkeypatch, spotify={"stopped": True})
    s = w._status_for_template()
    assert (s["source"], s["playback"]) == ("none", "Stopped")


def test_paused_spotify_still_names_spotify(monkeypatch):
    _wire(monkeypatch, spotify={**PLAYING_SPOTIFY, "paused": True})
    s = w._status_for_template()
    assert (s["source"], s["playback"]) == ("spotify", "Paused")


def test_bt_lookup_failure_does_not_break_the_dashboard(monkeypatch):
    """bluetoothctl missing or hanging must not 500 the page."""
    _wire(monkeypatch, spotify=PLAYING_SPOTIFY)

    def _boom():
        raise OSError("bluetoothctl not found")
    monkeypatch.setattr(w.bt, "list_paired_devices", _boom)
    assert w._status_for_template()["source"] == "spotify"


# ─── _snapcast_snapshot caching ──────────────────────────────────────────────

def test_snapcast_snapshot_is_cached(as_speaker, monkeypatch):
    """The dashboard polls every 2 s and /advanced every 5 s; without a shared
    cache each poll opened its own TCP connection to the snapserver."""
    as_speaker("robinpi")
    monkeypatch.setattr(w, "_snap_cache", (0.0, {}))
    calls = []

    class _Client:
        def __init__(self, **kw): pass
        def get_state(self):
            calls.append(1)
            return {"playing": True, "group_name": "g", "stream": "s"}

    import beatbird.sources.snapcast as sc
    monkeypatch.setattr(sc, "SnapcastClient", _Client)
    monkeypatch.setattr(sc, "get_local_wlan_mac", lambda: "aa:bb:cc:dd:ee:ff")
    monkeypatch.setenv("BEATBIRD_SNAPCAST_SERVER", "snap.example")

    first = w._snapcast_snapshot()
    second = w._snapcast_snapshot()
    assert first["playing"] is True
    assert second == first
    assert len(calls) == 1, "second read inside the TTL must not re-query"
