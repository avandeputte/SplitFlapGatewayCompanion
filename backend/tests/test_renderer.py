"""
Renderer conformance tests — these guard the *faithful port* of splitflap-os's
character set, normalization, and animation orderings. If any of these drift,
dropped-in splitflap-os apps would render differently, so treat failures here as
compatibility regressions, not cosmetic ones.
"""

import asyncio

import pytest

from app import renderer
from app.config import Config
from app.engine import DisplayController
from app.state import DisplayState


def test_flap_chars_exact():
    # Byte-for-byte identical to splitflap-os app.py FLAP_CHARS.
    assert renderer.FLAP_CHARS == \
        " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$&()-+=;q:%'.,/?*roygbpw"
    assert len(renderer.FLAP_CHARS) == 64


def test_char_to_index():
    assert renderer.char_to_index(" ") == 0
    assert renderer.char_to_index("A") == 1
    assert renderer.char_to_index("~") == 0  # unknown -> blank


def test_normalize_basics():
    out = renderer.normalize("hello", 15)
    assert out == "HELLO".ljust(15)
    assert len(out) == 15


def test_normalize_truncates_and_quote_alias():
    assert renderer.normalize('say "hi"', 8) == 'SAY qHIq'
    assert renderer.normalize("abcdef", 3) == "ABC"


def test_normalize_currency_alias():
    # A non-$ currency symbol maps onto the physical $ flap.
    assert renderer.normalize("€5", 4, currency="€") == "$5  "


def test_normalize_emoji_color_tiles():
    assert renderer.normalize("\U0001f7e5\U0001f7e9", 2) == "rg"


def test_normalize_raw_keeps_case():
    # Animation pages pass raw=True so lowercase colour codes survive.
    assert renderer.normalize("roygbpw", 7, raw=True) == "roygbpw"


@pytest.mark.parametrize("style", renderer.ORDERED_STYLES)
def test_animation_orders_are_permutations(style):
    rows, cols = 3, 15
    order = renderer.get_animation_order(style, rows, cols)
    assert sorted(order) == list(range(rows * cols)), f"{style} is not a permutation"


def test_ordered_plan_is_one_frame_per_module():
    clean = renderer.normalize("HI", 45)
    plan = renderer.build_send_plan(
        clean, style="ltr", speed_ms=15, rows=3, cols=15, current_indices=[-1] * 45
    )
    assert len(plan) == 45
    assert all(len(s.frames) == 1 for s in plan)
    # frames come out in left-to-right grid order
    assert [s.frames[0][0] for s in plan] == list(range(45))


def test_slot_plan_has_spin_then_lockin():
    clean = renderer.normalize("HI", 6)
    plan = renderer.build_send_plan(
        clean, style="slot", speed_ms=80, rows=1, cols=6, current_indices=[-1] * 6
    )
    # phase 1: one step that spins ALL modules, then 6 lock-in steps
    assert len(plan) == 1 + 6
    assert len(plan[0].frames) == 6
    assert plan[0].delay_after == pytest.approx(1.5)


def test_sync_plan_covers_all_modules():
    clean = renderer.normalize("ABCDEF", 6)
    plan = renderer.build_send_plan(
        clean, style="sync", speed_ms=0, rows=1, cols=6, current_indices=[-1] * 6
    )
    sent = sorted(f[0] for s in plan for f in s.frames)
    assert sent == list(range(6))


def test_engine_sim_send_updates_state(tmp_path):
    cfg = Config(data_dir=tmp_path)
    st = DisplayState(cfg.module_count())
    ctrl = DisplayController(cfg, st)

    async def run():
        await ctrl.start()  # sim transport
        await ctrl.send_text("HELLO", style="ltr", speed=0)
        await ctrl.stop()

    asyncio.run(run())
    snap = st.snapshot()
    assert "".join(snap["chars"]).startswith("HELLO")
    assert snap["transport"]["type"] == "sim"
