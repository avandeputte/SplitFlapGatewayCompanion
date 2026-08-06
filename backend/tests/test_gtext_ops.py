"""Scalable on-device text (the firmware's ``gtext`` op / 0x21) and the box ``blur`` op /
0x22 — what lets a text app draw its type as ops instead of pushing pixel frames. Pins the
capability parse, the surface helpers, the binary encodings (byte-identical to the firmware
decoder), and the LCD stream-adoption gate that keeps 2 MB raw keyframes off the wire."""

import pytest

from app import canvas, device
from app.canvas import encode_ops_bin
from conftest import CANVAS_DOC, canvas_surface


# --- capability parse --------------------------------------------------------

def _lcd_caps(gtext=True):
    ops = ["clear", "rect", "text", "gtext", "blur", "show"] if gtext else ["clear", "rect", "text"]
    doc = {"product": "LCD Gateway", "features": ["canvas"], "charset": {"common": "A"},
           "canvas": {"width": 1280, "height": 800, "formats": ["rgb888", "rgb565", "qoi"],
                      "opsBin": True, "stream": True, "ops": ops,
                      "text2": {"scalable": True, "aa": True, "maxSize": 512,
                                "charset": "cp1252", "faces": ["sans", "mono", "custom"]}}}
    return device.from_capabilities(doc)


def test_text2_capability_is_parsed():
    caps = _lcd_caps()
    assert caps.canvas_gtext and caps.canvas_text_max == 512
    assert caps.canvas_text_faces == ("sans", "mono", "custom") and caps.canvas_blur
    # gtext needs BOTH the text2 flag and the op in the vocabulary
    assert not _lcd_caps(gtext=False).canvas_gtext
    # an older wall with no text2 at all
    assert not device.from_capabilities(CANVAS_DOC).canvas_gtext


def _lcd_surface():
    return canvas.CanvasSurface("http://gw", _lcd_caps())


def test_surface_exposes_the_gtext_gates():
    s = _lcd_surface()
    assert s.can_gtext and s.text_max == 512 and s.can_blur
    assert "sans" in s.text_faces


# --- the surface helpers emit the right ops ----------------------------------

def test_gtext_builds_a_scalable_text_op():
    s = _lcd_surface()
    s.gtext(40, 96, "Partly Cloudy 72°", color=(214, 226, 246), size=48,
            align="center", outline=(0, 0, 0))
    op = s._ops[-1]
    assert op["op"] == "gtext" and op["size"] == 48 and op["align"] == "center"
    assert op["outline"] == [0, 0, 0] and "face" not in op        # sans is the default, omitted
    s.gtext(0, 0, "x", face="mono", aa=False)
    assert s._ops[-1]["face"] == "mono" and s._ops[-1]["aa"] is False


def test_blur_builds_a_box_blur_op():
    s = _lcd_surface()
    s.blur(4, 4, 300, 200, r=6)
    assert s._ops[-1] == {"op": "blur", "x": 4, "y": 4, "w": 300, "h": 200, "r": 6}


def test_wrap_gtext_keeps_words_and_fit_wrap_sizes_to_the_box():
    s = _lcd_surface()
    lines = s.wrap_gtext("A pleasant surprise is waiting for you", 300, 40, max_lines=3)
    assert 1 <= len(lines) <= 3 and all(s.text_width(ln, 40) <= 300 for ln in lines)
    size, lines = s.fit_wrap_gtext("Honey never spoils, edible honey has been found in tombs",
                                   600, 300, max_lines=4)
    assert size >= 8 and len(lines) <= 4
    # every word survives (fit_wrap shrinks the size until the whole text fits)
    assert sum(len(ln.split()) for ln in lines) == 10
    assert all(s.text_width(ln, size) <= 600 for ln in lines)


def test_fit_gtext_finds_the_largest_fitting_size():
    s = _lcd_surface()
    big = s.fit_gtext("22:35", 1000, 400)
    assert big > s.fit_gtext("22:35", 120, 400) >= 8        # width-bound is smaller
    assert s.fit_gtext("x", 1000, 40) <= 40                 # never taller than max_h
    assert s.text_width("22:35", big) <= 1000               # the winner actually fits
    assert s.fit_gtext("22:35", 100000, 900) <= s.text_max  # capped at the wall's ceiling


def test_time_app_draws_gtext_ops_on_a_capable_wall():
    """The clock converts: on a gtext wall its LCD render is a few gtext ops (crisp native text,
    a few hundred bytes) instead of a pixel frame. LED walls keep the PIL path (no can_gtext)."""
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "..", "apps", "time", "app.py")
    spec = importlib.util.spec_from_file_location("time_gtext", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    captured = {}
    s = _lcd_surface()                                       # 1280x800, can_gtext=True

    def cap_show():
        captured["ops"] = list(s._ops)
        s._ops = []
        return True

    s.show = cap_show
    mod.fetch_canvas({"timezone": "US/Eastern", "want_seconds": "no"}, s)
    ops = captured.get("ops", [])
    kinds = [o["op"] for o in ops]
    assert kinds.count("gtext") >= 3                        # clock + weekday + date (+ AM/PM)
    assert "clear" in kinds and "frame" not in kinds        # ops, not a pixel frame
    assert any(o.get("align") == "center" for o in ops if o["op"] == "gtext")


def test_text_width_matches_the_bundled_face():
    s = _lcd_surface()
    w48 = s.text_width("Weather", size=48)
    assert w48 > s.text_width("Weather", size=24) > 0        # scales with size, real metrics
    assert s.text_width("", size=48) == 0


# --- binary encodings: byte-identical to the firmware decoder ----------------

def test_gtext_binary_matches_the_0x21_layout():
    # 0x21 x:i16 y:i16 size:u16 face:u8 flags:u8 rgb [outline] [shadow] slen:u8 bytes
    assert encode_ops_bin([{"op": "gtext", "x": 2, "y": 0, "s": "Hi", "size": 48,
                            "color": [214, 226, 246]}]) == \
        bytes.fromhex("210002000000300004d6e2f602") + b"Hi"    # flags 0x04 = aa on
    # outline sets bit3 and puts the ring rgb before the length byte
    assert encode_ops_bin([{"op": "gtext", "x": 2, "y": 0, "s": "Hi", "size": 48,
                            "color": [214, 226, 246], "outline": [0, 0, 0]}]) == \
        bytes.fromhex("21000200000030000cd6e2f6000000") + b"\x02Hi"
    # mono face byte = 1, aa off clears bit2
    assert encode_ops_bin([{"op": "gtext", "x": 0, "y": 0, "s": "9", "size": 200,
                            "color": [255, 255, 255], "face": "mono", "aa": False}]) == \
        bytes.fromhex("2100000000") + bytes.fromhex("00c8") + b"\x01\x00\xff\xff\xff\x019"


def test_blur_binary_matches_the_0x22_layout():
    assert encode_ops_bin([{"op": "blur", "x": 10, "y": 20, "w": 100, "h": 40, "r": 6}]) == \
        bytes.fromhex("22000a00140064002806")


def test_gtext_falls_back_to_json_when_binary_cant_carry_it():
    # tracking has no binary field; a >255-byte string overruns slen — both -> JSON batch
    assert encode_ops_bin([{"op": "gtext", "x": 0, "y": 0, "s": "x", "size": 20,
                            "color": [1, 2, 3], "tracking": 2}]) is None
    big = {"op": "gtext", "x": 0, "y": 0, "s": "a" * 300, "size": 20, "color": [1, 2, 3]}
    assert encode_ops_bin([big]) is None


# --- the LCD stream-adoption gate (the 2 MB-keyframe fix) --------------------

def test_frame_and_ops_apps_both_adopt_the_stream_on_a_big_panel(monkeypatch, tmp_path):
    """On the LCD the stream is the only fast path (HTTP measured ~1-2.4 s/request), so BOTH a
    frame-push app and a binary-ops app adopt it — the 2 MB keyframe is suppressed over a stream
    instead (see test_no_periodic_keyframe_over_a_stream). A JSON-only ops batch stays on HTTP."""
    import asyncio

    from app.config import Config
    from app.engine import DisplayController
    from app.state import DisplayState

    async def run():
        cfg = Config(data_dir=tmp_path)
        ctrl = DisplayController(cfg, DisplayState(cfg.module_count()))
        adopted = []
        monkeypatch.setattr(canvas, "has_stream", lambda url: False)
        monkeypatch.setattr(canvas, "stream_begin", lambda url: adopted.append(url) or True)

        def caps(w, h):
            return device.from_capabilities({
                "product": "x", "features": ["canvas"], "charset": {"common": "A"},
                "canvas": {"width": w, "height": h, "formats": ["rgb888"],
                           "stream": True, "ops": ["rect"]}})

        # LCD (big): a frame-push app adopts the stream — HTTP would be seconds per frame there
        monkeypatch.setattr(canvas, "last_push_was_frame", lambda url: True)
        monkeypatch.setattr(canvas, "last_push_was_opsb", lambda url: False)
        await ctrl._maybe_stream("http://gw", caps(1280, 800), "a", 0.1)
        assert adopted == ["http://gw"]
        # a binary-ops app (the aquarium / a converted text app) too
        adopted.clear()
        monkeypatch.setattr(canvas, "last_push_was_frame", lambda url: False)
        monkeypatch.setattr(canvas, "last_push_was_opsb", lambda url: True)
        await ctrl._maybe_stream("http://gw", caps(1280, 800), "a", 0.1)
        assert adopted == ["http://gw"]
        # a JSON-only ops batch (neither frame nor opsb) does NOT adopt — it can't ride the records
        adopted.clear()
        monkeypatch.setattr(canvas, "last_push_was_frame", lambda url: False)
        monkeypatch.setattr(canvas, "last_push_was_opsb", lambda url: False)
        await ctrl._maybe_stream("http://gw", caps(1280, 800), "a", 0.1)
        assert adopted == []

    asyncio.run(run())


def test_no_periodic_keyframe_over_a_stream():
    """The 2 MB-keyframe fix: over an open stream _try_delta never forces a periodic full frame
    (TCP can't drift); on the HTTP path it still does, so a reboot self-heals."""
    from conftest import canvas_surface

    surf = canvas_surface("http://gw2", 1280, 800, ("rgb888", "rgb565", "qoi"), rects=True)
    wall = canvas._wall("http://gw2")

    class _LiveStream:
        alive = True

    same = bytes(1280 * 800 * 3)
    wall.stream = _LiveStream()
    wall.last_frame = (1280, 800, same)
    wall.delta_n = canvas._keyframe_every(1280, 800) - 1        # the next push is the keyframe tick
    assert surf._try_delta(wall, same) is True                 # streaming: identical -> nothing, NOT a full
    wall.stream = None                                         # HTTP path: keyframe forces a full
    wall.delta_n = canvas._keyframe_every(1280, 800) - 1
    assert surf._try_delta(wall, same) is None
    wall.stream = None
    canvas.forget_frame("http://gw2")


def test_take_panel_drops_a_lingering_stream_before_taking_over(monkeypatch, tmp_path):
    """The stuck-stream fix: every app start closes any draw stream a prior app left open, so
    a streaming ops app (the aquarium) switched away can't freeze the wall on its last frame
    (the drawing REST endpoints 409 while a stream is open). The claim itself is the QUIET one
    (stand_down, no set_active(True)): the firmware clears-and-presents on a takeover, which
    blanked the panel early and added one more full-panel present per switch — a visible blink
    on the LCD. The app's first push claims the panel instead; forget_frame makes it a full."""
    import asyncio

    from app.config import Config
    from app.engine import DisplayController
    from app.state import DisplayState

    async def run():
        cfg = Config(data_dir=tmp_path)
        cfg._effective["transport"]["gateway_url"] = "http://gw"
        ctrl = DisplayController(cfg, DisplayState(cfg.module_count()))
        ended, stood, forgot = [], [], []
        monkeypatch.setattr(canvas, "stream_end", lambda url: ended.append(url))
        monkeypatch.setattr(canvas, "stand_down", lambda url: stood.append(url))
        monkeypatch.setattr(canvas, "forget_frame", lambda url: forgot.append(url))
        url = await ctrl._take_panel()
        assert url == "http://gw" and ended == ["http://gw"] and stood == ["http://gw"]
        assert forgot == ["http://gw"]                 # first push after a claim is a FULL frame
        # already in canvas mode (canvas->canvas switch): still drop the prior stream, don't re-claim
        await ctrl._take_panel()
        assert ended == ["http://gw", "http://gw"] and stood == ["http://gw"]

    asyncio.run(run())


def test_fit_wrap_gtext_shrinks_for_an_unbreakable_word():
    """A word wider than the column can't wrap — the fitter must shrink the SIZE until the
    word fits, not accept an overflowing line ("AL-FITR" ran off a 256-wide holiday card)."""
    from app.canvas import _fit_wrap_gtext, _gtext_width
    size, lines = _fit_wrap_gtext("EID AL-FITRALFITR", 90, 200, 3, "sans", 512)
    assert all(_gtext_width(ln, size, "sans") <= 90 for ln in lines)
    assert size >= 8 and sum(len(ln.split()) for ln in lines) == 2
    # normal text is untouched: the accepted size already had every line inside max_w
    s2, l2 = _fit_wrap_gtext("DINNER IS READY", 200, 120, 3, "sans", 512)
    assert all(_gtext_width(ln, s2, "sans") <= 200 for ln in l2)


# --- shared gtext anchoring helpers (retire the per-app drifted fractions) ---

def test_gtext_ink_is_descender_exact():
    """gtext_ink measures the REAL glyph box (PIL getbbox), so a descender/comma line bottoms
    lower than a caps line — no hand-tuned 0.94/1.14 fractions, no 'JQ-only' char-set to miss."""
    s = _lcd_surface()
    _, b_caps = s.gtext_ink("ISS", 100)
    _, b_desc = s.gtext_ink("apply,", 100)
    assert b_caps == 94 and b_desc > b_caps       # comma + y-tail hang below the caps bottom
    # scales linearly with size
    _, b200 = s.gtext_ink("ISS", 200)
    assert 185 <= b200 <= 190


def test_gtext_valign_anchors_by_ink():
    """valign moves y off the ascent-box top onto the string's ink: ink-bottom floor-anchors
    descender-safe (the whole point — metro/planes clipped lowercase tails with JQ-only checks)."""
    s = _lcd_surface()
    _, b = s.gtext_ink("apply,", 100)
    s._ops = []
    s.gtext(10, 800, "apply,", size=100, valign="ink-bottom")
    assert s._ops[-1]["y"] == 800 - b             # ink bottom lands exactly on y=800
    s._ops = []
    t, bb = s.gtext_ink("Xy", 80)
    s.gtext(0, 400, "Xy", size=80, valign="ink-center")
    assert s._ops[-1]["y"] == int(400 - (t + bb) / 2)
    s._ops = []
    s.gtext(0, 0, "Xy", size=80)                   # default valign=top: raw y, unchanged
    assert s._ops[-1]["y"] == 0


def test_ellipsize_trims_to_fit():
    """One shared ellipsize (was copy-pasted under ~6 names with drift): trims with a trailing …
    to fit max_w, whole string when it already fits, bare … when nothing does."""
    s = _lcd_surface()
    long = "Building a four panel LED matrix wall"
    e = s.ellipsize(long, 60, 300)
    assert e.endswith("…") and s.text_width(e, 60) <= 300 and e != long
    assert s.ellipsize("Hi", 60, 300) == "Hi"      # fits: untouched
    assert s.ellipsize("Hello", 60, 0) == "…"      # no room


def test_gtext_block_height_is_descender_aware():
    """Block height = (n-1) steps + the LAST line's real ink bottom, so centring a wrapped block
    never clips its tail. Standardized on the 1.18 step apps had drifted (1.18 vs 1.2)."""
    s = _lcd_surface()
    caps = s.gtext_block_height(["ONE", "TWO"], 50)
    desc = s.gtext_block_height(["ONE", "tail gjpqy"], 50)
    assert desc > caps                             # descender last line reserves more
    assert s.gtext_block_height([], 50) == 0
    assert s.gtext_block_height(["X"], 100) == s.gtext_ink("X", 100)[1]   # 1 line = its ink bottom
