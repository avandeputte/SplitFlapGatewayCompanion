"""Firmware 1.19 / 1.25 / 2.1 canvas additions: panel readback, the full draw-op
vocabulary, the overlay ticker, frame transitions, the on-device animation/font
libraries, GIF import and the sprite atlas. These pin that the capability document
is parsed into the right gates, and that each ``CanvasSurface`` helper speaks the
gateway endpoint it should — request shape and response parsing both.
"""

import pytest

from app import canvas, device

# A 2.1 Matrix wall as /api/capabilities describes it.
DOC_2_1 = {
    "product": "Matrix Portal Gateway", "fw": "2.1.0", "api": "3.1.0",
    "features": ["cells", "colors", "canvas", "effects", "ticker"],
    "colors": ["red", "green"], "charset": {"uniform": True, "common": "ABC"},
    "canvas": {"formats": ["rgb888", "rgb565", "qoi"], "width": 128, "height": 32,
               "rect": True, "anim": True, "ticker": True, "readback": True,
               "ops": ["clear", "pixel", "hline", "vline", "line", "rect", "circle", "ellipse",
                       "triangle", "roundrect", "gradient", "polyline", "text", "image",
                       "sprite", "scroll", "show"]},
    "effects": ["plasma", "fire"], "effectParams": ["hue", "density"], "motion": {"kind": "drawn"},
}


# --- capability parsing -----------------------------------------------------

def test_2_1_capabilities_are_parsed():
    c = device.from_capabilities(DOC_2_1)
    assert c.fw_version == (2, 1) and c.canvas_2_1
    assert c.canvas_readback
    assert "sprite" in c.canvas_ops and c.canvas_sprite
    assert len(c.canvas_ops) == 17


def test_a_1_18_wall_has_none_of_the_new_features():
    doc = dict(DOC_2_1, fw="1.18.0")
    doc["canvas"] = {"formats": ["rgb888", "qoi"], "width": 128, "height": 32,
                     "rect": True, "anim": True, "ticker": True}      # no readback / ops
    c = device.from_capabilities(doc)
    assert c.fw_version == (1, 18) and not c.canvas_2_1
    assert not c.canvas_readback and not c.canvas_sprite and c.canvas_ops == ()
    # but the 1.18 gates it DOES have still read true
    assert c.canvas_rect and c.canvas_anim and c.canvas_ticker


def test_firmware_parsing_is_forgiving():
    assert device._parse_fw("2.1.0") == (2, 1)
    assert device._parse_fw("v3") == (3, 0)
    assert device._parse_fw("2.10.4") == (2, 10)
    assert device._parse_fw(None) == (0, 0)
    assert device._parse_fw("nonsense") == (0, 0)


# --- helper + transport -----------------------------------------------------

@pytest.fixture
def gw(monkeypatch):
    """Records (method, path, json, content) in a list; ``.respond(**kw)`` sets what the next
    gateway call returns (json / headers / content / status)."""
    box = {"json": {"ok": True}, "headers": {}, "content": b"", "status": 200}

    class Resp:
        @property
        def status_code(self):
            return box["status"]

        @property
        def headers(self):
            return box["headers"]

        @property
        def content(self):
            return box["content"]

        def json(self):
            return box["json"]

    class Handle(list):
        def respond(self, **kw):
            box.update(kw)

    h = Handle()
    import app.gateway as gateway
    monkeypatch.setattr(gateway, "_request",
                        lambda method, url, path, *, timeout, **kw:
                        (h.append((method, path, kw.get("json"), kw.get("content"))) or Resp()))
    return h


def _surface():
    c = device.from_capabilities(DOC_2_1)
    return canvas.CanvasSurface(
        "http://gw", c.canvas_w, c.canvas_h, c.canvas_formats, c.effects,
        rect=c.canvas_rect, anim=c.canvas_anim, ticker=c.canvas_ticker,
        effect_params=c.effect_params, readback=c.canvas_readback, ops=c.canvas_ops,
        overlay=c.canvas_2_1, transition=c.canvas_2_1, anim_library=c.canvas_2_1,
        gif=c.canvas_2_1, fonts=c.canvas_2_1, sprite=c.canvas_sprite)


def test_surface_flags_follow_the_caps():
    s = _surface()
    assert s.can_ops and s.can_readback and s.can_overlay and s.can_transition
    assert s.can_anim_library and s.can_gif and s.can_fonts and s.can_sprite
    assert s.ops and "gradient" in s.ops


# --- the full draw-op set ---------------------------------------------------

def test_every_new_op_batches_and_presents(gw):
    s = _surface()
    (s.clear("black").line(0, 0, 10, 10, "red").circle(5, 5, 3, "green", fill=True)
      .ellipse(5, 5, 4, 2).triangle(0, 0, 5, 0, 2, 5).roundrect(0, 0, 8, 8, 2)
      .gradient(0, 0, 128, 16, "blue", "black").polyline([(0, 0), (5, 5), (10, 0)])
      .sprite(2, 4, 4).scroll(2, 0).text(1, 1, "HI", size=12, align="center", font="custom"))
    assert gw == []                             # batched, nothing sent
    s.show()
    method, path, body, _ = gw[0]
    assert method == "POST" and path == "/api/canvas/ops"
    assert [o["op"] for o in body] == ["clear", "line", "circle", "ellipse", "triangle",
                                       "roundrect", "gradient", "polyline", "sprite", "scroll",
                                       "text", "show"]
    grad = next(o for o in body if o["op"] == "gradient")
    assert grad["from"] == [0, 80, 255] and grad["dir"] == "v"
    poly = next(o for o in body if o["op"] == "polyline")
    assert poly["points"] == [[0, 0], [5, 5], [10, 0]]
    text = next(o for o in body if o["op"] == "text")
    assert text["align"] == "center" and text["font"] == "custom"


def test_plain_text_op_has_no_align_or_font(gw):
    _surface().text(0, 0, "hi").show()
    text = gw[0][2][0]
    assert "align" not in text and "font" not in text


# --- overlay ticker ---------------------------------------------------------

def test_overlay_ticker_sends_overlay_band_font(gw):
    _surface().ticker("NEWS", (255, 0, 0), 3, overlay=True, band=False, font="orbitron8")
    _, path, body, _ = gw[0]
    assert path == "/api/canvas/ticker"
    assert body["overlay"] is True and body["band"] is False and body["font"] == "orbitron8"


def test_plain_ticker_omits_the_2_1_keys(gw):
    _surface().ticker("HELLO")
    body = gw[0][2]
    assert "overlay" not in body and "band" not in body and "font" not in body


# --- transitions ------------------------------------------------------------

def test_transition_request_and_clamp(gw):
    _surface().transition("wipe", 5000)         # ms clamps to 2000
    _, path, body, _ = gw[0]
    assert path == "/api/canvas/transition" and body == {"type": "wipe", "ms": 2000}


def test_transition_unknown_type_falls_back_to_crossfade(gw):
    canvas.set_transition("http://gw", "sparkle", 400)
    assert gw[0][2]["type"] == "crossfade"


# --- animation library + GIF ------------------------------------------------

def test_anim_library_save_play_delete(gw):
    s = _surface()
    assert s.save_anim("rainbow") is True
    s.play_anim("rainbow")
    assert s.delete_anim("rainbow") is True
    paths = [(m, p, b) for m, p, b, _ in gw]
    assert ("POST", "/api/canvas/anim/save", {"name": "rainbow"}) in paths
    assert ("POST", "/api/canvas/anim/play", {"name": "rainbow"}) in paths
    assert ("POST", "/api/canvas/anim/delete", {"name": "rainbow"}) in paths


def test_anim_list_returns_the_library(gw):
    gw.respond(json=[{"name": "rainbow", "frames": 96, "w": 128, "h": 32, "fps": 15, "loop": True}])
    lib = canvas.anim_list("http://gw")
    assert lib and lib[0]["name"] == "rainbow"


def test_gif_import_returns_frames_and_fps(gw):
    gw.respond(json={"ok": True, "frames": 42, "fps": 12})
    out = _surface().gif(b"GIF89a....")
    assert out == {"ok": True, "frames": 42, "fps": 12}
    method, path, _, content = gw[-1]
    assert method == "PUT" and path == "/api/canvas/gif" and content == b"GIF89a...."


# --- readback ---------------------------------------------------------------

def test_readback_reads_headers_and_body(gw):
    body = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])   # 4×1 rgb888
    gw.respond(headers={"X-Canvas-Width": "4", "X-Canvas-Height": "1", "X-Canvas-Format": "rgb888"},
               content=body)
    img = _surface().readback()
    assert img is not None and img.size == (4, 1)
    assert img.getpixel((0, 0)) == (255, 0, 0) and img.getpixel((2, 0)) == (0, 0, 255)


def test_readback_rgb565_widens_to_888(gw):
    gw.respond(headers={"X-Canvas-Width": "1", "X-Canvas-Height": "1", "X-Canvas-Format": "rgb565"},
               content=bytes([0xF8, 0x00]))     # pure red in rgb565
    f = canvas.get_frame("http://gw", "rgb565")
    assert f == (1, 1, bytes([255, 0, 0]))


def test_preview_readback_requests_rgb565_and_caches(monkeypatch):
    """The effect/ticker preview reads the panel back as rgb565 (a third less over WiFi) and caches
    it ~1s, so the browser can poll the preview freely without a gateway round-trip each time."""
    fmts = []
    monkeypatch.setattr(canvas, "get_frame",
                        lambda url, fmt, **k: (fmts.append(fmt), (1, 1, b"\xff\x00\x00"))[1])
    canvas.forget_frame("http://rb")
    a = canvas.readback_png("http://rb")
    b = canvas.readback_png("http://rb")             # within the TTL -> served from cache
    assert a is not None and a == b and fmts == ["rgb565"]   # rgb565, and the wall was read once
    canvas.forget_frame("http://rb")                 # a mode/effect switch clears the cache
    canvas.readback_png("http://rb")
    assert len(fmts) == 2                             # so the panel is read again
    canvas.forget_frame("http://rb")


# --- sprite atlas -----------------------------------------------------------

def test_atlas_upload_header_is_mpta(gw):
    from PIL import Image
    from app import canvas as _canvas
    _canvas.forget_atlas("http://gw")
    ok = _surface().upload_atlas([Image.new("RGB", (8, 8), (255, 0, 255)) for _ in range(3)])
    assert ok
    # The sheet goes to its NAMED slot in the wall's library; the MPTA body is unchanged.
    method, path, _, content = next(c for c in gw if c[0] == "PUT")
    assert method == "PUT" and path.startswith("/api/canvas/atlas/")
    assert content[:4] == b"MPTA" and content[4] == 1 and content[5] == 3    # ver=1, fmt=rgb888
    assert int.from_bytes(content[6:8], "big") == 8                          # tileW
    assert int.from_bytes(content[8:10], "big") == 8                         # tileH
    assert int.from_bytes(content[10:12], "big") == 3                        # tiles
    assert len(content) == 12 + 3 * 8 * 8 * 3


# --- fonts ------------------------------------------------------------------

def test_font_upload_and_library(gw):
    gw.respond(json={"ok": True, "font": "custom", "w": 8, "h": 13, "ascent": 11})
    out = canvas.put_font("http://gw", b"MPFT....")
    assert out["font"] == "custom"
    assert canvas.font_save("http://gw", "orbitron8") is True
    gw.respond(json=[{"name": "orbitron8", "w": 8, "h": 13, "ascent": 11}])
    assert canvas.font_list("http://gw")[0]["name"] == "orbitron8"


# --- Bug 1: the shown-cell cache self-heals with a periodic full repaint --------

def test_periodic_full_repaint_resends_the_whole_page(monkeypatch):
    """The cells diff skips a module it BELIEVES is already correct — so if the wall drifts from
    the cache (another client, the gateway's compose page, a reboot's re-home) the stale flap
    lingers. Every _REPAINT_SECONDS the whole page is resent regardless, which corrects any drift
    (an unchanged module no-ops on the wall, so it stays invisible otherwise)."""
    import asyncio
    import time

    from app.transport.rest import RestTransport, _REPAINT_SECONDS

    async def run():
        t = RestTransport("http://gw")
        t.caps = device.from_capabilities(DOC_2_1)          # indexed → the /api/display/cells path
        posts = []

        class FakeClient:
            async def post(self, path, **kw):
                import json
                posts.append(json.loads(kw["content"]))

                class R:
                    status_code = 200

                    def raise_for_status(self):
                        pass
                return R()

        t._client = FakeClient()
        clock = {"t": 1000.0}
        monkeypatch.setattr(time, "monotonic", lambda: clock["t"])
        page = [(i, "A") for i in range(5)]
        await t.send_batch(page, 15)                        # full — empty cache; arms the timer
        await t.send_batch(page, 15)                        # same page, no time passed → diffed to nothing
        clock["t"] += _REPAINT_SECONDS + 1                  # cross the repaint window
        await t.send_batch(page, 15)                        # window elapsed → the whole page again

        # A module already showing its value does not re-flip, so the repaint is invisible on the
        # wall — but it re-sends every cell, which is what heals a drifted flap.
        with_cells = [p for p in posts if p.get("cells")]
        assert len(with_cells) == 2, f"expected 2 full repaints, saw {len(with_cells)} of {len(posts)} posts"

    asyncio.run(run())


def test_a_held_page_is_re_emitted_on_the_heartbeat(tmp_path):
    """A non-anim app suppresses re-sending an unchanged page. Without a heartbeat, a flap that
    drifts while that page holds would never be re-asserted until the text finally changed — so
    the loop re-emits the held page every _PAGE_HEARTBEAT_S, giving the transport a whole-page
    repaint to heal through."""
    import asyncio

    from app.config import Config
    from app.engine import DisplayController, _PAGE_HEARTBEAT_S
    from app.state import DisplayState

    async def run():
        ctl = DisplayController(Config(data_dir=tmp_path), DisplayState(45))
        emits = []

        async def fake_emit(clean, *, style, speed, record_as=None):
            emits.append(record_as)
            ctl._app_last_sent = record_as             # as the real _emit_page does on success
            return True

        class Plugins:                                  # the two methods _play_app_pages calls
            def get_pages(self, app_id, ov=None):
                return ["HELLO"]                        # a single, unchanging page

            def page_timing(self, app_id, ov=None):
                return {"is_anim": False, "style": "ltr", "speed": 15, "loop_delay": 0.0}

        ctl.plugins = Plugins()
        ctl._emit_page_from_loop = fake_emit

        await ctl._play_app_pages("x", None, lambda: True)     # new text -> emits
        await ctl._play_app_pages("x", None, lambda: True)     # same text, heartbeat not due -> skip
        ctl._last_page_emit -= _PAGE_HEARTBEAT_S + 1           # pretend the hold has lasted
        await ctl._play_app_pages("x", None, lambda: True)     # heartbeat due -> re-emits the held page
        return emits

    assert asyncio.run(run()) == ["HELLO", "HELLO"]
