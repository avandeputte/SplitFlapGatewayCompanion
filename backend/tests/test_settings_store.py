"""app_settings.json is stored in two readable sections — ``global`` (the reusable
catalog) and ``apps`` (per-app). There is no cross-app 'shared' bucket: a stray
bare key is never persisted, and legacy files load without one."""

import json

from app.plugin_settings import PluginSettings


def test_sectioned_layout_and_roundtrip(tmp_path):
    ps = PluginSettings(tmp_path)
    ps.set_known_apps(["weather"])
    ps.update({
        "weather_api_key": "K",           # catalog key      -> global
        "plugin_weather_show_aqi": "no",  # per-app          -> apps.weather.show_aqi
    })
    doc = json.loads((tmp_path / "app_settings.json").read_text())
    assert doc["global"]["weather_api_key"] == "K"
    assert doc["apps"]["weather"]["show_aqi"] == "no"
    assert "shared" not in doc               # no cross-app bucket
    # reload -> flat values preserved
    ps2 = PluginSettings(tmp_path)
    assert ps2.get("weather_api_key") == "K"
    assert ps2.get("plugin_weather_show_aqi") == "no"


def test_stray_bare_key_is_not_persisted(tmp_path):
    """A non-catalog bare key (an old cross-app value) is never written to disk —
    settings are cleanly global-or-per-app."""
    ps = PluginSettings(tmp_path)
    ps.set_known_apps(["anim_rainbow"])
    ps.update({"anim_style": "rtl"})         # bare, non-catalog -> dropped
    doc = json.loads((tmp_path / "app_settings.json").read_text())
    assert "shared" not in doc
    assert "anim_style" not in json.dumps(doc)


def test_legacy_shared_section_is_dropped_on_load(tmp_path):
    """Loading an old file keeps global + per-app values but discards the whole
    legacy 'shared' section (no backward-compat carry-over)."""
    (tmp_path / "app_settings.json").write_text(json.dumps({
        "global": {"weather_api_key": "K"},
        "shared": {"anim_style": "rtl", "sports_ger": "B04", "crypto_list": "bitcoin"},
        "apps": {"weather": {"show_aqi": "no"}},
        "installed_apps": ["weather"],
    }))
    ps = PluginSettings(tmp_path)
    assert ps.get("weather_api_key") == "K"
    assert ps.get("plugin_weather_show_aqi") == "no"
    assert ps.get("anim_style") is None          # dropped
    assert ps.get("sports_ger") is None          # dropped
    assert ps.get("crypto_list") is None         # dropped


def test_migrates_legacy_flat_file(tmp_path):
    """A pre-sections flat file keeps its recognized keys (catalog + per-app) and
    drops bare cross-app cruft, then re-saves sectioned."""
    (tmp_path / "app_settings.json").write_text(json.dumps({
        "weather_api_key": "K",
        "plugin_weather_show_aqi": "no",
        "anim_style": "rtl",                     # bare non-catalog -> dropped
        "installed_apps": ["weather"],
    }))
    ps = PluginSettings(tmp_path)
    assert ps.get("weather_api_key") == "K"
    assert ps.get("plugin_weather_show_aqi") == "no"
    assert ps.get("anim_style") is None
    ps.set_known_apps(["weather"])
    ps.set("saved_app_playlists", {})            # trigger a re-save (sectioned)
    doc = json.loads((tmp_path / "app_settings.json").read_text())
    assert "global" in doc and doc["apps"]["weather"]["show_aqi"] == "no"
    assert "shared" not in doc


def test_all_returns_an_independent_deep_copy(tmp_path):
    """all()/get() return deep copies, so a caller can't mutate the store's
    internal nested state (installed_apps/playlists/triggers) by reference."""
    ps = PluginSettings(tmp_path)
    ps.all()["installed_apps"].append("HACK")
    ps.get("installed_apps").append("HACK")
    assert "HACK" not in ps.get("installed_apps")


def test_unknown_app_keys_preserved(tmp_path):
    """A plugin_<app>_ key whose app isn't known is kept verbatim, not lost."""
    ps = PluginSettings(tmp_path)                    # no known apps set
    ps.set("plugin_ghost_thing", "v")
    doc = json.loads((tmp_path / "app_settings.json").read_text())
    assert doc["apps"]["_other"]["plugin_ghost_thing"] == "v"
    assert PluginSettings(tmp_path).get("plugin_ghost_thing") == "v"


def test_multivalue_setting_stored_as_array(tmp_path):
    """A multi-value (search_chips) per-app setting persists as a JSON array on
    disk but stays a comma-string in memory (what apps read)."""
    ps = PluginSettings(tmp_path)
    ps.set_known_apps(["stocks"])
    ps.set_list_keys({"plugin_stocks_stocks_list"})
    ps.set("plugin_stocks_stocks_list", "MSFT,GOOG,AAPL")
    doc = json.loads((tmp_path / "app_settings.json").read_text())
    assert doc["apps"]["stocks"]["stocks_list"] == ["MSFT", "GOOG", "AAPL"]  # array on disk
    assert PluginSettings(tmp_path).get("plugin_stocks_stocks_list") == "MSFT,GOOG,AAPL"
