"""The cultural and fun layers the holidays app absorbed from National Today:
curated traditions per language-region (cultural/<locale>.json, locale-picked
like the dataset, ON by default), and the one-a-day novelty calendar (fun.json,
OFF by default — 366 a year would drown the real holidays). Both are M/D-keyed
(they recur yearly) and live OUTSIDE data/, which the dataset rebuild wipes.
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
_today = datetime.now(timezone.utc)
KEY = f"{_today.month}/{_today.day}"


def _fixture_app(tmp_path, cultural=None, fun=None):
    d = tmp_path / "hol"
    d.mkdir(parents=True)
    shutil.copy(APP_DIR / "app.py", d / "app.py")
    (d / "data").mkdir()
    (d / "data" / "en-us.json").write_text(json.dumps(
        {"locale": "en-us", "language": "en", "region": "US",
         "holidays": [{"name": "Statutory Day", "public": True,
                       "dates": ["2099-01-05"]}]}), "utf-8")
    (d / "cultural").mkdir()
    for locale, name in (cultural or {}).items():
        (d / "cultural" / f"{locale}.json").write_text(
            json.dumps({KEY: [name]}), "utf-8")
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


# --- the shipped curation must be wall-safe ---------------------------------

_KEY = re.compile(r"^([1-9]|1[0-2])/([1-9]|[12][0-9]|3[01])$")
_LOCALE = re.compile(r"^[a-z]{2}-[a-z]{2}$")
SHIPPED = sorted((APP_DIR / "cultural").glob("*.json")) + [APP_DIR / "fun.json"]


@pytest.mark.parametrize("path", SHIPPED, ids=[p.stem for p in SHIPPED])
def test_shipped_layer_file_is_wall_safe(path):
    if path.parent.name == "cultural":
        assert _LOCALE.match(path.stem), f"{path.name}: not a <lang>-<cc> name"
    doc = json.loads(path.read_text("utf-8"))
    assert isinstance(doc, dict) and doc
    for key, names in doc.items():
        assert _KEY.match(key), f"{path.name}: bad date key {key!r}"
        assert isinstance(names, list) and names
        for n in names:
            n.encode("cp1252")
            assert len(n) <= 40, f"{path.name}: {key}: {n!r}"


def test_the_curated_locales_ship():
    have = {p.stem for p in (APP_DIR / "cultural").glob("*.json")}
    assert {"de-de", "de-at", "de-ch", "de-be", "fr-fr", "fr-be", "fr-ca",
            "fr-ch", "it-it", "it-ch", "nl-nl", "nl-be", "en-gb", "en-ie",
            "en-ca", "en-au", "en-nz", "sv-se", "da-dk", "no-no", "es-es",
            "es-mx", "es-ar", "pt-pt", "pt-br"} <= have


def test_the_old_app_id_migrates(tmp_path):
    from conftest import make_runtime
    rt = make_runtime(tmp_path, ["national-today", "holidays", "time"])
    assert "national-today" not in rt.settings.installed_apps
    assert "holidays" in rt.settings.installed_apps
