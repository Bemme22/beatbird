"""Snapcast ↔ CamillaDSP volume reconciliation — pure decision logic.

snapclient runs with ``--mixer none``; the per-client volume on the server
is only a register the bridge keeps in step with CamillaDSP. Before this,
the bridge wrote the snapclient % into the display while the DSP poll wrote
the CamillaDSP % back, and the ring jumped between the two.
"""

from beatbird.sources.snapcast import SnapcastClient, reconcile_volume


def test_first_observation_pushes_dsp_to_server():
    # CamillaDSP is the truth at startup; MA/HA gets told what it is.
    assert reconcile_volume(25, None, 60) == ("push", 60)


def test_server_change_is_adopted():
    # MA/HA slider moved → CamillaDSP follows.
    assert reconcile_volume(80, 60, 60) == ("adopt", 80)


def test_server_change_wins_over_dsp_difference():
    # Both moved between two ticks: the slider is the newer user intent.
    assert reconcile_volume(80, 60, 30) == ("adopt", 80)


def test_local_change_is_pushed():
    # Rotary / web / Spotify moved CamillaDSP, server held → push back.
    assert reconcile_volume(60, 60, 45) == ("push", 45)


def test_roundtrip_jitter_is_ignored():
    # pct → dB (0.1-dB rounding) → pct can come back one off; no ping-pong.
    assert reconcile_volume(60, 60, 61) == ("none", 60)
    assert reconcile_volume(60, 60, 59) == ("none", 60)


def test_steady_state_is_quiet():
    assert reconcile_volume(60, 60, 60) == ("none", 60)


# ─── get_state / set_volume wire format ─────────────────────────────────────

_STATUS = {"result": {"server": {
    "groups": [{
        "name": "ma_robinpi", "stream_id": "s1",
        "clients": [{
            "id": "robinpi", "connected": True,
            "host": {"mac": "AA:BB:CC:DD:EE:FF"},
            "config": {"volume": {"percent": 42, "muted": True}},
        }],
    }],
    "streams": [{"id": "s1", "status": "playing"}],
}}}


def test_get_state_reports_client_id_and_mute(monkeypatch):
    c = SnapcastClient(host="h", my_mac="aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(c, "_rpc", lambda *a, **k: _STATUS)
    s = c.get_state()
    assert s["client_id"] == "robinpi"
    assert s["volume_pct"] == 42
    assert s["muted"] is True
    assert s["playing"] is True


def test_set_volume_sends_client_setvolume(monkeypatch):
    sent = {}

    def fake_rpc(method, params=None):
        sent["method"], sent["params"] = method, params
        return {"id": 1, "jsonrpc": "2.0", "result": {"volume": params["volume"]}}

    c = SnapcastClient(host="h", my_mac="aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(c, "_rpc", fake_rpc)
    assert c.set_volume("robinpi", 130) is True
    assert sent["method"] == "Client.SetVolume"
    assert sent["params"] == {"id": "robinpi",
                              "volume": {"muted": False, "percent": 100}}


def test_set_volume_without_client_id_is_a_noop(monkeypatch):
    c = SnapcastClient(host="h", my_mac="x")
    monkeypatch.setattr(c, "_rpc", lambda *a, **k: 1 / 0)  # must not be called
    assert c.set_volume("", 50) is False
