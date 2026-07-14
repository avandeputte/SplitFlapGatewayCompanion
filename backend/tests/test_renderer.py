"""
Renderer conformance tests — these guard the *faithful port* of the app-plugin
character set, normalization, and animation orderings. If any of these drift,
dropped-in apps would render differently, so treat failures here as
compatibility regressions, not cosmetic ones.
"""

import asyncio

import pytest

from app import device, renderer
from app.config import Config
from app.engine import DisplayController
from app.state import DisplayState


def test_normalize_basics():
    """normalize() sizes the page and makes its colours explicit. It does NOT fold the case:
    whether to uppercase is a property of the WALL, not of the text, so the engine does it
    last, once, for everyone (renderer.fold). A Matrix Portal shows "hello"."""
    out = renderer.normalize("hello", 15)
    assert out == "hello".ljust(15)
    assert len(out) == 15
    assert renderer.fold(out) == "HELLO".ljust(15)     # …what a split-flap gets


def test_normalize_passes_characters_through():
    # The companion no longer polices characters against a fixed flap set:
    # accents, quotes and currency symbols are all sent verbatim (uppercased);
    # a module blanks anything it lacks.
    fold = lambda t, n: renderer.fold(renderer.normalize(t, n))     # noqa: E731
    assert fold('café "5"€', 20) == 'CAFÉ "5"€'.ljust(20)
    assert fold("groß", 4) == "GROß"                   # ß kept (not "SS")
    assert renderer.normalize("abcdef", 3) == "abc"    # truncates to n


def test_normalize_emoji_color_tiles():
    """A colour tile becomes a COLOUR — its own codepoint, not the letter r or g. It has to
    be: a wall that can show lowercase can show the letter r, so a page must say which it
    meant. On the wire a split-flap still gets the byte `r` (renderer.for_legacy)."""
    out = renderer.normalize("\U0001f7e5\U0001f7e9", 2)
    assert [renderer.PUA_TO_NAME[c] for c in out] == ["red", "green"]
    assert "".join(renderer.for_legacy(c) for c in out) == "rg"


def test_a_frames_lowercase_is_a_colour():
    """An animation draws with lowercase r/o/y/g/b/p/w — the only way it can ask for a colour
    flap. `frame=True` is what says so, and it must survive."""
    out = renderer.normalize("roygbpw", 7, frame=True)
    assert [renderer.PUA_TO_NAME[c] for c in out] == \
        ["red", "orange", "yellow", "green", "blue", "purple", "white"]
    assert renderer.fold(out) == out, "folding must never eat a colour"


def test_words_keep_their_letters():
    """…and the same characters in WORDS are letters. This is the whole distinction, and
    getting it wrong is how "Hello" came out as "Hell<orange>"."""
    out = renderer.normalize("roygbpw", 7)
    assert out == "roygbpw"
    assert not any(renderer.is_color(c) for c in out)


@pytest.mark.parametrize("style", renderer.ORDERED_STYLES)
def test_animation_orders_are_permutations(style):
    rows, cols = 3, 15
    order = renderer.get_animation_order(style, rows, cols)
    assert sorted(order) == list(range(rows * cols)), f"{style} is not a permutation"


def test_ordered_plan_is_one_frame_per_module():
    clean = renderer.normalize("HI", 45)
    plan = renderer.build_send_plan(clean, style="ltr", speed_ms=15, rows=3, cols=15)
    assert len(plan) == 45
    assert all(len(s.frames) == 1 for s in plan)
    # frames come out in left-to-right grid order
    assert [s.frames[0][0] for s in plan] == list(range(45))


def test_slot_plan_has_spin_then_lockin():
    clean = renderer.normalize("HI", 6)
    plan = renderer.build_send_plan(clean, style="slot", speed_ms=80, rows=1, cols=6)
    # phase 1: one step that spins ALL modules, then 6 lock-in steps
    assert len(plan) == 1 + 6
    assert len(plan[0].frames) == 6
    assert plan[0].delay_after == pytest.approx(1.5)


def test_sync_plan_sends_all_modules_together():
    clean = renderer.normalize("ABCDEF", 6)
    plan = renderer.build_send_plan(clean, style="sync", speed_ms=0, rows=1, cols=6)
    # one simultaneous step covering every module (no per-module stagger)
    assert len(plan) == 1
    assert sorted(f[0] for f in plan[0].frames) == list(range(6))


def test_engine_sim_send_updates_state(tmp_path):
    cfg = Config(data_dir=tmp_path)
    st = DisplayState(cfg.module_count())
    ctrl = DisplayController(cfg, st)
    # Production always drives the gateway over REST; we skip start() (which would
    # open the REST transport) and use the controller's default SimTransport double
    # to exercise the per-frame emit + state path with no network.

    asyncio.run(ctrl.send_text("HELLO", style="ltr", speed=0))
    snap = st.snapshot()
    assert "".join(snap["chars"]).startswith("HELLO")
    assert snap["transport"]["type"] == "sim"
