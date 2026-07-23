"""Multi-rect delta frames (firmware 3.1). A frame-push app sends only the rectangles that changed
since its last frame; a full frame is used for the first frame, a large change, or as a periodic
keyframe so a reboot self-heals. Identical frames send nothing.
"""

import numpy as np

from app import canvas
from conftest import canvas_surface


def _rgb(h, w):
    return np.zeros((h, w, 3), np.uint8)


# -- the diff ---------------------------------------------------------------

def test_identical_frames_produce_no_rects():
    f = _rgb(8, 16).tobytes()
    assert canvas.diff_rects(f, f, 16, 8) == []


def test_a_small_change_is_one_coarse_rect():
    a = _rgb(8, 16)
    b = a.copy()
    b[2:4, 5:9] = 255                              # a 4x2 block changes
    rects = canvas.diff_rects(a.tobytes(), b.tobytes(), 16, 8)
    assert len(rects) == 1
    x, y, w, h, px = rects[0]
    assert (x, y, w, h) == (5, 2, 4, 2)
    assert len(px) == 4 * 2 * 2                    # rgb565 = 2 bytes/pixel


def test_separated_changes_become_separate_bands():
    a = _rgb(10, 16)
    b = a.copy()
    b[1, 2:5] = 255                                # top band
    b[8, 10:13] = 255                              # bottom band, unchanged rows between
    rects = canvas.diff_rects(a.tobytes(), b.tobytes(), 16, 10)
    assert len(rects) == 2
    assert {(r[0], r[1], r[2], r[3]) for r in rects} == {(2, 1, 3, 1), (10, 8, 3, 1)}


def test_a_large_change_falls_back_to_full_frame():
    a = _rgb(8, 16)
    b = np.full((8, 16, 3), 255, np.uint8)         # everything changed
    assert canvas.diff_rects(a.tobytes(), b.tobytes(), 16, 8) is None


def test_rgb565_big_endian_encoding():
    assert canvas._rgb565_be(np.array([[[255, 0, 0]]], np.uint8)) == b"\xf8\x00"   # pure red
    assert canvas._rgb565_be(np.array([[[0, 0, 255]]], np.uint8)) == b"\x00\x1f"   # pure blue


# -- the transport ----------------------------------------------------------

def test_put_rects_body_layout(monkeypatch):
    sent = {}

    def req(m, u, p, *, timeout, **k):
        sent["path"], sent["body"] = p, k["content"]
        return type("R", (), {"status_code": 200, "json": lambda s: {"ok": True, "rects": 1}})()

    monkeypatch.setattr(canvas.gateway, "_request", req)
    px = b"\x01\x02" * 6                           # a 3x2 rect
    assert canvas.put_rects("http://gw", [(4, 3, 3, 2, px)]) is True
    b = sent["body"]
    assert sent["path"] == "/api/canvas/rects"
    assert int.from_bytes(b[0:2], "big") == 1 and b[2] == 2 and b[3] == 0     # count, fmt=rgb565, pad
    assert [int.from_bytes(b[i:i + 2], "big") for i in (4, 6, 8, 10)] == [4, 3, 3, 2]
    assert b[12:] == px


def test_put_rects_413_says_toobig(monkeypatch):
    monkeypatch.setattr(canvas.gateway, "_request",
                        lambda *a, **k: type("R", (), {"status_code": 413})())
    assert canvas.put_rects("http://gw", [(0, 0, 1, 1, b"\x00\x00")]) == "toobig"


# -- the frame path ---------------------------------------------------------

def _surface(monkeypatch, url):
    calls = {"rects": 0, "qoi": 0, "frame": 0}
    monkeypatch.setattr(canvas, "put_rects", lambda u, r, **k: (calls.__setitem__("rects", calls["rects"] + 1), True)[1])
    monkeypatch.setattr(canvas, "put_qoi", lambda u, d, **k: (calls.__setitem__("qoi", calls["qoi"] + 1), True)[1])
    monkeypatch.setattr(canvas, "put_frame", lambda u, d, **k: (calls.__setitem__("frame", calls["frame"] + 1), True)[1])
    canvas.forget_frame(url)
    return canvas_surface(url, 16, 8, ("rgb888", "qoi"), (), rects=True), calls


def test_first_frame_full_then_delta_then_skip(monkeypatch):
    s, calls = _surface(monkeypatch, "http://d1")
    base = _rgb(8, 16).tobytes()
    changed = _rgb(8, 16); changed[1, 1] = 255; changed = changed.tobytes()
    s.frame(base)                                  # no base yet -> full frame (QOI)
    assert (calls["qoi"], calls["rects"]) == (1, 0)
    s.frame(changed)                               # a small change -> delta
    assert (calls["qoi"], calls["rects"]) == (1, 1)
    s.frame(changed)                               # identical -> nothing sent
    assert (calls["qoi"], calls["rects"]) == (1, 1)
    canvas.forget_frame("http://d1")


def test_keyframe_forces_a_full_frame(monkeypatch):
    s, calls = _surface(monkeypatch, "http://d2")
    frames = []
    for i in range(canvas._KEYFRAME_EVERY):
        f = _rgb(8, 16); f[0, i % 16] = i + 1; frames.append(f.tobytes())
    for f in frames:
        s.frame(f)
    # frame 1 (no base) and frame _KEYFRAME_EVERY (n % N == 0) are full; the rest are deltas.
    assert calls["qoi"] == 2
    assert calls["rects"] == canvas._KEYFRAME_EVERY - 2
    canvas.forget_frame("http://d2")


def test_a_wall_without_rects_still_pushes_full_frames(monkeypatch):
    calls = {"rects": 0, "qoi": 0}
    monkeypatch.setattr(canvas, "put_rects", lambda *a, **k: (calls.__setitem__("rects", 1), True)[1])
    monkeypatch.setattr(canvas, "put_qoi", lambda *a, **k: (calls.__setitem__("qoi", calls["qoi"] + 1), True)[1])
    canvas.forget_frame("http://d3")
    s = canvas_surface("http://d3", 16, 8, ("rgb888", "qoi"), ())     # rects not advertised
    s.frame(_rgb(8, 16).tobytes())
    changed = _rgb(8, 16); changed[1, 1] = 9
    s.frame(changed.tobytes())
    assert calls["rects"] == 0 and calls["qoi"] == 2                        # always full, never a delta
    canvas.forget_frame("http://d3")


def test_the_rects_capability_is_parsed():
    from app import device
    doc = {"features": ["cells", "canvas", "events"], "charset": {"uniform": True, "common": "A"},
           "canvas": {"formats": ["rgb888", "qoi"], "width": 128, "height": 64, "rects": True}}
    caps = device.from_capabilities(doc)
    assert caps.canvas_rects is True and caps.events is True
    assert device.from_capabilities({"features": ["canvas"], "charset": {"uniform": True, "common": "A"},
                                     "canvas": {"width": 64, "height": 32}}).canvas_rects is False
