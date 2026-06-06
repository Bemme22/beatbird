"""Tests for SpotifyClient.get_state() parsing.

go-librespot's /status JSON shape drifts between versions (artist as
`artist_names` list vs nested `artists` objects, album as `album_name` vs
nested `album.name`). get_state() normalises all of that into a flat
SpotifyState. These tests lock the parsing + the None contract that the
bridge's health watchdog relies on, without touching the network — `_call`
is stubbed per instance.
"""

from beatbird.sources.spotify import SpotifyClient, SpotifyState


def _client(status):
    """A SpotifyClient whose GET /status returns `status` (no HTTP)."""
    c = SpotifyClient()
    c._call = lambda method, endpoint, **kw: status if endpoint == "/status" else None
    return c


def test_transport_failure_returns_none():
    # _call returns None on HTTP/transport failure → no usable state.
    assert _client(None).get_state() is None


def test_empty_status_returns_none():
    # A 204 / unparseable body surfaces from _call as {}. get_state must
    # collapse it to None so the bridge counts it toward the librespot
    # health-restart threshold instead of reporting a degenerate stopped
    # state.
    assert _client({}).get_state() is None


def test_stopped_status_is_a_real_state():
    # A genuine idle librespot still carries `stopped` — that's a valid
    # state, NOT the None failure path.
    st = _client({"stopped": True}).get_state()
    assert isinstance(st, SpotifyState)
    assert st.stopped is True


def test_full_track_parsed():
    st = _client({
        "stopped": False,
        "paused": False,
        "volume": 32768,
        "volume_steps": 65535,
        "track": {
            "name": "Once in a Lifetime",
            "artist_names": ["Talking Heads", "Brian Eno"],
            "album_name": "Remain in Light",
            "uri": "spotify:track:xyz",
            "position": 45300,
            "duration": 232000,
            "album_cover_url": "http://img/cover.jpg",
        },
    }).get_state()
    assert st.title == "Once in a Lifetime"
    assert st.artist == "Talking Heads"   # first of the list
    assert st.album == "Remain in Light"
    assert st.position_ms == 45300
    assert st.duration_ms == 232000
    assert st.album_cover_url == "http://img/cover.jpg"


def test_artist_falls_back_to_nested_objects():
    # Older shape: no artist_names, artists as a list of {name: ...} dicts.
    st = _client({
        "stopped": False,
        "track": {"name": "T", "artists": [{"name": "Aphex Twin"}]},
    }).get_state()
    assert st.artist == "Aphex Twin"


def test_album_falls_back_to_nested_name():
    st = _client({
        "stopped": False,
        "track": {"name": "T", "album": {"name": "Selected Ambient Works"}},
    }).get_state()
    assert st.album == "Selected Ambient Works"


def test_duration_clamped_to_at_least_one():
    # duration_ms feeds a divisor on the display side — never let it hit 0.
    st = _client({"stopped": False, "track": {"name": "T", "duration": 0}}).get_state()
    assert st.duration_ms == 1


def test_missing_track_is_safe():
    # status present but no `track` key → defaults, no crash.
    st = _client({"stopped": False}).get_state()
    assert st.title == ""
    assert st.artist == ""
    assert st.duration_ms == 1
