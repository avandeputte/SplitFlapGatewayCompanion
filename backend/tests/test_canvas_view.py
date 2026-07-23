"""Dual-view apps: a normal flap app that ALSO ships a rich Matrix-panel rendering (manifest
``"canvas_view": true``, its ``fetch`` branching on the injected ``canvas``). On a wall with a
framebuffer it draws that view when the per-app ``matrix_view`` toggle is on; on a flap wall, or
with the toggle off, it returns its flap pages. These pin the framework wiring — detection, the
toggle, the greyed-on-flap rule, the canvas-vs-pages routing, and the catalog badge flag — the same
contract channels use for their art view.
"""

import tempfile

from app import device
from conftest import make_runtime
from test_canvas import CANVAS_DOC


def _flap(app):
    return make_runtime(tmp_path=tempfile.mkdtemp(), installed=[app], caps=device.SPLIT_FLAP)


def _matrix(app, **kw):
    return make_runtime(tmp_path=tempfile.mkdtemp(), installed=[app],
                        caps=device.from_capabilities(CANVAS_DOC), **kw)


def test_dual_view_apps_are_detected_but_plain_apps_are_not():
    rt = make_runtime(tmp_path=tempfile.mkdtemp(), installed=["countdown", "world_clock", "date"],
                      caps=device.from_capabilities(CANVAS_DOC))
    assert rt.has_canvas_view("countdown") is True
    assert rt.has_canvas_view("world_clock") is True
    assert rt.has_canvas_view("date") is False                 # a plain flap app
    assert rt.has_canvas_view("does-not-exist") is False


def test_a_pure_canvas_app_is_not_flagged_dual_view():
    # canvas-date is surface:canvas — panel only, no flap fallback — so it is NOT dual-view.
    rt = _matrix("canvas-date")
    assert rt.has_canvas_view("canvas-date") is False


def test_the_matrix_view_toggle_is_greyed_on_a_flap_wall_and_live_on_a_matrix():
    flap = _flap("holidays")
    fld = next(f for f in flap.settings_schema("holidays")["fields"] if f["key"].endswith("_matrix_view"))
    assert fld["disabled"] is True and fld.get("note")          # greyed, with the why

    matrix = _matrix("holidays")
    fld2 = next(f for f in matrix.settings_schema("holidays")["fields"] if f["key"].endswith("_matrix_view"))
    assert not fld2.get("disabled")                             # live on a wall with a framebuffer


def test_canvas_view_defaults_on_and_can_be_turned_off():
    rt = _matrix("countdown")
    assert rt.canvas_view_on("countdown") is True               # default: draw on the panel
    rt.settings.set("plugin_countdown_matrix_view", "no")
    assert rt.canvas_view_on("countdown") is False              # explicit opt-out honoured
    assert rt.canvas_view_on("date") is False                   # a plain app never routes to canvas


def test_flap_path_gets_no_canvas_so_a_dual_app_returns_pages():
    # In the pages path a dual-view app must see canvas=None (so it returns flap pages, not draw).
    rt = _matrix("holidays")
    wants = rt._wants.get("holidays", frozenset())
    assert "canvas" in wants                                    # holidays.fetch accepts canvas
    flap_kwargs = rt._helper_kwargs("holidays", wants, {}, rt.settings)
    assert flap_kwargs.get("canvas") is None                   # pages path: suppressed
    canvas_kwargs = rt._helper_kwargs("holidays", wants, {}, rt.settings, for_canvas=True)
    assert canvas_kwargs.get("canvas") is not None             # render path: a real surface


def test_the_catalog_flags_dual_view_for_the_badge():
    rt = _matrix("countdown")
    entry = next(a for a in rt.app_list() if a["id"] == "countdown")
    assert entry["canvas_view"] is True
    date_entry = next(a for a in rt.app_list() if a["id"] == "countdown")
    assert date_entry.get("surface") != "canvas"               # dual-view, not panel-only
