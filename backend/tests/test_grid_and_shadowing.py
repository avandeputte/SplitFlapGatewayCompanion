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
    assert "do_gateway_sync(" in hb, "the heartbeat must re-sync the gateway config"
    assert "sync_from_gateway" in hb, "…and must honour the sync_from_gateway switch"
    # …for the display it heartbeats for: a second gateway must not resize the first.
    assert "do_gateway_sync(display)" in hb


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


# ---------------------------------------------------------------------------
# tall walls: centre the block, and let apps use the room
# ---------------------------------------------------------------------------
def _runtime(rows, cols, tmp_path, **settings):
    from pathlib import Path as P

    from app.config import Config
    from app.plugin_settings import PluginSettings
    from app.plugins import PluginRuntime

    cfg = Config(tmp_path)
    cfg.update({"grid": {"rows": rows, "cols": cols}})
    st = PluginSettings(cfg.data_dir)
    for k, v in settings.items():
        st.set(k, v)
    apps = P(__file__).resolve().parents[2] / "apps"
    rt = PluginRuntime(cfg, st, apps, cfg.data_dir / "apps")
    rt.load()
    return rt


def _rows_of(page, rows, cols):
    return [page[r * cols:(r + 1) * cols] for r in range(rows)]


def test_format_lines_centres_a_short_block_vertically(tmp_path):
    """A 3-line app on a 5-row wall must not sit at the top with two dead rows —
    it is centred. This is our documented divergence from splitflap-os."""
    rt = _runtime(5, 15, tmp_path)
    page = rt.format_lines("ONE", "TWO", "THREE")
    lines = _rows_of(page, 5, 15)
    assert lines[0].strip() == ""            # padding above
    assert [l.strip() for l in lines[1:4]] == ["ONE", "TWO", "THREE"]
    assert lines[4].strip() == ""            # …and below


def test_format_lines_is_unchanged_when_the_app_fills_the_wall(tmp_path):
    """The 3-row wall — what everyone has — must render byte-for-byte as before."""
    rt = _runtime(3, 15, tmp_path)
    assert rt.format_lines("A", "B", "C") == "A".center(15) + "B".center(15) + "C".center(15)


def test_odd_padding_falls_to_the_bottom(tmp_path):
    rt = _runtime(5, 15, tmp_path)
    lines = _rows_of(rt.format_lines("X", "Y"), 5, 15)
    assert lines[0].strip() == "" and lines[1].strip() == "X"
    assert lines[2].strip() == "Y"
    assert lines[3].strip() == "" and lines[4].strip() == ""


@pytest.mark.parametrize("app_id,setting,value", [
    ("world_clock", "plugin_world_clock_world_clock_zones",
     "US/Eastern,US/Pacific,Europe/London,Europe/Paris,Asia/Tokyo"),
])
def test_apps_use_the_extra_rows(tmp_path, app_id, setting, value):
    """world_clock already sliced zones[:get_rows()]; its MANIFEST capped it at 3."""
    rt = _runtime(5, 15, tmp_path, **{setting: value})
    page = rt.get_pages(app_id)[0]
    used = sum(1 for line in _rows_of(page, 5, 15) if line.strip())
    assert used == 5, f"{app_id} filled only {used}/5 rows"


def test_date_gains_the_year_on_a_tall_wall_and_keeps_3_row_layout(tmp_path):
    tall = _rows_of(_runtime(5, 15, tmp_path / "a").get_pages("date")[0], 5, 15)
    assert any(str(__import__("datetime").datetime.now().year) in l for l in tall)
    short = _rows_of(_runtime(3, 15, tmp_path / "b").get_pages("date")[0], 3, 15)
    assert sum(1 for l in short if l.strip()) == 3     # unchanged: time / date / weekday
