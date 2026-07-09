"""app_settings.json is stored in readable sections (global / shared / apps) and
migrates transparently from the old flat format."""

import json

from app.plugin_settings import PluginSettings


def test_sectioned_layout_and_roundtrip(tmp_path):
    ps = PluginSettings(tmp_path)
    ps.set_known_apps(["weather"])
    ps.update({
        "weather_api_key": "K",           # catalog key      -> global
        "anim_style": "rtl",              # non-catalog bare -> shared
        "plugin_weather_show_aqi": "no",  # per-app          -> apps.weather.show_aqi
    })
    doc = json.loads((tmp_path / "app_settings.json").read_text())
    assert doc["global"]["weather_api_key"] == "K"
    assert doc["shared"]["anim_style"] == "rtl"
    assert doc["apps"]["weather"]["show_aqi"] == "no"
    # reload -> flat values preserved
    ps2 = PluginSettings(tmp_path)
    assert ps2.get("weather_api_key") == "K"
    assert ps2.get("plugin_weather_show_aqi") == "no"


def test_migrates_legacy_flat_file(tmp_path):
    (tmp_path / "app_settings.json").write_text(json.dumps({
        "weather_api_key": "K",
        "plugin_weather_show_aqi": "no",
        "installed_apps": ["weather"],
    }))
    ps = PluginSettings(tmp_path)                    # reads legacy flat
    assert ps.get("weather_api_key") == "K"
    assert ps.get("plugin_weather_show_aqi") == "no"
    ps.set_known_apps(["weather"])
    ps.set("saved_app_playlists", {})                # trigger a re-save (sectioned)
    doc = json.loads((tmp_path / "app_settings.json").read_text())
    assert "global" in doc and doc["apps"]["weather"]["show_aqi"] == "no"


def test_unknown_app_keys_preserved(tmp_path):
    """A plugin_<app>_ key whose app isn't known is kept verbatim, not lost."""
    ps = PluginSettings(tmp_path)                    # no known apps set
    ps.set("plugin_ghost_thing", "v")
    doc = json.loads((tmp_path / "app_settings.json").read_text())
    assert doc["apps"]["_other"]["plugin_ghost_thing"] == "v"
    assert PluginSettings(tmp_path).get("plugin_ghost_thing") == "v"


def test_multivalue_setting_stored_as_array(tmp_path):
    """A multi-value (search_chips) setting persists as a JSON array on disk but
    stays a comma-string in memory (what apps read)."""
    ps = PluginSettings(tmp_path)
    ps.set_list_keys({"stocks_list"})
    ps.set("stocks_list", "MSFT,GOOG,AAPL")
    doc = json.loads((tmp_path / "app_settings.json").read_text())
    assert doc["shared"]["stocks_list"] == ["MSFT", "GOOG", "AAPL"]   # array on disk
    assert PluginSettings(tmp_path).get("stocks_list") == "MSFT,GOOG,AAPL"  # comma in memory
