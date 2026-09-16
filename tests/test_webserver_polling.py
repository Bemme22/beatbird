"""Poll dampening — die geteilten TTL-Snapshots im Webserver.

Hintergrund (16.09.2026): ein untaetiges Dashboard kostete auf einem Zero 2 W
gemessene 195 `bluetoothctl` + 90 `systemctl` Prozesse pro Minute. Zwei
Ursachen: `bt.list_paired_devices()` ist nicht ein bluetoothctl-Aufruf, sondern
1 + N (ein `devices Paired`, dann ein `info <mac>` je Geraet), und die drei
Poller (now-playing @2s, BT-Karte @4s, BT-Seite @5s) fragten unabhaengig
voneinander dasselbe ab.

Diese Tests halten beide Haelften des Vertrags fest:
  * der Cache greift (sonst waere die Last zurueck), und
  * er laesst rechtzeitig wieder los — nach TTL und sofort nach jeder
    mutierenden Aktion. Ein Cache, den man nicht loswird, ist schlimmer als
    keiner: der Nutzer klickt "Vergessen" und sieht sein Geraet weiter stehen.
"""

import pytest

from beatbird import webserver as w


class _Dev:
    """Minimal-BTDevice, wie die Templates es lesen."""
    def __init__(self, mac="AA:BB:CC:DD:EE:01", alias="Phone",
                 connected=False, paired=True, trusted=True):
        self.mac, self.alias = mac, alias
        self.connected, self.paired, self.trusted = connected, paired, trusted


@pytest.fixture
def clock(monkeypatch):
    """Virtuelle Uhr — die Caches haengen an time.monotonic()."""
    now = {"t": 1000.0}

    class _T:
        @staticmethod
        def monotonic():
            return now["t"]

    monkeypatch.setattr(w, "time", _T)
    return now


@pytest.fixture
def bluez(monkeypatch):
    """Zaehlt, wie oft der Webserver wirklich bei bluez nachfragt."""
    state = {"calls": 0, "devices": [_Dev()]}

    def _list():
        state["calls"] += 1
        return list(state["devices"])

    monkeypatch.setattr(w.bt, "list_paired_devices", _list)
    monkeypatch.setattr(w.bt, "is_discoverable", lambda: False)
    w._bt_cache = (0.0, {})
    yield state
    w._bt_cache = (0.0, {})


# ─── BT-Snapshot ─────────────────────────────────────────────────────────────

def test_three_pollers_share_one_bluez_read(clock, bluez):
    """Der eigentliche Fix: now-playing, BT-Karte und BT-Seite fragen
    dasselbe — innerhalb der TTL darf das EINEN bluez-Zugriff kosten."""
    w._bt_snapshot()
    w._bt_context()
    w.get_bluetooth()
    assert bluez["calls"] == 1


def test_snapshot_refreshes_after_ttl(clock, bluez):
    w._bt_snapshot()
    clock["t"] += w._BT_TTL_S + 0.01
    w._bt_snapshot()
    assert bluez["calls"] == 2


def test_snapshot_holds_just_inside_the_ttl(clock, bluez):
    w._bt_snapshot()
    clock["t"] += w._BT_TTL_S - 0.01
    w._bt_snapshot()
    assert bluez["calls"] == 1


def test_forgetting_a_device_is_visible_immediately(clock, bluez):
    """Ohne Invalidierung stuende das geloeschte Geraet bis zu TTL-Sekunden
    weiter auf dem Schirm — und der Knopf saehe kaputt aus."""
    assert len(w.get_bluetooth()["devices"]) == 1
    bluez["devices"] = []
    w._invalidate_bt()
    assert w.get_bluetooth()["devices"] == []
    assert bluez["calls"] == 2          # kein Warten auf die TTL


def test_connected_flag_reaches_the_dashboard(clock, bluez):
    bluez["devices"] = [_Dev(connected=True)]
    assert w._bt_snapshot()["connected"] is not None


def test_snapshot_fails_soft(clock, bluez, monkeypatch):
    """Ein bluez-Ausfall darf das Dashboard nicht mitreissen — lieber 'kein
    Telefon' anzeigen als eine 500er-Seite."""
    def _boom():
        raise RuntimeError("bluez is having a day")
    monkeypatch.setattr(w.bt, "list_paired_devices", _boom)
    w._bt_cache = (0.0, {})
    snap = w._bt_snapshot()
    assert snap["paired"] == [] and snap["connected"] is None


def test_discoverable_countdown_is_not_cached(clock, bluez, monkeypatch):
    """Die Restsekunden sind eine reine In-Prozess-Rechnung. Waeren sie Teil
    des Snapshots, liefe der Countdown in TTL-Spruengen statt in Sekunden."""
    w._note_discoverable(60)
    first = w._bt_context()["discoverable_seconds_left"]
    clock["t"] += 2.0
    second = w._bt_context()["discoverable_seconds_left"]
    assert first is not None and second is not None
    assert second < first
    assert bluez["calls"] == 1          # trotzdem nur ein bluez-Zugriff


# ─── systemctl-Snapshot ──────────────────────────────────────────────────────

@pytest.fixture
def units(monkeypatch):
    state = {"calls": 0, "active": True}

    def _active(name):
        state["calls"] += 1
        return state["active"]

    monkeypatch.setattr(w.system, "service_active", _active)
    w._service_cache = {}
    yield state
    w._service_cache = {}


def test_repeated_status_ticks_do_not_refork_systemctl(clock, units):
    """Drei is-active-Forks pro Dashboard-Tick waren 90 Prozesse/Minute."""
    for _ in range(5):
        w._service_active("camilladsp")
    assert units["calls"] == 1


def test_each_unit_is_cached_separately(clock, units):
    w._service_active("camilladsp")
    w._service_active("go-librespot")
    assert units["calls"] == 2


def test_service_cache_expires(clock, units):
    w._service_active("camilladsp")
    clock["t"] += w._SERVICE_TTL_S + 0.01
    w._service_active("camilladsp")
    assert units["calls"] == 2


def test_restarting_a_unit_invalidates_only_that_unit(clock, units):
    w._service_active("camilladsp")
    w._service_active("go-librespot")
    w._invalidate_service("camilladsp")
    w._service_active("camilladsp")     # neu gelesen
    w._service_active("go-librespot")   # weiterhin aus dem Cache
    assert units["calls"] == 3
