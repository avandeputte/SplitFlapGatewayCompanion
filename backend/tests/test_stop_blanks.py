"""Nothing running means nothing on the wall.

A display that has been stopped used to keep showing the last page the app happened to draw.
That is worse than blank: it is a lie that goes stale, and the longer it sits there the more
it looks like the thing is still working. A clock frozen at 11:34 is the clearest example —
it is not obviously OFF, it is obviously WRONG.

Blanking IS homing: flap 0 is the blank flap, so driving every module to a space returns it
home. (The Home button is a separate, physical re-home; this is the ordinary "nothing is on"
state.)

Two ways a display ends up with nothing running, and both must blank:
  * somebody stopped it (the Stop button, /api/apps/stop, the HA button, the MCP tool);
  * a playlist that does not loop simply ran out.
"""
import asyncio
import tempfile
from pathlib import Path

import pytest

APPS = Path(__file__).resolve().parents[2] / "apps"


def _controller():
    from app.config import Config
    from app.engine import DisplayController
    from app.plugin_settings import PluginSettings
    from app.plugins import PluginRuntime
    from app.state import DisplayState

    tmp = Path(tempfile.mkdtemp())
    cfg = Config(tmp)
    cfg.update({"grid": {"rows": 3, "cols": 15}})     # sim: no gateway, but state still moves
    st = PluginSettings(cfg.data_dir)
    st.set("installed_apps", ["time"])
    plugins = PluginRuntime(cfg, st, APPS, cfg.data_dir / "apps")
    plugins.load()
    c = DisplayController(cfg, DisplayState(45))
    c.attach_plugins(plugins)
    return c


def _shown(c):
    return "".join(c.state.current_chars).strip()


def test_stopping_an_app_blanks_the_wall():
    async def go():
        c = _controller()
        await c.start()
        await c.run_app("time")
        await asyncio.sleep(1.2)
        assert _shown(c), "the app never drew anything, so this proves nothing"

        await c.stop_app()
        assert _shown(c) == "", "the wall is still showing the last page"
        assert c.active_app is None
        await c.stop()
    asyncio.run(go())


def test_a_playlist_that_runs_out_blanks_the_wall():
    """It did not get stopped — it simply ended. Nothing is running either way."""
    async def go():
        c = _controller()
        await c.start()
        await c.run_playlist([{"type": "compose", "text": "Goodbye", "duration": 0.2}],
                             False, "once")
        await asyncio.sleep(0.1)
        # a plain wall folds it, which is right — the point is that it IS on the wall
        assert "GOODBYE" in "".join(c.state.current_chars)

        await asyncio.sleep(1.0)                      # let it run out
        assert c.active_playlist is None
        assert _shown(c) == "", "the last page of a finished playlist is still on the wall"
        await c.stop()
    asyncio.run(go())


def test_a_finished_playlist_is_not_resumed_after_a_restart():
    """It ended. Coming back up and putting it back on screen would be a surprise."""
    async def go():
        c = _controller()
        remembered = []
        c.attach_persist(lambda doc: remembered.append(doc))
        await c.start()
        await c.run_playlist([{"type": "compose", "text": "Bye", "duration": 0.2}],
                             False, "once")
        await asyncio.sleep(1.0)
        assert remembered[-1] is None, "a run-out playlist would be resurrected on restart"
        await c.stop()
    asyncio.run(go())


def test_switching_apps_does_not_flash_a_blank_wall():
    """Only an EXPLICIT stop blanks. run_app cancels the loop directly, so starting another
    app must not go through stop_app() and put a blank frame on the wall in between."""
    src = (Path(__file__).resolve().parents[1] / "app" / "engine.py").read_text("utf-8")
    body = src[src.index("    async def run_app("):]
    body = body[:body.index("\n    async def ", 10)]
    assert "stop_app" not in body
    assert "_cancel_task" in body
