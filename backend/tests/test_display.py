"""Phase 0 of multi-display: a Display owns a wall, and routes resolve through a seam.

Nothing here is a new feature — the companion still drives exactly one gateway, and
every URL still means what it meant. What is pinned is the *structure* that makes a
second gateway possible:

  * a Display owns the geometry, settings store, app loop and HA device that used to
    be module-level globals in main.py;
  * the manager's default is a CHOICE, not "whatever was on screen" — it is what the
    display-less surfaces (bare /api/... routes, /local-api/message, an MCP call with
    no display argument, an existing HACS entry) resolve to;
  * every route goes through display_for(request), so Phase 2 changes one method
    instead of 51 call sites.
"""
import ast
import re
from pathlib import Path

import pytest

from app.display import DEFAULT_ID, Display, DisplayManager, slugify

MAIN_PY = Path(__file__).resolve().parents[1] / "app" / "main.py"
APPS_DIR = Path(__file__).resolve().parents[2] / "apps"


def _display(tmp_path, **kw):
    from app.config import Config
    return Display.build(apps_dir=APPS_DIR, config=Config(tmp_path), **kw)


# ---------------------------------------------------------------------------
# a Display owns a wall
# ---------------------------------------------------------------------------
def test_a_display_owns_everything_a_wall_needs(tmp_path):
    d = _display(tmp_path)
    for attr in ("config", "state", "controller", "settings", "plugins", "scheduler", "ha"):
        assert getattr(d, attr) is not None, attr
    # …and they are wired to each other, not to some other display's objects
    assert d.controller.state is d.state
    assert d.plugins.settings is d.settings
    assert d.plugins.config is d.config


def test_two_displays_share_nothing(tmp_path):
    """The whole point: a second wall's geometry, settings and running app are its own."""
    a = _display(tmp_path / "a", id="kitchen")
    b = _display(tmp_path / "b", id="office")

    a.config.update({"grid": {"rows": 5, "cols": 15}})
    b.config.update({"grid": {"rows": 3, "cols": 22}})
    assert (a.config.module_count(), b.config.module_count()) == (75, 66)

    a.settings.set("zip_code", "02118")
    b.settings.set("zip_code", "10001")
    assert a.settings.get("zip_code") == "02118"

    a.controller.active_app = "weather"
    assert b.controller.active_app is None

    # the tabs a gateway advertises belong to THAT gateway (this was a module global,
    # i.e. last-writer-wins the moment a second gateway registered)
    a.gateway_tabs = [{"id": "modules", "label": "Modules"}]
    assert b.gateway_tabs == []


def test_gateway_tabs_are_no_longer_a_module_global():
    from app import gateway
    assert not hasattr(gateway, "_gateway_tabs")
    assert not hasattr(gateway, "gateway_tabs")


# ---------------------------------------------------------------------------
# the manager, and an EXPLICIT default
# ---------------------------------------------------------------------------
def test_the_default_is_a_choice_not_an_inference(tmp_path):
    m = DisplayManager(APPS_DIR)
    kitchen = m.add(_display(tmp_path / "k", id="kitchen"))
    office = m.add(_display(tmp_path / "o", id="office"))

    assert m.default is kitchen          # first added, until told otherwise
    assert m.default_id == "kitchen"

    m.set_default("office")              # …and it is settable
    assert m.default is office

    with pytest.raises(KeyError):        # never silently invents one
        m.set_default("nope")

    # Running an app on one display must not change which display is the default —
    # "whatever is on screen" is exactly the inference we refuse to make.
    office.controller.active_app = "weather"
    assert m.default is office
    m.set_default("kitchen")
    assert m.default is kitchen


def test_build_default_seeds_the_upgrade_path(tmp_path):
    from app.config import Config
    m = DisplayManager(APPS_DIR)
    d = m.build_default(config=Config(tmp_path))
    assert d.id == DEFAULT_ID and m.default is d and m.ids() == [DEFAULT_ID]


@pytest.mark.parametrize("name,expected", [
    ("Kitchen wall", "kitchen-wall"),
    ("  Office  ", "office"),
    ("5x15 MatrixPortal!", "5x15-matrixportal"),
    ("", "display"),
])
def test_slugify(name, expected):
    assert slugify(name) == expected


# ---------------------------------------------------------------------------
# the resolution seam
# ---------------------------------------------------------------------------
class FakeRequest:
    def __init__(self, **params):
        self.query_params = params


def test_current_resolves_the_default_when_no_display_is_named(tmp_path):
    """Every existing URL — and every Vestaboard client, MCP call and HACS entry —
    sends no display id. They must land on the default, or the upgrade breaks them."""
    m = DisplayManager(APPS_DIR)
    a = m.build_default(config=__import__("app.config", fromlist=["Config"]).Config(tmp_path))
    assert m.current(None) is a
    assert m.current(FakeRequest()) is a


def test_current_honours_an_explicit_display(tmp_path):
    m = DisplayManager(APPS_DIR)
    m.build_default(config=__import__("app.config", fromlist=["Config"]).Config(tmp_path / "d"))
    office = m.add(_display(tmp_path / "o", id="office"))
    assert m.current(FakeRequest(display="office")) is office


def test_an_unknown_display_is_an_error_not_the_wrong_wall(tmp_path):
    """Silently falling back would drive the wrong display — worse than a 404."""
    m = DisplayManager(APPS_DIR)
    m.build_default(config=__import__("app.config", fromlist=["Config"]).Config(tmp_path))
    with pytest.raises(KeyError):
        m.current(FakeRequest(display="nope"))


def test_the_api_404s_on_an_unknown_display():
    from starlette.testclient import TestClient

    from app.main import app
    r = TestClient(app).get("/api/grid?display=nope")
    assert r.status_code == 404
    assert "no such display" in r.json()["detail"]


# ---------------------------------------------------------------------------
# no route may reach around the seam
# ---------------------------------------------------------------------------
def test_no_route_reaches_for_a_module_global():
    """main.py keeps module aliases (config, controller, plugins…) for the lifespan and
    the background loops, and they are the DEFAULT display's objects. A route using one
    would be pinned to that display forever — invisible today, wrong the moment a second
    gateway exists. Routes must go through display_for(request).

    Since the E1 router split the routes live in app/routes/*.py, nested inside each
    module's build(deps) — so this scans those files too (ast.walk, not just the module
    body) and accepts either decorator spelling, @app.* or @router.*. The intent is
    unchanged: only BARE alias names are offenders, so a route that deliberately says
    `displays.default.config` (the process-wide Vestaboard/MCP toggles) still passes."""
    names = ("config", "controller", "plugins", "state", "ha", "plugin_settings")
    pat = re.compile(r"(?<![\w.])(" + "|".join(names) + r")\.")

    def is_route(fn):
        for dec in fn.decorator_list:
            f = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                    and f.value.id in ("app", "router"):
                return True
        return False

    files = [MAIN_PY] + sorted((MAIN_PY.parent / "routes").glob("*.py"))
    assert len(files) > 1, "the router package is gone — did the E1 split get reverted?"
    offenders = []
    for path in files:
        src = path.read_text("utf-8")
        lines = src.split("\n")
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and is_route(n):
                body = "\n".join(lines[n.lineno - 1:n.end_lineno])
                for m in pat.finditer(body):
                    offenders.append(
                        f"{path.name}: {n.name}() uses the global `{m.group(1)}`")
    assert not offenders, "these routes bypass display_for(request):\n" + "\n".join(offenders)


def test_the_module_aliases_are_the_default_displays_objects():
    """They must be the SAME objects, not copies — otherwise the lifespan would drive
    one display while the routes drove another."""
    from app import main
    d = main.displays.default
    assert main.config is d.config
    assert main.controller is d.controller
    assert main.plugins is d.plugins
    assert main.plugin_settings is d.settings
    assert main.ha is d.ha
    assert main.state is d.state


def test_settings_sync_is_per_display():
    """Each gateway holds its own settings blob: with two walls the companion must push
    and pull each one separately, not overwrite one with the other's."""
    src = MAIN_PY.read_text("utf-8")
    for fn in ("setup_settings_sync", "_settings_flush_loop"):
        body = src[src.index(f"async def {fn}("):]
        body = body[:body.index("\nasync def ", 10)]
        assert "d.settings" in body or "settings." in body, fn
        assert "displays.default" in body or "d = d or" in body, fn
    # the blob is pushed to the display's OWN gateway url
    body = src[src.index("async def setup_settings_sync("):]
    body = body[:body.index("\nasync def ", 10)]
    assert "url = d.gateway_url" in body
