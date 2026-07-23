"""Send-pipeline regressions from the July 2026 audit.

Three related bugs, one theme — what the renderer planned was not what the wire got:

  * the engine's batch path flattened the whole send plan into one request and threw
    ``Step.delay_after`` away, so the slot style's 1.5 s spin-hold never happened;
  * the cells path ``dict()``-ed the frames, so a module that appears twice in a plan
    (spin, then lock-in) kept only its LAST frame — the spin phase vanished entirely;
  * the legacy ``/api/rs485/batch`` path re-sent the whole board every page, because
    only the cells path had ``_shown`` diffing.

Plus the smallest one: ``for_text``'s reverse color map tested the wrong variable and
was permanently empty, so MCP ``get_display`` leaked raw U+E000-06 private-use chars
instead of 🟥-style tiles.

All fakes; no gateway is ever contacted.
"""
import asyncio
import json
import time

import pytest

from app import device, renderer
from app.config import Config
from app.engine import DisplayController
from app.state import DisplayState
from app.transport.rest import RestTransport


# ---------------------------------------------------------------------------
# for_text: a color flap reads back as the tile a person would have typed
# ---------------------------------------------------------------------------
def test_for_text_turns_color_flaps_into_tiles():
    page = renderer.normalize("🟥🟧🟨🟩🟦🟪⬜", 7)
    assert all(renderer.is_color(c) for c in page)
    assert "".join(renderer.for_text(c) for c in page) == "🟥🟧🟨🟩🟦🟪⬜"


def test_for_text_never_leaks_private_use_codepoints():
    """What MCP get_display shows: readable text, not U+E000."""
    page = renderer.normalize("AQI 🟩", 5)
    out = "".join(renderer.for_text(c) for c in page)
    assert out == "AQI 🟩"
    assert not any(0xE000 <= ord(c) <= 0xE006 for c in out)


def test_for_text_leaves_letters_alone():
    """`r` the LETTER stays a letter — that ambiguity is the whole reason colors are
    PUA codepoints internally."""
    assert renderer.for_text("r") == "r"
    assert renderer.for_text("A") == "A"


# ---------------------------------------------------------------------------
# cells path: duplicate module ids are ordered repaints, not dict collisions
# ---------------------------------------------------------------------------
class _FakeClient:
    def __init__(self):
        self.posts = []

    async def post(self, path, content=None, headers=None, timeout=None):
        self.posts.append((path, json.loads(content.decode("utf-8"))))

        class R:
            status_code = 200

            def raise_for_status(self):
                pass
        return R()


def _cells_transport():
    t = RestTransport("http://gw")
    t._client = _FakeClient()
    t.caps = device.MATRIX_PORTAL
    return t


def test_cells_path_keeps_the_spin_phase():
    """A module sent twice in one plan (slot: spin char, then the real one) must be
    painted twice, in order — dict(frames) used to keep only the lock-in."""
    async def go():
        t = _cells_transport()
        await t.send_batch([(0, "X"), (1, "Y"), (0, "A"), (1, "B")], 10)
        posts = t._client.posts
        assert len(posts) == 2, "the repaint pass was collapsed away"
        assert posts[0][1]["cells"][:2] == [{"ch": "X"}, {"ch": "Y"}]   # the spin
        assert posts[1][1]["cells"][:2] == [{"ch": "A"}, {"ch": "B"}]   # the lock-in
        assert t._shown == {0: "A", 1: "B"}, "the record must end on the FINAL frames"
    asyncio.run(go())


def test_cells_path_without_duplicates_is_still_one_request():
    async def go():
        t = _cells_transport()
        await t.send_batch([(0, "A"), (1, "B")], 10)
        assert len(t._client.posts) == 1
    asyncio.run(go())


# ---------------------------------------------------------------------------
# engine batch path: the slot style's spin + hold survive
# ---------------------------------------------------------------------------
class FakeBatchTransport:
    type_name = "fake"
    batch_capable = True

    def __init__(self):
        self.batches = []          # (frames, step_ms, monotonic timestamp)

    @property
    def connected(self):
        return True

    @property
    def last_error(self):
        return None

    async def send_frame(self, mid, ch):
        raise AssertionError("batch transport driven per-frame")

    async def send_batch(self, frames, step_ms):
        self.batches.append((list(frames), step_ms, time.monotonic()))

    async def close(self):
        pass


def test_slot_style_sends_spin_then_holds_then_locks_in(tmp_path):
    cfg = Config(data_dir=tmp_path)
    tr = FakeBatchTransport()
    ctrl = DisplayController(cfg, DisplayState(cfg.module_count()))
    ctrl.transport = tr

    asyncio.run(ctrl.send_text("HI", style="slot", speed=0))

    assert len(tr.batches) == 2, "slot must be spin batch + lock-in batch"
    spin, lock = tr.batches
    n = cfg.module_count()
    assert len(spin[0]) == n and len(lock[0]) == n
    # phase 2 is the real page…
    assert [c for _, c in lock[0][:2]] == ["H", "I"]
    # …phase 1 is a different (spinning) character in every cell
    assert all(s != f for (_, s), (_, f) in zip(spin[0], lock[0]))
    # and the 1.5 s hold between them actually elapsed (Step.delay_after survived)
    assert lock[2] - spin[2] >= 1.4, "the spin-hold was discarded on the batch path"


def test_ordered_styles_are_still_exactly_one_request(tmp_path):
    """The wire contract for every ordered style: one request per page, step_ms pacing
    the cascade device-side. The delay_after fix must not change that."""
    cfg = Config(data_dir=tmp_path)
    tr = FakeBatchTransport()
    ctrl = DisplayController(cfg, DisplayState(cfg.module_count()))
    ctrl.transport = tr

    asyncio.run(ctrl.send_text("HELLO", style="ltr", speed=15))
    assert len(tr.batches) == 1
    frames, step, _ = tr.batches[0]
    assert step == 15
    assert len(frames) == cfg.module_count()
    assert frames[0] == (0, "H")


def test_sync_style_is_still_one_request(tmp_path):
    cfg = Config(data_dir=tmp_path)
    tr = FakeBatchTransport()
    ctrl = DisplayController(cfg, DisplayState(cfg.module_count()))
    ctrl.transport = tr
    asyncio.run(ctrl.send_text("ABC", style="sync", speed=15))
    assert len(tr.batches) == 1


# ---------------------------------------------------------------------------
# legacy batch path: unchanged modules are not re-sent
# ---------------------------------------------------------------------------
class FakeHttp:
    def __init__(self, status=200):
        self.status = status
        self.calls = []

    async def post(self, url, json=None, content=None, headers=None, timeout=None):
        self.calls.append({"url": url, "content": content})

        class R:
            status_code = self.status

            def raise_for_status(inner):
                if inner.status_code >= 400:
                    raise RuntimeError(f"status {inner.status_code}")
        return R()


def _legacy_transport(status=200):
    t = RestTransport("http://gw")
    t._client = FakeHttp(status)
    return t                      # default caps: a split-flap, so the legacy wire


def _frames_of(call):
    return json.loads(call["content"].decode("cp1252"))["frames"]


def test_legacy_batch_resends_only_the_changed_modules():
    """A clock moving one digit occupies the gateway for one frame, not the board."""
    async def go():
        t = _legacy_transport()
        await t.send_batch(list(enumerate("12:00")), 0)
        assert len(_frames_of(t._client.calls[0])) == 5     # unknown wall: full page

        await t.send_batch(list(enumerate("12:01")), 0)
        assert _frames_of(t._client.calls[1]) == ["m04-1\n"]
    asyncio.run(go())


def test_legacy_batch_sends_nothing_for_an_unchanged_page():
    async def go():
        t = _legacy_transport()
        await t.send_batch(list(enumerate("HELLO")), 0)
        await t.send_batch(list(enumerate("HELLO")), 0)
        assert len(t._client.calls) == 1
        assert t.connected is True
    asyncio.run(go())


def test_legacy_batch_sends_whole_page_when_the_wall_is_unknown_or_mismatched():
    async def go():
        t = _legacy_transport()
        await t.send_batch(list(enumerate("HI")), 0)        # modules 0-1 known
        # a bigger page: module 2 unknown -> diffing would be a guess; send it whole
        await t.send_batch(list(enumerate("HI!")), 0)
        assert len(_frames_of(t._client.calls[1])) == 3
    asyncio.run(go())


def test_legacy_batch_failure_forces_the_next_page_out_whole():
    """After an error the wall is in an unknown state, so `skip` would be a lie —
    exactly the cells path's rule."""
    async def go():
        t = _legacy_transport()
        await t.send_batch(list(enumerate("HELLO")), 0)

        t._client.status = 500
        with pytest.raises(Exception):
            await t.send_batch(list(enumerate("WORLD")), 0)
        assert t._shown == {}

        t._client.status = 200
        await t.send_batch(list(enumerate("HELLO")), 0)     # unchanged text, but…
        assert len(_frames_of(t._client.calls[-1])) == 5, \
            "the wall was in an unknown state; the page must go out whole"
    asyncio.run(go())


def test_legacy_batch_never_diffs_an_ordered_repaint():
    """Duplicate ids (the slot spin) are intentional motion, not staleness."""
    async def go():
        t = _legacy_transport()
        await t.send_batch([(0, "A"), (1, "B")], 0)
        await t.send_batch([(0, "X"), (0, "A"), (1, "B")], 0)   # 0 spins X then A
        frames = _frames_of(t._client.calls[1])
        assert frames == ["m00-X\n", "m00-A\n", "m01-B\n"]
        assert t._shown == {0: "A", 1: "B"}
    asyncio.run(go())
