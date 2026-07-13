"""Phase 1 of multi-display: the walls get an identity on disk.

    data/displays.json                    which walls exist; which one is the default
    data/displays/<id>/app_settings.json  one settings store per wall, ENTIRELY its own
    data/app_settings.json                the pre-migration file, kept as a backup

EVERY setting is per display, credentials included. That is forced, not chosen: the
gateway is the BACKUP for its wall's settings (main.setup_settings_sync mirrors the whole
doc onto it, and a rebuilt host restores from it), so anything held in a companion-local
file shared across displays would have no gateway to live on and could never be recovered.
The cost — entering an API key once per wall — is the price of every wall's settings being
recoverable from its own box.

What these pin, in rough order of how badly each would hurt to get wrong:

  * the migration DOES NOT DESTROY the old settings file — it is one-way, and someone's
    playlists and triggers are in there;
  * NOTHING a display owns lives outside its own store, so its gateway can hold all of it;
  * an existing install upgrades with ZERO configuration (GATEWAY_URL still seeds it);
  * the add-on's gateway_url option still owns display `default`, so a user fixing a
    typo'd IP on the Configuration tab is not silently ignored;
  * the default display is a stored choice that survives a restart.
"""
import json
from pathlib import Path

import pytest

from app.config import Config
from app.display import DisplayManager
from app.plugin_settings import PluginSettings
from app.registry import DEFAULT_ID, DisplayRegistry, migrate_settings, slugify

APPS_DIR = Path(__file__).resolve().parents[2] / "apps"

# A realistic pre-1.9 settings file: credentials, a location, installed apps, a playlist.
LEGACY = {
    "installed_apps": ["time", "weather", "stocks"],
    "saved_app_playlists": {"morning": {"entries": [{"app": "weather"}], "loop": True}},
    "triggers": [{"app": "weather", "condition": "severe"}],
    "global": {
        "zip_code": "02118",
        "timezone": "US/Eastern",
        "weather_api_key": "SECRET-WEATHER",
        "yt_api_key": "SECRET-YT",
        "weather_provider": "weatherapi",
    },
    "apps": {"weather": {"show_aqi": "yes"}},
}


@pytest.fixture
def legacy_data(tmp_path):
    (tmp_path / "app_settings.json").write_text(json.dumps(LEGACY), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# migration — the part that can lose someone's data
# ---------------------------------------------------------------------------
def test_migration_copies_and_never_moves(legacy_data):
    """One-way migration: a 1.x companion cannot read the new layout, so the old file
    stays exactly where it was. Destroying the only copy of a settings file someone
    spent an evening building is the one unforgivable bug here."""
    assert migrate_settings(legacy_data) is True

    original = legacy_data / "app_settings.json"
    assert original.exists(), "the pre-migration file was destroyed"
    assert json.loads(original.read_text()) == LEGACY, "the backup was modified"

    migrated = legacy_data / "displays" / DEFAULT_ID / "app_settings.json"
    assert json.loads(migrated.read_text()) == LEGACY


def test_migration_is_idempotent(legacy_data):
    """It runs on every boot that finds no registry; running twice must not clobber a
    display's settings with the stale backup."""
    migrate_settings(legacy_data)
    migrated = legacy_data / "displays" / DEFAULT_ID / "app_settings.json"
    migrated.write_text(json.dumps({"installed_apps": ["time"]}), encoding="utf-8")

    migrate_settings(legacy_data)
    assert json.loads(migrated.read_text()) == {"installed_apps": ["time"]}


def test_a_fresh_install_has_nothing_to_migrate(tmp_path):
    assert migrate_settings(tmp_path) is False
    assert not (tmp_path / "displays").exists()


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------
def test_an_existing_install_upgrades_with_zero_configuration(legacy_data):
    """The whole upgrade path: GATEWAY_URL seeds display `default`, the settings come
    with it, and the add-on's one required option keeps meaning what it meant."""
    reg = DisplayRegistry(legacy_data).ensure(gateway_url="http://192.168.1.218")

    assert reg.ids() == [DEFAULT_ID]
    assert reg.get(DEFAULT_ID).gateway_url == "http://192.168.1.218"
    assert reg.default_id == DEFAULT_ID
    assert (legacy_data / "displays" / DEFAULT_ID / "app_settings.json").exists()


def test_the_addon_option_still_owns_the_default_display(legacy_data):
    """A user fixing a typo'd IP on the add-on's Configuration tab must not be silently
    ignored by a registry that thinks it knows better."""
    reg = DisplayRegistry(legacy_data).ensure(gateway_url="http://192.168.1.99")
    assert reg.adopt_env_gateway("http://192.168.1.218") is True
    assert reg.get(DEFAULT_ID).gateway_url == "http://192.168.1.218"

    # …and it persists, rather than being re-applied from memory each boot
    assert DisplayRegistry(legacy_data).ensure().get(DEFAULT_ID).gateway_url \
        == "http://192.168.1.218"
    assert reg.adopt_env_gateway("http://192.168.1.218") is False   # no-op when unchanged
    assert reg.adopt_env_gateway("") is False                       # unset: leave it alone


def test_a_second_display_gets_a_unique_id(tmp_path):
    reg = DisplayRegistry(tmp_path).ensure(gateway_url="http://a")
    k1 = reg.add(name="Kitchen wall", gateway_url="http://b")
    k2 = reg.add(name="Kitchen wall", gateway_url="http://c")
    assert k1.id == "kitchen-wall" and k2.id == "kitchen-wall-2"


def test_the_default_is_a_stored_choice_that_survives_a_restart(tmp_path):
    reg = DisplayRegistry(tmp_path).ensure(gateway_url="http://a")
    office = reg.add(name="Office", gateway_url="http://b")
    reg.set_default(office.id)

    reloaded = DisplayRegistry(tmp_path)
    assert reloaded.load() is True
    assert reloaded.default_id == "office"

    with pytest.raises(KeyError):
        reloaded.set_default("nope")


def test_removing_a_display_keeps_its_settings(tmp_path):
    """Deregistering a wall must not silently delete the playlists and triggers built
    for it — re-adding it with the same id gets them back."""
    reg = DisplayRegistry(tmp_path).ensure(gateway_url="http://a")
    office = reg.add(name="Office", gateway_url="http://b")
    PluginSettings(tmp_path, display_id=office.id).set("zip_code", "10001")
    settings_file = tmp_path / "displays" / "office" / "app_settings.json"
    assert settings_file.exists()

    reg.remove("office")
    assert reg.ids() == [DEFAULT_ID]
    assert settings_file.exists(), "removing a display destroyed its settings"


def test_removing_the_default_hands_the_role_on(tmp_path):
    reg = DisplayRegistry(tmp_path).ensure(gateway_url="http://a")
    reg.add(name="Office", gateway_url="http://b")
    reg.set_default("office")
    reg.remove("office")
    assert reg.default_id == DEFAULT_ID, "the default must never dangle"


def test_the_last_display_cannot_be_removed(tmp_path):
    reg = DisplayRegistry(tmp_path).ensure(gateway_url="http://a")
    with pytest.raises(ValueError):
        reg.remove(DEFAULT_ID)


def test_a_corrupt_registry_does_not_take_the_companion_down(tmp_path):
    """An add-on user has no shell. A registry they cannot parse must not be a brick."""
    (tmp_path / "displays.json").write_text("{ not json", encoding="utf-8")
    reg = DisplayRegistry(tmp_path)
    assert reg.load() is False
    reg.ensure(gateway_url="http://a")          # rebuilds rather than raising
    assert reg.ids() == [DEFAULT_ID]


@pytest.mark.parametrize("name,expected", [
    ("Kitchen wall", "kitchen-wall"), ("  Office  ", "office"),
    ("5x15 MatrixPortal!", "5x15-matrixportal"), ("", "display"),
])
def test_slugify(name, expected):
    assert slugify(name) == expected


# ---------------------------------------------------------------------------
# everything is per display, and everything can go to that display's gateway
# ---------------------------------------------------------------------------
def test_migration_keeps_the_credentials_with_the_display(legacy_data):
    """They are not lifted anywhere. The whole doc goes to display `default`, so the
    whole doc can be mirrored to `default`'s gateway and restored from it."""
    migrate_settings(legacy_data)
    assert not (legacy_data / "globals.json").exists(), "a store with no gateway to live on"

    doc = json.loads((legacy_data / "displays" / DEFAULT_ID / "app_settings.json").read_text())
    assert doc["global"]["weather_api_key"] == "SECRET-WEATHER"
    assert doc["global"]["weather_provider"] == "weatherapi"


def test_two_displays_share_nothing_at_all(tmp_path):
    kitchen = PluginSettings(tmp_path, display_id="kitchen")
    office = PluginSettings(tmp_path, display_id="office")

    # credentials included: each wall's key is its own, and rides to its own gateway
    kitchen.set("weather_api_key", "KEY-A")
    office.set("weather_api_key", "KEY-B")
    assert kitchen.get("weather_api_key") == "KEY-A"
    assert office.get("weather_api_key") == "KEY-B"

    kitchen.set("zip_code", "02118")
    office.set("zip_code", "10001")
    assert kitchen.get("zip_code") == "02118"

    kitchen.set_installed(["time", "weather"])
    office.set_installed(["stocks"])
    assert kitchen.installed_apps == ["time", "weather"]
    assert office.installed_apps == ["stocks"]


def test_a_displays_whole_store_fits_in_its_gateway_blob(tmp_path):
    """The point of the rule: snapshot() is what gets pushed to the gateway, so every
    setting a display has must appear in it. A value that is not in here is a value that
    cannot be backed up or restored."""
    st = PluginSettings(tmp_path, display_id="kitchen")
    st.update({"weather_api_key": "ABC123", "yt_api_key": "YT", "zip_code": "02118",
               "weather_provider": "weatherapi"})
    st.set_installed(["time"])

    doc = st.snapshot()
    assert doc["global"]["weather_api_key"] == "ABC123"
    assert doc["global"]["yt_api_key"] == "YT"
    assert doc["global"]["zip_code"] == "02118"
    assert doc["global"]["weather_provider"] == "weatherapi"
    assert doc["installed_apps"] == ["time"]


def test_a_gateway_restore_brings_a_displays_credentials_back(tmp_path):
    """A rebuilt host restores each wall from its own gateway — the whole store, keys
    and all. This is precisely what a shared globals.json would have broken."""
    st = PluginSettings(tmp_path, display_id="kitchen")
    st.restore_from_doc({"global": {"weather_api_key": "FROM-GATEWAY", "zip_code": "1000"},
                         "apps": {}, "installed_apps": ["time"]})
    assert st.get("weather_api_key") == "FROM-GATEWAY"
    assert st.get("zip_code") == "1000"
    assert st.installed_apps == ["time"]

    # …and a second wall is untouched by it
    assert PluginSettings(tmp_path, display_id="office").get("weather_api_key") == ""


def test_each_display_writes_its_own_file(tmp_path):
    PluginSettings(tmp_path, display_id="kitchen").set("zip_code", "02118")
    PluginSettings(tmp_path, display_id="office").set("zip_code", "10001")
    assert (tmp_path / "displays" / "kitchen" / "app_settings.json").exists()
    assert (tmp_path / "displays" / "office" / "app_settings.json").exists()
    # the single-display path is untouched — it is the backup
    assert not (tmp_path / "app_settings.json").exists()


# ---------------------------------------------------------------------------
# a display's gateway URL is its identity
# ---------------------------------------------------------------------------
def test_a_displays_gateway_url_outranks_the_env(tmp_path, monkeypatch):
    """GATEWAY_URL is a single-display idea. If it outranked the registry, every display
    would be dragged onto the same gateway and the second wall would drive the first."""
    monkeypatch.setenv("GATEWAY_URL", "http://192.168.1.218")
    assert Config(tmp_path).transport["gateway_url"] == "http://192.168.1.218"
    office = Config(tmp_path, gateway_url="http://192.168.1.50")
    assert office.transport["gateway_url"] == "http://192.168.1.50"


def test_no_gateway_url_still_falls_through_to_the_env(tmp_path, monkeypatch):
    """Display `default` records "" when GATEWAY_URL was set later. It must still find it."""
    monkeypatch.setenv("GATEWAY_URL", "http://192.168.1.218")
    assert Config(tmp_path, gateway_url="").transport["gateway_url"] == "http://192.168.1.218"


# ---------------------------------------------------------------------------
# the manager, driven by the registry
# ---------------------------------------------------------------------------
def _manager(tmp_path):
    reg = DisplayRegistry(tmp_path).ensure(gateway_url="http://kitchen")
    reg.add(name="Office", gateway_url="http://office")
    m = DisplayManager(APPS_DIR, registry=reg, data_dir=tmp_path)
    m.load_registry()
    return reg, m


def test_the_manager_builds_one_display_per_record(tmp_path):
    reg, m = _manager(tmp_path)
    assert m.ids() == [DEFAULT_ID, "office"]
    assert m.get("office").gateway_url == "http://office"
    assert m.get(DEFAULT_ID).gateway_url == "http://kitchen"
    # each drives its own wall, with its own settings store
    assert m.get("office").settings.path != m.get(DEFAULT_ID).settings.path


def test_a_disabled_display_is_not_built(tmp_path):
    """It keeps its settings, but costs no sync loop, no MQTT device and no app task."""
    reg, m = _manager(tmp_path)
    reg.update("office", enabled=False)
    m.load_registry()
    assert m.ids() == [DEFAULT_ID]


def test_making_a_display_default_persists_it(tmp_path):
    reg, m = _manager(tmp_path)
    m.set_default("office")
    assert m.default_id == "office"
    assert DisplayRegistry(tmp_path).load() and DisplayRegistry(tmp_path).default_id  # loads
    fresh = DisplayRegistry(tmp_path)
    fresh.load()
    assert fresh.default_id == "office", "the choice did not survive a restart"
