"""The holidays app against its bundled dataset: locale ladder, the religious-
observance toggle with per-tradition filters, the ~estimated marker, and the
tall-wall layouts that used to be pinned through the Nager stub (the app is
offline now — layout tests run on fixture data with far-future dates so they
never age into time bombs the way a "next 4 holidays" assertion against the
real calendar would).
"""

import importlib.util
import json
import shutil
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "apps" / "holidays"


def _fixture_app(tmp_path, holidays, locale="en-us"):
    d = tmp_path / "hol"
    d.mkdir()
    shutil.copy(APP_DIR / "app.py", d / "app.py")
    (d / "data").mkdir()
    (d / "data" / f"{locale}.json").write_text(json.dumps(
        {"locale": locale, "language": locale.split("-")[0],
         "region": locale.split("-")[1].upper(), "holidays": holidays}), "utf-8")
    spec = importlib.util.spec_from_file_location("_hol_test", d / "app.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


FIXTURE = [
    {"name": "Martin Luther King Jr. Day", "public": True, "dates": ["2099-01-18"]},
    {"name": "Labor Day", "public": True, "dates": ["2099-09-06"]},
    {"name": "Epiphany", "religious": True, "tradition": "christian",
     "dates": ["2099-01-06"]},
    {"name": "Eid al-Fitr", "religious": True, "tradition": "islamic",
     "dates": ["~2099-02-19"]},
    {"name": "Regional Day", "public": True, "subdivisions": ["US-TX"],
     "dates": ["2099-03-02"]},
]


def _pages(m, rows=5, cols=15, get_location=None, **settings):
    settings.setdefault("country", "US")
    return m.fetch(settings, lambda *lines, **kw: list(lines),
                   lambda: rows, lambda: cols, get_location=get_location)


def _flat(page):
    return " ".join(page)


# --- filtering ---------------------------------------------------------------

def test_public_holidays_only_by_default(tmp_path):
    m = _fixture_app(tmp_path, FIXTURE)
    text = " ".join(_flat(p) for p in _pages(m))
    assert "Martin" in text and "Labor" in text
    assert "Epiphany" not in text and "Eid" not in text
    assert "Regional" not in text          # someone else's subdivision


def test_religious_toggle_adds_observances(tmp_path):
    m = _fixture_app(tmp_path, FIXTURE)
    text = " ".join(_flat(p) for p in _pages(m, religious_holidays="on"))
    assert "Epiphany" in text and "Eid" in text
    assert "Martin" in text                # public days never disappear


def test_tradition_filter_is_respected(tmp_path):
    m = _fixture_app(tmp_path, FIXTURE)
    text = " ".join(_flat(p) for p in _pages(
        m, religious_holidays="on", tradition_islamic="off"))
    assert "Epiphany" in text
    assert "Eid" not in text


def test_estimated_dates_carry_a_tilde(tmp_path):
    m = _fixture_app(tmp_path, FIXTURE)
    eid = [p for p in _pages(m, religious_holidays="on") if "Eid" in _flat(p)][0]
    assert any(l.startswith("~") for l in eid), eid


def test_own_subdivision_is_included(tmp_path):
    m = _fixture_app(tmp_path, FIXTURE)
    loc = lambda: {"country": "US", "subdivision": "US-TX"}
    text = " ".join(_flat(p) for p in _pages(m, get_location=loc))
    assert "Regional" in text


# --- locale ladder -----------------------------------------------------------

def test_locale_prefers_the_walls_language_then_english_then_any():
    spec = importlib.util.spec_from_file_location("_hol_real", APP_DIR / "app.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m._pick_locale("fr", "CA") == "fr-ca"      # the wall's language there
    assert m._pick_locale("sv", "US") == "en-us"      # no sv-us -> English there
    assert m._pick_locale("en", "VA") == "it-va"      # no English -> any for the country
    assert m._pick_locale("en", "ZZ") is None         # nothing for the country


# --- the tall-wall layouts, formerly pinned through the Nager stub ------------

def test_the_holiday_name_survives_a_tall_wall_intact(tmp_path):
    """MARTIN LUTHER KING JR. DAY is 26 characters on a 15-wide wall. It used to
    be cut; it wraps, and every word of it is on the page."""
    m = _fixture_app(tmp_path, FIXTURE)
    mlk = [p for p in _pages(m) if "Martin" in _flat(p)][0]
    text = _flat(mlk)
    for word in ("Martin", "Luther", "King"):
        assert word in text, f"{word} was truncated away"
    assert all(len(l) <= 15 for l in mlk)


def test_a_tall_wall_spends_its_spare_row_on_the_date(tmp_path):
    """A short name (LABOR DAY) leaves a row over: say WHEN, don't show blank flaps."""
    m = _fixture_app(tmp_path, FIXTURE)
    labor = [p for p in _pages(m) if "Labor" in _flat(p)][0]
    assert any("Sep" in l for l in labor), labor
    assert any(l.startswith("In ") for l in labor), "the countdown is the point"


# --- the real dataset is present and sane -------------------------------------

def test_shipped_dataset_covers_the_home_locales():
    have = {f.stem for f in (APP_DIR / "data").glob("*.json") if not f.stem.startswith("_")}
    assert {"en-us", "en-gb", "fr-fr", "fr-ca", "de-de", "es-es", "it-it",
            "nl-nl", "pt-pt", "sv-se", "da-dk", "no-no", "ca-es"} <= have
    assert len(have) > 150


def test_three_row_wall_gives_up_the_header_before_it_truncates(tmp_path):
    """On the common wall, a name that fits keeps 'Next holiday'; one that doesn't
    takes that row rather than losing half of itself — the header says less than
    the name it was cutting in half."""
    m = _fixture_app(tmp_path, FIXTURE)
    pages = _pages(m, rows=3)
    short = [p for p in pages if "Labor" in _flat(p)][0]
    assert "Next holiday" in _flat(short)
    long = [p for p in pages if "Martin" in _flat(p)][0]
    assert "Next holiday" not in _flat(long), "the header must yield to the name"
    assert "Martin" in _flat(long) and "King" in _flat(long)
