"""
Plugin conformance tests — the guardrail for drop-in compatibility.

Every app under ../../apps must load and satisfy the app-plugin contract:
a valid manifest, and either a functional app.py exposing fetch() or a channel
data.json. If this breaks, a vendored (or dropped-in) app would fail to run.
"""

from pathlib import Path

import pytest

from app.config import Config
from app.plugin_settings import PluginSettings
from app.plugins import PluginRuntime

APPS_DIR = Path(__file__).resolve().parents[2] / "apps"


def _runtime(tmp_path, installed):
    cfg = Config(data_dir=tmp_path)
    ps = PluginSettings(tmp_path)
    ps.set_installed(installed)
    rt = PluginRuntime(cfg, ps, APPS_DIR)
    rt.load()
    return rt


def test_apps_directory_populated():
    rt_ids = PluginRuntime(Config(data_dir=Path("/tmp")), PluginSettings(Path("/tmp")), APPS_DIR).discover()
    assert len(rt_ids) >= 40, f"expected the vendored app library, found {len(rt_ids)}"


def test_every_app_loads(tmp_path):
    """Install ALL discovered apps and assert each one loads cleanly."""
    rt = _runtime(tmp_path, [])  # tmp for discover only
    all_ids = rt.discover()
    rt = _runtime(tmp_path, all_ids)

    failures = []
    for app_id in all_ids:
        manifest = rt.manifest(app_id)
        if manifest is None:
            failures.append(f"{app_id}: manifest missing/failed")
            continue
        if not manifest.get("name"):
            failures.append(f"{app_id}: manifest has no name")
        kind = manifest.get("type")
        if kind == "functional":
            if app_id not in rt._modules:
                failures.append(f"{app_id}: functional but fetch() not loaded")
        elif kind == "channel":
            if app_id not in rt._channel:
                failures.append(f"{app_id}: channel but no data pages")
        else:
            failures.append(f"{app_id}: unknown type {kind!r}")
    assert not failures, "app load failures:\n" + "\n".join(failures)


def test_format_lines_dimensions(tmp_path):
    rt = _runtime(tmp_path, [])
    # 3x15 default grid -> 45 chars, each line centered in 15.
    out = rt.format_lines("HI", "THERE")
    assert len(out) == 45
    assert out[:15] == "HI".center(15)
    assert out[15:30] == "THERE".center(15)


def test_channel_app_returns_pages(tmp_path):
    rt = _runtime(tmp_path, ["dad-jokes"])
    pages = rt.get_pages("dad-jokes")
    assert isinstance(pages, list) and pages
    assert all(isinstance(p, str) for p in pages)
    assert all(len(p) == rt.get_rows() * rt.get_cols() for p in pages)


def test_local_functional_app_runs(tmp_path):
    """`date` renders from the clock only — no network — so it must produce a page."""
    rt = _runtime(tmp_path, ["date"])
    pages = rt.get_pages("date")
    assert isinstance(pages, list) and pages
    assert all(isinstance(p, str) for p in pages)


def test_settings_schema_resolves_keys(tmp_path):
    # weather mixes per-app keys (temperature_unit, ...) with a global_key
    # (weather_api_key), so it exercises both resolution paths.
    rt = _runtime(tmp_path, ["weather"])
    schema = rt.settings_schema("weather")
    assert schema["id"] == "weather"
    keys = {f["key"] for f in schema["fields"]}
    # per-app (non-global) keys are namespaced plugin_<id>_<key>
    assert "plugin_weather_temperature_unit" in keys
    # a catalog global (weather_api_key) is edited under Global settings, not here
    assert "weather_api_key" not in keys
    for f in schema["fields"]:
        assert f["key"] in schema["values"]


def test_save_settings_roundtrip(tmp_path):
    rt = _runtime(tmp_path, ["weather"])
    rt.save_settings("weather", {"plugin_weather_temperature_unit": "celsius",
                                 "weather_api_key": "abc123"})
    schema = rt.settings_schema("weather")
    assert schema["values"]["plugin_weather_temperature_unit"] == "celsius"
    assert schema["values"]["weather_api_key"] == "abc123"
    # a key that isn't this app's namespace or a known global is rejected
    rt.save_settings("weather", {"plugin_other_x": "nope"})
    assert rt.settings.get("plugin_other_x") is None


def test_missing_app_pages_error(tmp_path):
    rt = _runtime(tmp_path, [])
    pages = rt.get_pages("does-not-exist")
    assert pages and "NOT FOUND" in pages[0]


# -- surfacing settings apps read but never declare (no hidden settings) ------

def test_global_editor_is_catalog_only_and_rich(tmp_path):
    """The global editor shows exactly the built-in catalog, always — even with
    only one app installed and the declaring app absent."""
    from app.catalog import CATALOG_KEYS
    rt = _runtime(tmp_path, ["dashboard"])   # weather NOT installed
    fields = {f["key"]: f for f in rt.global_settings_schema()["fields"]}
    assert set(fields) == set(CATALOG_KEYS)
    # renders richly from the catalog even though Weather isn't installed
    assert fields["weather_api_key"]["type"] == "password"
    assert "Dashboard" in fields["weather_api_key"]["note"]   # dashboard reads it


def test_app_dialog_excludes_catalog_globals_but_hints(tmp_path):
    """Catalog globals an app uses (dashboard -> weather_api_key/timezone) are NOT
    fields in the app dialog (edited under Global settings); a hint names them."""
    rt = _runtime(tmp_path, ["dashboard", "weather"])
    schema = rt.settings_schema("dashboard")
    keys = {f["key"] for f in schema["fields"]}
    assert "weather_api_key" not in keys and "timezone" not in keys
    hint = next((f for f in schema["fields"] if f.get("type") == "notice"), None)
    assert hint and "Weather Provider API Key" in hint["label"]


def test_noncatalog_shared_global_still_shown_in_app(tmp_path):
    """A shared-but-not-catalog global (anim_style, used by several anim apps)
    stays in the app dialog, flagged shared."""
    rt = _runtime(tmp_path, ["anim_rainbow", "anim_sweep"])
    fields = {f["key"]: f for f in rt.settings_schema("anim_rainbow")["fields"]}
    assert "anim_style" in fields and fields["anim_style"].get("shared") is True


def test_app_dialog_infers_undeclared_perapp_setting(tmp_path):
    """A private setting an app reads but never declares (sports -> sports_compact,
    read with a 'no' default) is surfaced as an inferred toggle."""
    rt = _runtime(tmp_path, ["sports"])
    fields = {f["key"]: f for f in rt.settings_schema("sports")["fields"]}
    assert "sports_compact" in fields
    assert fields["sports_compact"]["type"] == "toggle"


def test_read_setting_persists_from_app_dialog(tmp_path):
    """Surfaced settings must be saveable from the app dialog."""
    rt = _runtime(tmp_path, ["dashboard", "weather"])
    rt.save_settings("dashboard", {"weather_api_key": "K"})
    assert rt.settings.get("weather_api_key") == "K"


def test_location_composite_writes_component_keys(tmp_path):
    """The catalog 'location_precise' search stores its coordinates into the
    real location_lat/lon/name keys apps read, and rebuilds the chip on read."""
    rt = _runtime(tmp_path, ["weather"])
    rt.save_global_settings({"location_precise": "42.35,-71.08|Boston"})
    assert rt.settings.get("location_lat") == "42.35"
    assert rt.settings.get("location_lon") == "-71.08"
    assert rt.settings.get("location_name") == "Boston"
    assert rt.global_settings_schema()["values"]["location_precise"] == "42.35,-71.08|Boston"


def test_catalog_is_the_reusable_set(tmp_path):
    from app.catalog import CATALOG_KEYS
    # single-app keys are NOT global; reusable infra IS
    assert {"stocks_list", "crypto_list", "world_clock_zones",
            "yt_channel_id", "yt_video_id"} & CATALOG_KEYS == set()
    assert {"weather_api_key", "zip_code", "timezone",
            "global_loop_delay", "yt_api_key"} <= CATALOG_KEYS
