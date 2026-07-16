"""National Today's country sidecars: where the wall IS decides whose days it
celebrates. A wall in Germany leads with the German day and keeps the
international one after it; a date with no local entry falls back to the
default file; a country with no sidecar changes nothing at all.

The merge tests run against fixture data (copied app + synthetic sidecars keyed
to today), so they hold regardless of what the shipped curation contains. The
conformance tests then validate every SHIPPED holidays_<cc>.json: real
calendar keys, wall-renderable characters, names short enough to wrap onto a
15-column sign.
"""

import importlib.util
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "apps" / "national-today"


def _load_from(app_dir):
    spec = importlib.util.spec_from_file_location("_nt_test", app_dir / "app.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _fixture_app(tmp_path, local_entry="Deutsch Tag"):
    d = tmp_path / "nt"
    d.mkdir()
    shutil.copy(APP_DIR / "app.py", d / "app.py")
    now = datetime.now(timezone.utc)
    key = f"{now.month}/{now.day}"
    (d / "holidays.json").write_text(json.dumps({key: ["Base Day"]}), "utf-8")
    (d / "holidays_de.json").write_text(json.dumps({key: [local_entry]}), "utf-8")
    return _load_from(d)


def _pages(mod, country):
    loc = (lambda: {"country": country}) if country is not None else None
    return mod.fetch({"timezone": "UTC"}, lambda *lines, **kw: " ".join(lines),
                     lambda: 3, lambda: 15, get_location=loc)


def test_no_location_keeps_the_default_day(tmp_path):
    pages = _pages(_fixture_app(tmp_path), None)
    assert pages == ["Today is Base Day"]


def test_local_day_leads_and_default_follows(tmp_path):
    pages = _pages(_fixture_app(tmp_path), "DE")
    assert pages[0] == "Today is Deutsch Tag"
    assert pages[1] == "Today is Base Day"


def test_country_without_a_sidecar_changes_nothing(tmp_path):
    pages = _pages(_fixture_app(tmp_path), "CH")
    assert pages == ["Today is Base Day"]


def test_identical_local_and_default_day_dedupes(tmp_path):
    pages = _pages(_fixture_app(tmp_path, local_entry="Base Day"), "DE")
    assert pages == ["Today is Base Day"]


# --- the shipped curation must be wall-safe ---------------------------------

_KEY = re.compile(r"^([1-9]|1[0-2])/([1-9]|[12][0-9]|3[01])$")
SIDECARS = sorted(APP_DIR.glob("holidays_*.json"))


@pytest.mark.parametrize("path", SIDECARS, ids=[p.stem for p in SIDECARS])
def test_shipped_sidecar_is_wall_safe(path):
    doc = json.loads(path.read_text("utf-8"))
    assert isinstance(doc, dict) and doc, f"{path.name}: empty or not an object"
    for key, names in doc.items():
        assert _KEY.match(key), f"{path.name}: bad date key {key!r}"
        assert isinstance(names, list) and names, f"{path.name}: {key} has no names"
        for n in names:
            n.encode("cp1252")           # must be showable on the modules
            assert len(n) <= 40, f"{path.name}: {key} name too long: {n!r}"


def test_at_least_the_promised_countries_ship():
    """The country files this feature was built around. A missing one here means
    the curation quietly vanished, not that it was never planned.
    (Curation is PAUSED at three countries by request — de/fr/gb/nl/se/es/it/dk/no
    are still to come; grow this set as they land.)"""
    have = {p.stem.split("_", 1)[1] for p in SIDECARS}
    assert {"pt", "ca", "au"} <= have
