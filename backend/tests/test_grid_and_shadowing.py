"""Two bugs found on a 5x15 MatrixPortal, pinned so they cannot come back.

1. `t` shadowing. i18n introduced a global translate function called t(). Three
   callbacks already used `t` as their parameter name, so inside them t("...") called
   a DOM element / a trigger object and threw "t is not a function". The worst was in
   updateActiveUI, which only threw WHEN AN APP WAS RUNNING — so it passed every smoke
   test, aborted init() in the field, and the visible symptom was that the gateway's
   tabs never appeared. A static guard is the only thing that catches this class.

2. Sync-once. do_gateway_sync() ran only at startup. A gateway that was unreachable
   then (still booting) or whose geometry changed later left the companion on its
   default 3x15 forever — 75 modules rendered as 45.
"""
import re
from pathlib import Path

import pytest

APP_JS = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text("utf-8")


# ---------------------------------------------------------------------------
# 1. nothing may shadow t()
# ---------------------------------------------------------------------------
def test_nothing_shadows_the_translate_function():
    """No callback parameter, const, let or var may be named `t`: t() is global, and a
    shadow turns every t("...") inside that scope into a TypeError at runtime."""
    offenders = []
    patterns = [
        (r"\(\s*t\s*\)\s*=>", "arrow param (t) =>"),
        (r"\(\s*t\s*,", "arrow/function param (t, ...)"),
        (r"function\s*\(\s*t\s*[,)]", "function (t)"),
        (r"\b(?:const|let|var)\s+t\s*=", "local variable named t"),
    ]
    for i, line in enumerate(APP_JS.splitlines(), 1):
        if line.lstrip().startswith("//"):
            continue
        for pat, what in patterns:
            if re.search(pat, line):
                offenders.append(f"line {i}: {what} — {line.strip()[:70]}")
    assert not offenders, "these shadow the translate function t():\n" + "\n".join(offenders)


def test_the_running_app_badge_still_translates():
    """The exact line that threw: it must call t() and must not sit in a `t` scope."""
    assert 'badge.textContent = on ? t("▶ RUNNING") : "";' in APP_JS
    assert 'forEach((tile) => {' in APP_JS


# ---------------------------------------------------------------------------
# 2. the grid must be re-read, not read once
# ---------------------------------------------------------------------------
def test_heartbeat_resyncs_the_gateway_config():
    """A one-shot sync at startup is not enough: the gateway may be booting, and its
    geometry can change while we run."""
    main = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text("utf-8")
    hb = main[main.index("async def _companion_heartbeat"):]
    hb = hb[:hb.index("\nasync def ", 10)]
    assert "do_gateway_sync()" in hb, "the heartbeat must re-sync the gateway config"
    assert "sync_from_gateway" in hb, "…and must honour the sync_from_gateway switch"


def test_sync_only_resizes_when_the_geometry_actually_moved():
    """The gateway reports its grid on every poll, so "grid in patch" is always true.
    Resizing on that would re-render every channel app every 30 seconds."""
    main = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text("utf-8")
    body = main[main.index("async def do_gateway_sync"):]
    body = body[:body.index("\nasync def ", 10)]
    assert "if config.grid != before:" in body
    assert "on_grid_changed()" in body


def test_spa_rebuilds_the_board_when_the_module_count_changes():
    """75 modules must not be laid out with a stale 15-column GRID from boot."""
    assert "st.module_count !== GRID.module_count" in APP_JS
    assert "await bootGrid();" in APP_JS


def test_init_survives_a_failing_step():
    """A throw in loadApps() must not take the gateway tabs (and everything after) down."""
    init = APP_JS[APP_JS.index("async function init()"):]
    assert "try { await loadApps(); } catch" in init
    assert init.index("catch") < init.index("setupGatewayTabs();")


# ---------------------------------------------------------------------------
# the sync itself, against a fake gateway document
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rows,cols,modules", [(3, 15, 45), (5, 15, 75), (1, 22, 22)])
def test_build_sync_patch_carries_any_geometry(rows, cols, modules):
    from app.config import Config
    from app.gateway import build_sync_patch

    patch = build_sync_patch({"gridRows": rows, "gridCols": cols})
    assert patch["grid"] == {"rows": rows, "cols": cols}
    c = Config()
    c.update(patch)
    assert (c.grid["rows"], c.grid["cols"], c.module_count()) == (rows, cols, modules)
