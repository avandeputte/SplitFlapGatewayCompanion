"""
Plugin conformance tests — the guardrail for drop-in compatibility.

Every app under ../../apps must load and satisfy the app-plugin contract:
a valid manifest, and either a functional app.py exposing fetch() or a channel
data.json. If this breaks, a vendored (or dropped-in) app would fail to run.
"""

import io
import json
import zipfile
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


def _upload_runtime(tmp_path):
    """A runtime whose user-apps dir is isolated in tmp (so uploads never touch
    the repo's apps/)."""
    rt = PluginRuntime(Config(data_dir=tmp_path), PluginSettings(tmp_path),
                       APPS_DIR, user_apps_dir=tmp_path / "user_apps")
    rt.load()
    return rt


def _make_app_zip(app_id, manifest, *, app_py=None, data_json=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{app_id}/manifest.json", json.dumps(manifest))
        if app_py is not None:
            z.writestr(f"{app_id}/app.py", app_py)
        if data_json is not None:
            z.writestr(f"{app_id}/data.json", json.dumps(data_json))
    return buf.getvalue()


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
    rt.save_settings("weather", {"plugin_weather_temperature_unit": "celsius"})
    assert rt.settings_schema("weather")["values"]["plugin_weather_temperature_unit"] == "celsius"
    # the app dialog only saves this app's per-app keys — a global (weather_api_key)
    # or another app's key is rejected (globals are owned by the Global editor).
    rt.save_settings("weather", {"weather_api_key": "abc", "plugin_other_x": "nope"})
    assert rt.settings.get("weather_api_key") == ""
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


def test_noncatalog_setting_is_per_app(tmp_path):
    """A non-catalog setting (anim_style) is per-app — each app has its own,
    namespaced key, and it's not flagged shared."""
    rt = _runtime(tmp_path, ["anim_rainbow", "anim_sweep"])
    fields = {f["key"]: f for f in rt.settings_schema("anim_rainbow")["fields"]}
    assert "plugin_anim_rainbow_anim_style" in fields
    assert not fields["plugin_anim_rainbow_anim_style"].get("shared")
    assert "anim_style" not in fields   # not the bare/shared key


def test_app_dialog_infers_undeclared_perapp_setting(tmp_path):
    """A dropped-in app that READS a setting it never declares gets it surfaced as
    an inferred per-app field, typed from the default it's read with. (No vendored
    app relies on this — they declare everything — but drop-in compat needs it.)"""
    import json
    apps = tmp_path / "apps"
    (apps / "widget").mkdir(parents=True)
    (apps / "widget" / "manifest.json").write_text(json.dumps(
        {"name": "Widget", "type": "functional", "settings": []}))
    (apps / "widget" / "app.py").write_text(
        "def fetch(settings, format_lines, get_rows, get_cols):\n"
        "    return [format_lines(str(int(settings.get('widget_count', 3))))]\n")
    ps = PluginSettings(tmp_path)
    ps.set_installed(["widget"])
    rt = PluginRuntime(Config(data_dir=tmp_path), ps, apps)
    rt.load()
    fields = {f["key"]: f for f in rt.settings_schema("widget")["fields"]}
    assert "plugin_widget_widget_count" in fields
    assert fields["plugin_widget_widget_count"]["type"] == "number"


def test_global_persists_from_global_editor(tmp_path):
    """Globals are saved via the Global editor and read by every app."""
    rt = _runtime(tmp_path, ["dashboard", "weather"])
    rt.save_global_settings({"weather_api_key": "K"})
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


def test_disable_colors_is_a_global_toggle(tmp_path):
    """Disabling colors is one global toggle every color app reads (Crypto,
    Stocks, Weather, Metro) — not a per-app setting."""
    from app.catalog import CATALOG_BY_KEY, CATALOG_KEYS
    assert "disable_colors" in CATALOG_KEYS
    assert CATALOG_BY_KEY["disable_colors"]["type"] == "toggle"
    rt = _runtime(tmp_path, ["crypto"])
    rt.save_global_settings({"disable_colors": "yes"})
    ps = rt._plugin_settings("crypto", rt.manifest("crypto"))
    assert ps.get("disable_colors") == "yes"        # reaches the app as a global


def test_language_global_is_windows1252_only(tmp_path):
    """Language is a global picker limited to Windows-1252 (Western) languages."""
    from app.catalog import CATALOG_BY_KEY, CATALOG_KEYS
    assert "language" in CATALOG_KEYS
    vals = {o["value"] for o in CATALOG_BY_KEY["language"]["options"]}
    assert {"en", "fr", "de", "es", "is"} <= vals            # Western/Latin-1 included
    assert not ({"el", "ru", "zh", "ja", "ko", "ar", "he", "th", "tr", "pl"} & vals)  # excluded


def test_sports_follows_is_one_perapp_list(tmp_path):
    """Sports follows are a single per-app list (plugin_sports_follows) — no bare
    per-league keys — and it reaches the app's fetch() dict as ``follows``."""
    rt = _runtime(tmp_path, ["sports"])
    rt.save_settings("sports", {"plugin_sports_follows": "mlb:NYY,ger:*"})
    # stored per-app, nothing bare/shared
    assert rt.settings.get("plugin_sports_follows") == "mlb:NYY,ger:*"
    assert rt.settings.get("sports_ger") is None
    # the app reads it as the bare declared key
    ps = rt._plugin_settings("sports", rt.manifest("sports"))
    assert ps.get("follows") == "mlb:NYY,ger:*"


def test_sports_display_options_are_declared_fields(tmp_path):
    """The Sports dialog exposes the follows list + filter/league/compact options
    as proper per-app controls (not crude auto-inferred fields)."""
    rt = _runtime(tmp_path, ["sports"])
    fields = {f["key"]: f for f in rt.settings_schema("sports")["fields"]}
    assert fields["plugin_sports_follows"]["type"] == "search_chips"
    assert fields["plugin_sports_sports_filter"]["type"] == "select"
    assert fields["plugin_sports_sports_show_league"]["type"] == "toggle"
    assert fields["plugin_sports_sports_compact"]["type"] == "toggle"
    # a declared setting is no longer auto-inferred
    assert "auto-detected" not in (fields["plugin_sports_sports_compact"].get("note") or "")


def test_anim_style_and_speed_are_per_app(tmp_path):
    """Animation style/speed are per-app now (each animation keeps its own), so
    the engine reads them from plugin_<app>_ keys."""
    rt = _runtime(tmp_path, ["anim_rainbow", "anim_sweep"])
    rt.save_settings("anim_rainbow", {"plugin_anim_rainbow_anim_style": "sync",
                                      "plugin_anim_rainbow_anim_speed": "0.2"})
    assert rt.page_timing("anim_rainbow")["style"] == "sync"
    assert abs(rt.loop_delay("anim_rainbow") - 0.2) < 1e-9
    # a different animation is unaffected (its own default)
    assert rt.page_timing("anim_sweep")["style"] == "ltr"


# -- upload validation / hardening -------------------------------------------

def test_upload_rejects_malicious_app(tmp_path):
    """A functional app using disallowed operations is rejected before install,
    with the reason(s) in the message."""
    rt = _upload_runtime(tmp_path)
    z = _make_app_zip("evil", {"name": "Evil", "type": "functional"},
                      app_py="import subprocess\n"
                             "def fetch(s, f, r, c):\n"
                             "    subprocess.run(['id'])\n"
                             "    return ['x']\n")
    with pytest.raises(ValueError) as ei:
        rt.install_zip(z, enable=False)
    msg = str(ei.value)
    assert "subprocess" in msg and "not allowed" in msg
    assert not (tmp_path / "user_apps" / "evil").exists()   # nothing installed


def test_upload_rejects_env_and_eval(tmp_path):
    rt = _upload_runtime(tmp_path)
    z = _make_app_zip("sneaky", {"name": "Sneaky", "type": "functional"},
                      app_py="import os\n"
                             "def fetch(s, f, r, c):\n"
                             "    return [str(eval(s.get('x', '1'))) + str(os.environ)]\n")
    with pytest.raises(ValueError) as ei:
        rt.install_zip(z, enable=False)
    assert "eval" in str(ei.value) or "environ" in str(ei.value)


def test_upload_rejects_app_without_fetch(tmp_path):
    rt = _upload_runtime(tmp_path)
    z = _make_app_zip("nofetch", {"name": "NoFetch", "type": "functional"},
                      app_py="X = 1\n")
    with pytest.raises(ValueError, match="fetch"):
        rt.install_zip(z, enable=False)


def test_upload_rejects_bad_channel(tmp_path):
    rt = _upload_runtime(tmp_path)
    z = _make_app_zip("chan", {"name": "Chan", "type": "channel"}, data_json={"pages": []})
    with pytest.raises(ValueError, match="pages"):
        rt.install_zip(z, enable=False)


def test_upload_scopes_undeclared_settings_into_manifest(tmp_path):
    """A clean app is installed, and a non-global setting it reads but never
    declares is auto-declared as an app-level setting in the manifest."""
    rt = _upload_runtime(tmp_path)
    z = _make_app_zip("widget", {"name": "Widget", "type": "functional", "settings": []},
                      app_py="def fetch(s, f, r, c):\n"
                             "    n = int(s.get('widget_count', '3'))\n"
                             "    return [f('X' * n)]\n")
    rt.install_zip(z, enable=True)
    manifest = json.loads((tmp_path / "user_apps" / "widget" / "manifest.json").read_text())
    keys = {st["key"] for st in manifest.get("settings", [])}
    assert "widget_count" in keys                       # auto-declared app-level
    # and it resolves per-app (never global) when read
    fields = {f["key"] for f in rt.settings_schema("widget")["fields"]}
    assert "plugin_widget_widget_count" in fields


def test_upload_drops_misleading_global_key_flag(tmp_path):
    """A non-catalog setting marked global_key is genuinely app-level; the upload
    rewrites the manifest to drop the misleading flag."""
    rt = _upload_runtime(tmp_path)
    z = _make_app_zip("flagged", {"name": "Flagged", "type": "functional",
                                  "settings": [{"key": "my_opt", "type": "text",
                                                "global_key": True}]},
                      app_py="def fetch(s, f, r, c):\n    return [f(s.get('my_opt', ''))]\n")
    rt.install_zip(z, enable=True)
    manifest = json.loads((tmp_path / "user_apps" / "flagged" / "manifest.json").read_text())
    opt = next(st for st in manifest["settings"] if st["key"] == "my_opt")
    assert "global_key" not in opt


def test_setting_default_applies_without_saving(tmp_path):
    """A per-app setting's declared default (what the dialog shows) is the
    effective value even before the user saves it — it overrides the global
    fallback. Regression: it used to fall back to global_loop_delay until saved."""
    import json
    apps = tmp_path / "apps"
    (apps / "slowapp").mkdir(parents=True)
    (apps / "slowapp" / "manifest.json").write_text(json.dumps({
        "name": "Slow", "type": "functional",
        # a loop_delay SETTING with a high default, but NO top-level loop_delay
        "settings": [{"key": "loop_delay", "type": "number", "default": "20"}]}))
    (apps / "slowapp" / "app.py").write_text(
        "def fetch(s, f, r, c):\n    return [f('X')]\n")
    ps = PluginSettings(tmp_path)
    ps.set_installed(["slowapp"])
    rt = PluginRuntime(Config(data_dir=tmp_path), ps, apps)
    rt.load()
    assert ps.get("global_loop_delay") == 8       # raised global default
    assert rt.loop_delay("slowapp") == 20.0        # the setting default, not the global
    # a saved value still wins
    rt.save_settings("slowapp", {"plugin_slowapp_loop_delay": "3"})
    assert rt.loop_delay("slowapp") == 3.0


def test_refresh_minutes_overrides_fetch_cadence(tmp_path):
    """A per-app refresh_minutes controls how often fetch() is re-run (so a
    random-content app pulls a fresh item on the user's schedule)."""
    rt = _runtime(tmp_path, ["cat-facts"])
    m = rt.manifest("cat-facts")
    assert rt._refresh_secs("cat-facts", m) == m.get("refresh_interval", 300)
    rt.save_settings("cat-facts", {"plugin_cat-facts_refresh_minutes": "5"})
    assert rt._refresh_secs("cat-facts", m) == 300   # 5 minutes


def test_grid_change_clears_page_caches(tmp_path):
    """A grid resize must drop cached pages (they were sized for the old width)
    so apps re-render at the new dimensions."""
    rt = _runtime(tmp_path, ["date"])
    rt.get_pages("date")               # populates the page cache
    assert "date" in rt._caches
    rt.on_grid_changed()
    assert "date" not in rt._caches


def test_i18n_localizes_labels_and_dates():
    from datetime import date
    from app import i18n
    assert i18n.translate("SUNRISE", "fr") == "LEVER"
    assert i18n.translate("SUNRISE", "en") == "SUNRISE"      # English -> unchanged
    assert i18n.translate("SUNRISE", "zz") == "SUNRISE"      # unknown language -> English
    assert i18n.translate("NOT_A_KEY", "fr") == "NOT_A_KEY"  # unknown key -> English
    d = date(2026, 1, 5)                                     # a Monday
    assert i18n.weekday(d, "fr") == "LUNDI" and i18n.weekday(d, "de") == "MONTAG"
    assert i18n.month(d, "es") == "ENERO"


def test_i18n_date_order_and_time_format():
    """Babel gives the locale's own day/month order; AM/PM is English-only."""
    from datetime import datetime
    from app import i18n
    dt = datetime(2026, 7, 9, 15, 5)                         # July 9, 15:05
    assert i18n.date(dt, "en") == "JULY 9"                   # month-first
    assert i18n.date(dt, "fr") == "9 JUILLET"                # day-first
    assert i18n.date(dt, "de") == "9. JULI"
    assert i18n.clock(dt, "en") == "3:05 PM"                 # 12h + meridiem
    assert i18n.clock(dt, "fr") == "15:05"                   # 24h everywhere else
    assert i18n.clock(dt, "de", ampm_space=False) == "15:05"
    assert not i18n.uses_24h("en") and i18n.uses_24h("fr")


def test_i18n_duration_units():
    """Compact D/H/M/S suffixes follow the language (French jour -> J, etc.)."""
    from app import i18n
    assert i18n.duration_unit("D", "en") == "D"             # English unchanged
    assert i18n.duration_unit("D", "fr") == "J"             # jour
    assert i18n.duration_unit("D", "de") == "T"             # Tag
    assert i18n.duration_unit("D", "it") == "G"             # giorno
    assert i18n.duration_unit("H", "nl") == "U"             # uur
    assert i18n.duration_unit("S", "fr") == "S"             # near-universal


def test_i18n_number_and_base_currency():
    """Numbers use the locale's separators; the FX base follows the language."""
    from app import i18n
    assert i18n.number(1234.5, "en") == "1,234.50"          # comma thousands, dot decimal
    assert i18n.number(1234.5, "de") == "1.234,50"          # dot thousands, comma decimal
    assert i18n.number(0.0432, "en", 4, grouping=False) == "0.0432"
    fr = i18n.number(1234567, "fr", 0)                       # French groups with spaces...
    assert fr == "1 234 567" and all(ord(c) < 128 for c in fr)   # ...folded to plain ASCII
    assert i18n.base_currency("en") == "USD" and i18n.base_currency("fr") == "EUR"


def test_manifest_i18n_flag_surfaces_in_listing(tmp_path):
    """The manifest's i18n flag reaches the app listing so the UI can badge cards."""
    rt = _runtime(tmp_path, ["crypto", "time"])
    flags = {a["id"]: a["i18n"] for a in rt.app_list()}
    assert flags["crypto"] is True      # a localized app
    assert flags["time"] is False       # not localized


def test_i18n_injected_by_param_name(tmp_path):
    """An app opts into localization by declaring an i18n parameter; the runtime
    injects a language-bound helper (a classic app gets nothing)."""
    rt = _runtime(tmp_path, ["date", "time"])
    assert rt._wants_i18n.get("date") is True     # date(...) declares i18n
    assert rt._wants_i18n.get("time") is False    # time(...) does not
    rt.settings.set("language", "de")
    from datetime import datetime
    wd = datetime.now().strftime("%A")
    de = {"Monday": "MONTAG", "Tuesday": "DIENSTAG", "Wednesday": "MITTWOCH",
          "Thursday": "DONNERSTAG", "Friday": "FREITAG", "Saturday": "SAMSTAG",
          "Sunday": "SONNTAG"}[wd]
    assert any(de in p for p in rt.get_pages("date"))   # weekday shows in German


def test_weather_helper_injected_only_when_opted_in(tmp_path):
    """An app opts into the shared weather helper by taking a get_weather param;
    a classic 4-arg app is called unchanged."""
    rt = _runtime(tmp_path, ["dashboard", "date"])
    assert rt._wants_weather.get("dashboard") is True   # fetch(..., get_weather=None)
    assert rt._wants_weather.get("date") is False       # classic 4-arg signature


def test_weather_helper_app_credited_under_weather_globals(tmp_path):
    """An app that uses get_weather is credited under the weather globals in the
    Global editor, even though it never reads them directly."""
    rt = _runtime(tmp_path, ["dashboard"])
    fields = {f["key"]: f for f in rt.global_settings_schema()["fields"]}
    assert "Dashboard" in fields["weather_provider"]["note"]
    assert "Dashboard" in fields["weather_api_key"]["note"]


def test_sports_league_dicts_in_sync(tmp_path):
    """The picker's league list (helpers) must match what the app fetches."""
    import re
    h = (Path(__file__).resolve().parents[1] / "app" / "helpers.py").read_text()
    a = (APPS_DIR / "sports" / "app.py").read_text()
    hk = set(re.findall(r'"(\w+)": \{"path": "', h[h.find("SPORTS_LEAGUES"):]))
    ak = set(re.findall(r"'(\w+)':\s*\{'path'", a[a.find("LEAGUES ="):a.find("def ")]))
    assert hk == ak and "ger" in hk   # includes Bundesliga
