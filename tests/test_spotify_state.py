"""SpotifyClient.get_state — the _call contract (None / {} / dict).

get_state must return None for BOTH a failed call (None) and a content-less
success ({}); an empty dict must not build a degenerate all-defaults state.
_call is mocked so these run without the `requests` lib or a live go-librespot.
"""

from beatbird.sources.spotify import SpotifyClient, SpotifyState


def _client_returning(call_result):
    c = SpotifyClient()
    c._call = lambda *a, **k: call_result   # type: ignore[assignment]
    return c


def test_get_state_none_on_failed_call():
    assert _client_returning(None).get_state() is None


def test_get_state_none_on_empty_dict():
    # 204 / empty-body success must NOT become a fake "stopped" state.
    assert _client_returning({}).get_state() is None


def test_get_state_parses_valid_status():
    status = {
        "stopped": False, "paused": False,
        "volume": 32768, "volume_steps": 65535,
        "track": {
            "name": "Song", "artist_names": ["Artist"],
            "album_name": "Album", "uri": "spotify:track:x",
            "position": 1000, "duration": 200000,
            "album_cover_url": "http://cover",
        },
    }
    st = _client_returning(status).get_state()
    assert isinstance(st, SpotifyState)
    assert st.stopped is False and st.paused is False
    assert st.title == "Song"
    assert st.artist == "Artist"
    assert st.album == "Album"
    assert st.track_uri == "spotify:track:x"
    assert st.volume == 32768
    assert st.album_cover_url == "http://cover"


def test_get_state_artist_fallback_to_artists_objs():
    # No artist_names → fall back to the artists[] objects.
    status = {"track": {"name": "S", "artists": [{"name": "Band"}]}}
    assert _client_returning(status).get_state().artist == "Band"


def test_get_state_album_fallback_to_album_obj():
    status = {"track": {"name": "S", "album": {"name": "Nested"}}}
    assert _client_returning(status).get_state().album == "Nested"


def test_get_state_duration_floors_to_one():
    # duration 0 must floor to 1 so the progress calc never divides by zero.
    assert _client_returning({"track": {"name": "S", "duration": 0}}).get_state().duration_ms == 1
