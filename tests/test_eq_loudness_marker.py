"""_bridge_loudness_names — welche Baender die BRIDGE pro Lautstaerke patcht.

Regressionstest fuer eine Fehlerklasse, die am 15./16.09.2026 dreimal zugeschlagen
hat: eine fest einkompilierte Liste von Filternamen wird still falsch, sobald ein
Filter umbenannt wird oder der Speaker das Verfahren wechselt.

Vorher stand hier ein Modul-Konstante:

    _TUNABLE_FILTERS = {"bass_shelf", "sub_punch", "timpani_body", "fullness"}

Als bass_shelf -> bass_voicing umbenannt wurde, traf sie nichts mehr. Folgen:
der EQ-Editor markierte kein Band mehr als loudness-eigen (und warnte trotzdem),
und das Diagnose-Filterpanel war leer, ohne dass jemand es merkte. Die Menge wird
deshalb jetzt aus dem PROFIL abgeleitet — aus derselben Quelle, aus der auch die
Bridge ihre Filterliste nimmt.
"""

import pytest


class _F:
    def __init__(self, name, max_boost_db=3.0):
        self.name = name
        self.max_boost_db = max_boost_db


class _Loud:
    curve = "legacy"

    def __init__(self, filters, enabled=True):
        self.filters = filters
        self.enabled = enabled


class _Audio:
    def __init__(self, loud):
        self.loudness = loud


class _Profile:
    def __init__(self, filters, enabled=True):
        self.audio = _Audio(_Loud(filters, enabled))


@pytest.fixture
def names(monkeypatch):
    """_bridge_loudness_names() gegen ein gestelltes Profil."""
    from beatbird import webserver

    def _run(profile):
        monkeypatch.setattr(webserver, "_get_profile", lambda: profile)
        return webserver._bridge_loudness_names()
    return _run


def test_names_come_from_the_profile(names):
    """Die Namen stammen aus dem Profil — nicht aus einer Liste im Code."""
    assert names(_Profile([_F("bass_shelf"), _F("timpani_body")])) == {
        "bass_shelf", "timpani_body"}


def test_renaming_a_filter_is_tracked(names):
    """Der eigentliche Regressionsfall: nach einer Umbenennung muss die Menge
    mitwandern. Die alte Konstante haette hier leer geliefert und damit ein
    gepatchtes Band als 'frei editierbar' ausgewiesen."""
    assert names(_Profile([_F("bass_voicing"), _F("timpani_voicing")])) == {
        "bass_voicing", "timpani_voicing"}


def test_empty_when_loudness_disabled(names):
    """Nativer CamillaDSP-Loudness-Filter: die Bridge patcht dann gar nichts,
    also darf KEIN Band als loudness-eigen gelten — sonst warnt der EQ-Editor
    vor einem Mechanismus, den dieser Speaker nicht mehr hat."""
    assert names(_Profile([_F("bass_shelf")], enabled=False)) == set()


def test_empty_when_no_filters(names):
    assert names(_Profile([])) == set()


def test_unreadable_profile_does_not_raise(monkeypatch):
    """Ein kaputtes/fehlendes Profil darf /api/eq/bands nicht mitreissen —
    im Zweifel lieber 'kein Band gehoert der Bridge' als ein 500er."""
    from beatbird import webserver

    def _boom():
        raise RuntimeError("no profile")
    monkeypatch.setattr(webserver, "_get_profile", _boom)
    assert webserver._bridge_loudness_names() == set()
