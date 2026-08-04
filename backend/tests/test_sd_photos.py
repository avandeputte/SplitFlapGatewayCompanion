"""The microSD surface (fw 3.10 ``sd`` feature) and the Photo Frame app built on it.

``canvas.sd_list``/``sd_get`` are thin, capability-gated reads of the card; the app
turns them into a slideshow — advance per dwell, LRU-decode, cover/letterbox layout,
and friendly cards when there is no card / no photos.
"""

import io

from conftest import canvas_surface, load_app
from test_canvas_ops35 import OPS35
from app import device


def _jpeg(w=40, h=30, color=(200, 120, 40)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG")
    return buf.getvalue()


def _cv(sd=True):
    return canvas_surface("http://gw", 128, 64, ("rgb888",), (), ops=OPS35, sd=sd)


def test_sd_capability_parses_from_the_feature_token():
    doc = {"features": ["canvas", "sd"], "charset": {"common": "A"},
           "canvas": {"width": 64, "height": 32}}
    assert device.from_capabilities(doc).can_sd
    doc["features"] = ["canvas"]                       # token absent = no card mounted
    assert not device.from_capabilities(doc).can_sd


def test_sd_helpers_are_gated_and_wrap_the_rest_api(monkeypatch):
    import app.gateway as gateway

    calls = []

    class _R:
        status_code = 200
        content = b"bytes!"

        def json(self):
            return [{"name": "a.jpg", "dir": False, "size": 3}]

    monkeypatch.setattr(gateway, "_request",
                        lambda m, u, p, **kw: (calls.append((m, p, kw.get("params"))) or _R()))
    cv = _cv(sd=True)
    assert cv.sd_list("/photos") == [{"name": "a.jpg", "dir": False, "size": 3}]
    assert cv.sd_get("/photos/a.jpg") == b"bytes!"
    assert calls == [("GET", "/api/sd/list", {"path": "/photos"}),
                     ("GET", "/api/sd/get", {"path": "/photos/a.jpg"})]
    # without the card the helpers answer empty without touching the wire
    calls.clear()
    off = _cv(sd=False)
    assert off.sd_list("/") == [] and off.sd_get("/x") is None and not calls


class _Frames:
    """Wrap a surface: capture frame() images instead of sending them."""

    def __init__(self, cv):
        self._cv = cv
        self.frames = []

    def __getattr__(self, name):
        return getattr(self._cv, name)

    def frame(self, img):
        self.frames.append(img)
        return True


def _sd_stub(cv, files):
    cv.sd_list = lambda path="/": ([{"name": n, "dir": False, "size": len(b)}
                                    for n, b in files.items()]
                                   if path.rstrip("/") == "/photos" else [])
    cv.sd_get = lambda path: files.get(path.rsplit("/", 1)[-1])
    return cv


def test_photo_frame_rotates_and_caches(monkeypatch):
    app = load_app("sd-photos")
    files = {"a.jpg": _jpeg(color=(200, 40, 40)), "b.jpg": _jpeg(color=(40, 200, 40))}
    gets = []
    cv = _Frames(_sd_stub(_cv(), files))
    real_get = cv.sd_get
    cv.sd_get = lambda p: (gets.append(p) or real_get(p))

    hold = app.fetch_canvas({"dwell": "5"}, cv)
    assert hold == 5 and len(cv.frames) == 1
    assert cv.frames[0].size == (128, 64)              # cover: panel-sized
    app.fetch_canvas({"dwell": "5"}, cv)               # advances to the other photo
    app.fetch_canvas({"dwell": "5"}, cv)               # wraps — cache hit, no re-download
    assert [p.rsplit("/", 1)[-1] for p in gets] == ["a.jpg", "b.jpg"]


def test_photo_frame_letterboxes_a_portrait_onto_a_backdrop():
    app = load_app("sd-photos")
    files = {"tall.jpg": _jpeg(w=20, h=60, color=(250, 250, 250))}
    cv = _Frames(_sd_stub(_cv(), files))
    app.fetch_canvas({"fit": "contain"}, cv)
    img = cv.frames[0]
    assert img.size == (128, 64)
    # the letterboxed sides carry the dim backdrop, not pure black bars
    left = img.getpixel((2, 32))
    center = img.getpixel((64, 32))
    assert sum(center) > 500                           # the photo itself is bright
    assert 0 < sum(left) < sum(center)                 # the echo is there, but dim


def test_photo_frame_without_a_card_hints_instead_of_failing():
    app = load_app("sd-photos")
    cv = _Frames(_cv(sd=False))
    hold = app.fetch_canvas({}, cv)
    assert hold == 30 and len(cv.frames) == 1          # the hint card, on a slow tick


def test_photo_frame_with_an_empty_folder_hints():
    app = load_app("sd-photos")
    cv = _Frames(_sd_stub(_cv(), {}))
    cv.sd_list = lambda path="/": []                   # card mounted, nothing on it
    hold = app.fetch_canvas({}, cv)
    assert hold == 30 and len(cv.frames) == 1


def test_photo_frame_skips_an_undecodable_file():
    app = load_app("sd-photos")
    files = {"broken.jpg": b"not a jpeg", "ok.jpg": _jpeg()}
    cv = _Frames(_sd_stub(_cv(), files))
    hold = app.fetch_canvas({"dwell": "7"}, cv)
    assert hold == 7 and cv.frames[0].size == (128, 64)   # landed on the good one


def test_photo_frame_is_matrix_only_in_the_catalog():
    from conftest import make_runtime
    rt = make_runtime(installed=["sd-photos"])
    card = next(a for a in rt.app_list() if a["id"] == "sd-photos")
    assert card["surfaces"] == ["matrix"]


# --- fw 3.13: card movies + WAV sound ----------------------------------------

def _cv313(sd=True):
    from app import device
    caps = device.Capabilities(lowercase=True, pictographs=True, named_colors=True,
                               indexed=True, canvas_w=128, canvas_h=64,
                               canvas_formats=("rgb888",), canvas_ops=OPS35,
                               canvas_anim=True, can_sd=sd, fw_version=(3, 13))
    from app.canvas import CanvasSurface
    return CanvasSurface("http://gw", caps)


def test_can_sd_anim_needs_the_card():
    assert _cv313().can_sd_anim
    assert not _cv313(sd=False).can_sd_anim                # no card mounted


def test_play_anim_path_posts_the_card_path(gw_calls):
    r = _cv313().play_anim_path("/movies/clip.mpg")
    assert isinstance(r, dict)
    m, path, body, _ = gw_calls[-1]
    assert (m, path) == ("POST", "/api/canvas/anim/play")
    assert body == {"path": "/movies/clip.mpg"}


def test_play_sound_wav_streams_from_the_card(monkeypatch):
    import time as _t

    from app import canvas as canvas_mod
    import app.gateway as gateway

    calls = []

    class _R:
        status_code = 200

    monkeypatch.setattr(gateway, "_request",
                        lambda m, u, p, **kw: (calls.append((p, kw.get("json"))) or _R()))
    assert canvas_mod.play_sound("http://gw", wav="/sounds/chime.wav", vol=80)
    for _ in range(200):                                    # daemon-thread POST
        if calls:
            break
        _t.sleep(0.005)
    assert calls and calls[-1] == ("/api/sound", {"vol": 80, "wav": "/sounds/chime.wav"})


def test_photo_frame_plays_a_movie_in_the_rotation(gw_calls):
    app = load_app("sd-photos")
    files = {"a.jpg": _jpeg(), "clip.mpg": b"MPGA...."}
    cv = _Frames(_sd_stub(_cv313(), files))
    hold = app.fetch_canvas({"dwell": "8"}, cv)             # a.jpg first (sorted)
    assert hold == 8 and len(cv.frames) == 1
    hold = app.fetch_canvas({"dwell": "8"}, cv)             # then the movie
    assert hold == 8 and len(cv.frames) == 1                # no frame pushed for a movie
    m, path, body, _ = gw_calls[-1]
    assert path == "/api/canvas/anim/play" and body == {"path": "/photos/clip.mpg"}
    app.fetch_canvas({"dwell": "8"}, cv)                    # and back to the photo
    assert len(cv.frames) == 2


def test_movies_stay_out_of_the_rotation_before_313():
    app = load_app("sd-photos")
    files = {"a.jpg": _jpeg(), "clip.mpg": b"MPGA...."}
    cv = _Frames(_sd_stub(_cv(), files))                    # the 3.10 wall from above
    app.fetch_canvas({}, cv)
    st = app.fetch_canvas.__globals__["fetch_canvas"]._state
    assert st["paths"] == ["/photos/a.jpg"]                 # the movie is not listed


def test_an_unplayable_movie_skips_to_the_next_item(monkeypatch, gw_calls):
    app = load_app("sd-photos")
    files = {"bad.mpg": b"x", "z.jpg": _jpeg()}
    cv = _Frames(_sd_stub(_cv313(), files))
    monkeypatch.setattr(cv._cv, "play_anim_path", lambda p: {})   # {} = non-2xx refusal
    hold = app.fetch_canvas({"dwell": "6"}, cv)
    assert hold == 6 and len(cv.frames) == 1                # landed on the photo instead


def test_icon_svg_passes_only_data_image_uris():
    """The drawn-icon field is upload-controlled and lands in an <img src> — only a
    data:image/ URI may survive into the catalog."""
    from conftest import make_runtime
    rt = make_runtime(installed=["canvas-chomper", "canvas-tetris"])
    cards = {a["id"]: a for a in rt.app_list()}
    assert cards["canvas-chomper"]["icon_svg"].startswith("data:image/svg+xml,")
    assert cards["canvas-tetris"]["icon_svg"].startswith("data:image/svg+xml,")
    rt._registry["canvas-chomper"]["icon_svg"] = "javascript:alert(1)"
    evil = next(a for a in rt.app_list() if a["id"] == "canvas-chomper")
    assert evil["icon_svg"] == ""                      # dropped at the boundary
