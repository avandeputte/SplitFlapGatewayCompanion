"""The three platform gaps the July 2026 audit closed.

**Coordinates via get_location.** Apps that need lat/lon (sun-times, iss) used to
hand-roll their own Nominatim ladder because ``get_location()`` only carried
country/subdivision/currency. It now returns the cached coordinates too.

**Triggers get the same helpers as fetch.** ``trigger(settings, conditions)`` was
capability-starved — no i18n, no caps, no weather — which is why trigger code
duplicated everything. A trigger now opts into the same injected kwargs, by
parameter name, and a classic two-arg trigger is called exactly as before.

**One guarded timezone.** ``i18n.tz(settings.get('timezone'))`` replaces the
``pytz.timezone(...)`` boilerplate that half the catalog copied and a third of the
copies forgot to guard. A junk zone name must never take a fetch down; the fallback
is UTC, the only zone that means the same thing on every wall.
"""

import json

from conftest import make_runtime


# -- get_location carries coordinates ---------------------------------------

def test_get_location_carries_coordinates(monkeypatch):
    from app import location
    monkeypatch.setattr(location, "coordinates", lambda s: (42.35, -71.06, "BOSTON"))
    monkeypatch.setattr(location, "_geo",
                        lambda s: {"country": "US", "subdivision": "US-MA"})
    out = location.resolve({})
    assert out["ok"] is True
    assert out["lat"] == 42.35 and out["lon"] == -71.06 and out["city"] == "BOSTON"


def test_get_location_coordinates_survive_failed_reverse_geocode(monkeypatch):
    """Precise coordinates don't need Nominatim; a reverse-geocode failure must not
    hide them. ok stays False (no country) but lat/lon are usable."""
    from app import location
    monkeypatch.setattr(location, "coordinates", lambda s: (42.35, -71.06, "BOSTON"))
    monkeypatch.setattr(location, "_geo",
                        lambda s: {"country": None, "subdivision": None})
    out = location.resolve({})
    assert out["ok"] is False and out["lat"] == 42.35 and out["city"] == "BOSTON"


def test_get_location_no_location_at_all(monkeypatch):
    from app import location
    monkeypatch.setattr(location, "coordinates", lambda s: None)
    monkeypatch.setattr(location, "_geo",
                        lambda s: {"country": None, "subdivision": None})
    out = location.resolve({})
    assert out["ok"] is False and out["lat"] is None and out["city"] is None


# -- guarded timezone --------------------------------------------------------

def test_tz_helper_valid_zone():
    from datetime import datetime

    from app import i18n
    tz = i18n.Localizer("en-US").tz("America/New_York")
    assert str(datetime(2026, 7, 4, 12, 0, tzinfo=None).astimezone(tz).tzinfo) != "UTC"
    assert tz.zone == "America/New_York"


def test_tz_helper_junk_and_blank_fall_back_to_utc():
    from datetime import datetime, timezone

    from app import i18n
    for junk in ("Mars/Olympus_Mons", "", None, 42):
        tz = i18n.tzinfo(junk)
        assert datetime.now(tz).utcoffset() == datetime.now(timezone.utc).utcoffset()


def test_clock_force_overrides_the_locale_default():
    """An app's Time-format toggle (`force`) beats the locale's usual 12h/24h convention,
    while the French `h` separator survives so a forced 12h clock still fits a colon-less reel."""
    from datetime import datetime

    from app import i18n
    dt = datetime(2026, 9, 5, 14, 30)                # 2:30 PM
    assert i18n.Localizer("en-US").time(dt) == "2:30 PM"          # English default: 12h
    assert i18n.Localizer("en-US").time(dt, force="24h") == "14:30"
    assert i18n.Localizer("de").time(dt) == "14:30"              # elsewhere default: 24h
    assert i18n.Localizer("de").time(dt, force="12h") == "2:30 PM"
    assert i18n.Localizer("fr").time(dt, force="12h") == "2h30 PM"   # fr keeps its `h` separator
    assert i18n.Localizer("en-US").time(dt, force="bogus") == "2:30 PM"  # junk -> locale default


# -- trigger helper injection -------------------------------------------------

TRIGGER_APP = '''
def fetch(settings, format_lines, get_rows, get_cols):
    return ["OK"]

def trigger(settings, conditions, caps=None, i18n=None):
    # Proves injection: fires only when the platform handed both helpers over.
    return caps is not None and i18n is not None and hasattr(i18n, "t")
'''

LEGACY_TRIGGER_APP = '''
def fetch(settings, format_lines, get_rows, get_cols):
    return ["OK"]

def trigger(settings, conditions):
    return True
'''


def _runtime_with_app(tmp_path, app_py):
    d = tmp_path / "user_apps" / "trigdemo"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(
        {"id": "trigdemo", "name": "Trig Demo", "type": "functional"}), "utf-8")
    (d / "app.py").write_text(app_py, "utf-8")
    return make_runtime(tmp_path, ["trigdemo"])


def test_trigger_receives_injected_helpers(tmp_path):
    rt = _runtime_with_app(tmp_path, TRIGGER_APP)
    assert rt.has_trigger("trigdemo")
    assert rt.call_trigger("trigdemo", {}) is True


def test_legacy_two_arg_trigger_still_works(tmp_path):
    """The bare two-positional-arg trigger signature is a hard contract — no kwargs for it."""
    rt = _runtime_with_app(tmp_path, LEGACY_TRIGGER_APP)
    assert rt.call_trigger("trigdemo", {}) is True


def test_wanted_helpers_precomputed_for_fetch_and_trigger(tmp_path):
    d = tmp_path / "user_apps" / "wdemo"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(
        {"id": "wdemo", "name": "W", "type": "functional"}), "utf-8")
    (d / "app.py").write_text(
        "def fetch(settings, format_lines, get_rows, get_cols, i18n=None):\n"
        "    return ['X']\n"
        "def trigger(settings, conditions, caps=None):\n"
        "    return False\n", "utf-8")
    rt = make_runtime(tmp_path, ["wdemo"])
    assert rt._wants["wdemo"] == frozenset({"i18n"})
    assert rt._trigger_wants["wdemo"] == frozenset({"caps"})
