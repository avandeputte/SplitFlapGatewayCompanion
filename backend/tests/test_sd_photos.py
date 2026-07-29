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

    hold = app.fetch_matrix({"dwell": "5"}, cv)
    assert hold == 5 and len(cv.frames) == 1
    assert cv.frames[0].size == (128, 64)              # cover: panel-sized
    app.fetch_matrix({"dwell": "5"}, cv)               # advances to the other photo
    app.fetch_matrix({"dwell": "5"}, cv)               # wraps — cache hit, no re-download
    assert [p.rsplit("/", 1)[-1] for p in gets] == ["a.jpg", "b.jpg"]


def test_photo_frame_letterboxes_a_portrait_onto_a_backdrop():
    app = load_app("sd-photos")
    files = {"tall.jpg": _jpeg(w=20, h=60, color=(250, 250, 250))}
    cv = _Frames(_sd_stub(_cv(), files))
    app.fetch_matrix({"fit": "contain"}, cv)
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
    hold = app.fetch_matrix({}, cv)
    assert hold == 30 and len(cv.frames) == 1          # the hint card, on a slow tick


def test_photo_frame_with_an_empty_folder_hints():
    app = load_app("sd-photos")
    cv = _Frames(_sd_stub(_cv(), {}))
    cv.sd_list = lambda path="/": []                   # card mounted, nothing on it
    hold = app.fetch_matrix({}, cv)
    assert hold == 30 and len(cv.frames) == 1


def test_photo_frame_skips_an_undecodable_file():
    app = load_app("sd-photos")
    files = {"broken.jpg": b"not a jpeg", "ok.jpg": _jpeg()}
    cv = _Frames(_sd_stub(_cv(), files))
    hold = app.fetch_matrix({"dwell": "7"}, cv)
    assert hold == 7 and cv.frames[0].size == (128, 64)   # landed on the good one


def test_photo_frame_is_matrix_only_in_the_catalog():
    from conftest import make_runtime
    rt = make_runtime(installed=["sd-photos"])
    card = next(a for a in rt.app_list() if a["id"] == "sd-photos")
    assert card["surfaces"] == ["matrix"]
