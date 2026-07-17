"""The category layers of the holidays app. Cultural traditions now live IN the
per-locale data file as recurring records ({name, recurs:"M/D", cultural:true})
— one file per locale, refreshed-but-preserved by scripts/extract_holidays.py —
and show under the Cultural switch (on by default). The fun-day novelty calendar
is the one global, non-localized layer (fun.json, off by default: 366 a year
would drown the real holidays).
"""

import importlib.util
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "apps" / "holidays"
# The app computes "today" with date.today() (local), so the fixture must key its
# cultural record to the SAME local date — keying to UTC drifts by a day across a
# UTC/local midnight boundary and the "shows today" assertion then fails by one day.
_today = datetime.now().date()
KEY = f"{_today.month}/{_today.day}"


def _fixture_app(tmp_path, cultural=None, fun=None):
    """cultural = {locale: name} → a recurring cultural record keyed to TODAY,
    written into that locale's data file alongside a fixed statutory day."""
    d = tmp_path / "hol"
    d.mkdir(parents=True)
    shutil.copy(APP_DIR / "app.py", d / "app.py")
    (d / "data").mkdir()
    files = {}
    for locale, name in (cultural or {}).items():
        files.setdefault(locale, []).append(
            {"name": name, "recurs": KEY, "cultural": True})
    files.setdefault("en-us", [])   # always present as the default country
    for locale, cult_records in files.items():
        (d / "data" / f"{locale}.json").write_text(json.dumps(
            {"locale": locale, "language": locale.split("-")[0],
             "region": locale.split("-")[1].upper(),
             "holidays": [{"name": "Statutory Day", "public": True,
                           "dates": ["2099-01-05"]}] + cult_records}), "utf-8")
    if fun:
        (d / "fun.json").write_text(json.dumps({KEY: [fun]}), "utf-8")
    spec = importlib.util.spec_from_file_location("_holc_test", d / "app.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _I18n(SimpleNamespace):
    def t(self, s, ctx=None):
        return s

    def weekday(self, dt, short=False):
        return "DOW"

    def date(self, dt, short=False, year=False):
        return "DATE"

    def country(self):
        return "US"

    def holiday(self, name):
        return None


def _pages(mod, lang="en", **settings):
    settings.setdefault("country", "US")
    return mod.fetch(settings, lambda *lines, **kw: " ".join(lines),
                     lambda: 3, lambda: 15, i18n=_I18n(lang_base=lang))


def test_cultural_traditions_show_by_default(tmp_path):
    mod = _fixture_app(tmp_path, cultural={"en-us": "Tradition Day"})
    text = " ".join(_pages(mod))
    assert "Tradition Day" in text and "Today" in text


def test_cultural_traditions_can_be_hidden(tmp_path):
    mod = _fixture_app(tmp_path, cultural={"en-us": "Tradition Day"})
    text = " ".join(_pages(mod, cultural_traditions="off"))
    assert "Tradition Day" not in text
    assert "Statutory" in text                 # the dataset layer is untouched


def test_language_picks_the_cultural_locale(tmp_path):
    mod = _fixture_app(tmp_path, cultural={"fr-be": "Jour FR", "nl-be": "Dag NL"})
    assert "Jour FR" in " ".join(_pages(mod, lang="fr", country="BE"))
    assert "Dag NL" in " ".join(_pages(mod, lang="nl", country="BE"))


def test_fun_days_are_off_by_default_and_opt_in(tmp_path):
    mod = _fixture_app(tmp_path, fun="National Donut Day")
    assert "Donut" not in " ".join(_pages(mod))
    assert "Donut" in " ".join(_pages(mod, fun_days="on"))


def test_same_name_same_day_shows_once(tmp_path):
    mod = _fixture_app(tmp_path, cultural={"en-us": "Twin Day"},
                       fun="Twin Day")
    pages = _pages(mod, fun_days="on")
    assert sum("Twin Day" in p for p in pages) == 1


# --- the shipped cultural records (now IN the data files) must be wall-safe ---

_MD = re.compile(r"^([1-9]|1[0-2])/([1-9]|[12][0-9]|3[01])$")
DATA = sorted((APP_DIR / "data").glob("*.json"))


@pytest.mark.parametrize("path", DATA, ids=[p.stem for p in DATA])
def test_shipped_cultural_records_are_wall_safe(path):
    for h in json.loads(path.read_text("utf-8")).get("holidays", []):
        if not h.get("cultural"):
            continue
        assert _MD.match(h.get("recurs", "")), f"{path.name}: bad recurs {h.get('recurs')!r}"
        name = h.get("name", "")
        name.encode("cp1252")
        assert 0 < len(name) <= 40, f"{path.name}: {name!r}"


def _locales_with_cultural():
    out = set()
    for path in (APP_DIR / "data").glob("*.json"):
        if any(h.get("cultural") for h in json.loads(path.read_text("utf-8")).get("holidays", [])):
            out.add(path.stem)
    return out


def test_the_curated_locales_ship():
    have = _locales_with_cultural()
    assert {"de-de", "de-at", "de-ch", "de-be", "fr-fr", "fr-be", "fr-ca",
            "fr-ch", "it-it", "it-ch", "nl-nl", "nl-be", "en-gb", "en-ie",
            "en-ca", "en-au", "en-nz", "sv-se", "da-dk", "no-no", "es-es",
            "es-mx", "es-ar", "pt-pt", "pt-br"} <= have


def test_the_old_app_id_migrates(tmp_path):
    from conftest import make_runtime
    rt = make_runtime(tmp_path, ["national-today", "holidays", "time"])
    assert "national-today" not in rt.settings.installed_apps
    assert "holidays" in rt.settings.installed_apps
