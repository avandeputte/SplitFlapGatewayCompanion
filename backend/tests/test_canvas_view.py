"""Surfaces: an app declares which displays it renders on — ``surfaces`` = ["flap"], ["matrix"], or
both. A functional app renders each with a matching entry point (``fetch`` for flaps, ``fetch_matrix``
for a Matrix panel); a channel's matrix surface is drawn generically. These pin the framework wiring:
the surfaces predicates, the single ``matrix`` toggle (default on, grayed off-panel), the render-vs-
pages routing, matrix-only gating, and the catalog field the UI badges off.
"""

import tempfile

from app import device
from conftest import make_runtime
from test_canvas import CANVAS_DOC


def _matrix(*apps):
    return make_runtime(tmp_path=tempfile.mkdtemp(), installed=list(apps),
                        caps=device.from_capabilities(CANVAS_DOC))


def _flap(*apps):
    return make_runtime(tmp_path=tempfile.mkdtemp(), installed=list(apps), caps=device.SPLIT_FLAP)


def test_surfaces_are_read_from_the_manifest():
    rt = _matrix("countdown", "canvas-art-clock", "art-clock")
    assert rt.surfaces("countdown") == ["flap", "matrix"]     # dual
    assert rt.surfaces("canvas-art-clock") == ["matrix"]           # matrix-only
    assert rt.surfaces("art-clock") == ["flap"]               # flap-only
    assert rt.surfaces("does-not-exist") == ["flap"]          # safe default


def test_surface_predicates():
    rt = _matrix("countdown", "world_clock", "canvas-art-clock", "art-clock", "movie-quotes")
    assert rt.is_dual_surface("countdown") and rt.is_dual_surface("world_clock")
    assert rt.is_matrix_only("canvas-art-clock") and not rt.is_dual_surface("canvas-art-clock")
    assert not rt.is_dual_surface("art-clock") and not rt.is_matrix_only("art-clock")
    # A channel is dual-surface too (flap text / generic art on a panel), with no fetch_matrix.
    assert rt.is_dual_surface("movie-quotes")
    assert rt.has_matrix_render("countdown") and rt.has_matrix_render("movie-quotes")


def test_the_matrix_toggle_shows_only_on_a_matrix_wall_and_leads_the_form():
    assert not any(f["key"].endswith("_matrix")               # absent entirely on a flap-only wall
                   for f in _flap("holidays").settings_schema("holidays")["fields"])
    fields = _matrix("holidays").settings_schema("holidays")["fields"]
    assert fields[0]["key"].endswith("_matrix")               # present, and leads the form


def test_matrix_on_defaults_on_and_can_be_turned_off():
    rt = _matrix("countdown", "art-clock")
    assert rt.matrix_on("countdown") is True                  # dual, default: draw on the panel
    rt.settings.set("plugin_countdown_matrix", "no")
    assert rt.matrix_on("countdown") is False                 # explicit opt-out honored
    assert rt.matrix_on("art-clock") is False                 # a flap-only app never routes to matrix


def test_a_matrix_only_app_is_always_on_and_has_no_toggle():
    rt = _matrix("canvas-art-clock")
    assert rt.matrix_on("canvas-art-clock") is True                # nothing to fall back to — always matrix
    keys = [f["key"] for f in rt.settings_schema("canvas-art-clock")["fields"]]
    assert not any(k.endswith("_matrix") for k in keys)       # no toggle: it's not dual-surface


def test_render_matrix_passes_a_canvas_and_flap_helpers_never_do():
    rt = _matrix("countdown")
    # fetch (flap) never receives a canvas among its injected helpers...
    assert "canvas" not in rt._wants.get("countdown", frozenset())
    # ...and render_matrix drives fetch_matrix with a real surface (returns its hold).
    hold = rt.render_matrix("countdown")
    assert isinstance(hold, (int, float)) and hold > 0


def test_the_catalog_exposes_surfaces_for_the_badge():
    rt = _matrix("countdown", "canvas-art-clock", "art-clock")
    by_id = {a["id"]: a for a in rt.app_list()}
    assert by_id["countdown"]["surfaces"] == ["flap", "matrix"]
    assert by_id["canvas-art-clock"]["surfaces"] == ["matrix"]
    assert by_id["art-clock"]["surfaces"] == ["flap"]
