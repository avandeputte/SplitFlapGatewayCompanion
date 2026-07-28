"""Shared test plumbing (backend audit 2026-07, sections B and E4).

Three helpers that a dozen files used to hand-roll, plus one guard:

  * ``load_app(name)``     — import ``apps/<name>/app.py`` by path, a FRESH module
                             per call (per-test isolation; ``app.py`` would otherwise
                             collide with the backend's ``app`` package).
  * ``make_runtime(...)``  — a PluginRuntime on an isolated tmp dir, via the REAL
                             constructor. Never build one with ``__new__``: it breaks
                             silently every time ``__init__`` gains state (it did —
                             ``_gen``, ``_wants``, ``_trigger_wants``).
  * ``stub_http``          — a fixture routing the weather helper's sync
                             ``httpx.Client`` at a handler, clearing its doc cache.
  * ``json_response(...)`` — the one canned-HTTP-response factory (json/status/
                             raise_for_status) behind every stubbed request seam.
  * ``gw_calls``           — a fixture recording gateway._request traffic as
                             (method, path, json, content) tuples.
  * ``capture_surface(...)``— the PIL canvas fake frame-push app tests draw on.
  * ``_lines(...)``        — split a rendered page string back into wall rows.
  * an AUTOUSE socket guard: no test may open a TCP connection off the loopback.
    A layout test that needs the internet is a layout test that fails on a train —
    and worse, one that passes while quietly hammering someone's free API.

pytest's default (prepend) import mode registers this file as the ``conftest``
module, so plain-helper imports (``from conftest import load_app``) share this
exact module instance with the fixtures below.
"""
import importlib.util
import socket
from pathlib import Path

import pytest

APPS_DIR = Path(__file__).resolve().parents[2] / "apps"

# ---------------------------------------------------------------------------
# load_app — the by-path app loader
# ---------------------------------------------------------------------------


def load_app(name: str):
    """Import ``apps/<name>/app.py`` by path and return the module — fresh per call,
    and never registered in sys.modules, so tests cannot leak state into each other."""
    spec = importlib.util.spec_from_file_location(f"_testapp_{name}", APPS_DIR / name / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# make_runtime — the PluginRuntime builder
# ---------------------------------------------------------------------------
# Plain functions (usable at module level and inside non-fixture helpers) still
# need somewhere temporary to live; stash the session's tmp factory so
# make_runtime can mint a fresh, pytest-cleaned dir when the caller has no
# tmp_path of its own. This replaces the uncleaned tempfile.mkdtemp() copies.
_tmp_factory = None


@pytest.fixture(scope="session", autouse=True)
def _stash_tmp_factory(tmp_path_factory):
    global _tmp_factory
    _tmp_factory = tmp_path_factory
    yield
    _tmp_factory = None


def fresh_dir(label: str = "rt") -> Path:
    """A fresh temporary directory, cleaned up by pytest (unlike tempfile.mkdtemp)."""
    return _tmp_factory.mktemp(label, numbered=True)


# The 64-flap reels we publish, one per locale. Uppercase only: this is what a split-flap is
# actually sent. Keep in step with the wiki's Flaps & Character Sets page.
_BASE = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789€!?.,'-"
REELS = {
    "fr": _BASE + "ÀÂÇÈÉÊËÎÏÔÙÛÜ",
    "de": _BASE + "ÄÖÜß" + ":%&()@#+=",
    "es": _BASE + "ÁÉÍÑÓÚÜ" + ":%&()@",
    "it": _BASE + "ÀÈÉÌÒÙ" + ":%&()@#",
    "pt": _BASE + "ÀÁÂÃÇÉÊÍÓÔÕÚ" + ":",
    "nl": _BASE + "ÁÉËÍÓÚÜ" + ":%&()@",
    "sv": _BASE + "ÅÄÖ" + ":%&()@#+=/",
    "da": _BASE + "ÆØÅ" + ":%&()@#+=/",
    "no": _BASE + "ÆØÅ" + ":%&()@#+=/",
}

# The canonical Matrix-wall capability document tests build runtimes from.
CANVAS_DOC = {
    "features": ["cells", "colors", "lowercase", "pictographs", "canvas", "effects"],
    "colors": ["red", "green"], "charset": {"uniform": True, "common": "ABC"},
    "canvas": {"formats": ["rgb888", "rgb565"], "width": 128, "height": 32},
    "effects": ["plasma", "fire", "matrix"], "motion": {"kind": "drawn"},
}


def make_runtime(tmp_path=None, installed=None, *, rows=None, cols=None,
                 apps_dir=APPS_DIR, user_apps_dir=None, caps=None,
                 settings=None, load=True):
    """A PluginRuntime with `installed` loaded from `apps_dir`, isolated in a tmp dir.

    tmp_path        data dir (settings + uploads). None mints a fresh pytest tmp dir.
    installed       installed-app ids; None keeps PluginSettings' own default set
                    (a fresh install is NOT empty — pass [] to mean empty).
    rows / cols     grid geometry override (defaults stay 3x15).
    apps_dir        the built-in apps directory (default: the repo's apps/).
    user_apps_dir   uploaded-apps dir; defaults INSIDE tmp_path so an upload can
                    never land in the repo's apps/.
    caps            a device capability object to attach (attach_caps) before load.
    settings        extra PluginSettings key/values (use for keys with dashes).
    load            set False for a runtime that only discover()s.
    """
    from app.config import Config
    from app.plugin_settings import PluginSettings
    from app.plugins import PluginRuntime

    tmp_path = Path(tmp_path) if tmp_path is not None else fresh_dir("runtime")
    cfg = Config(data_dir=tmp_path)
    if rows is not None or cols is not None:
        grid = {}
        if rows is not None:
            grid["rows"] = rows
        if cols is not None:
            grid["cols"] = cols
        cfg.update({"grid": grid})
    st = PluginSettings(tmp_path)
    if installed is not None:
        st.set_installed(list(installed))
    for k, v in (settings or {}).items():
        st.set(k, v)
    rt = PluginRuntime(cfg, st, apps_dir, user_apps_dir or tmp_path / "user_apps")
    if caps is not None:
        rt.attach_caps(lambda: caps)
    if load:
        rt.load()
    return rt


# ---------------------------------------------------------------------------
# stub_http — the weather helper's httpx.Client, faked
# ---------------------------------------------------------------------------
class Resp:
    """A canned JSON response — the ``.json()`` half of requests/httpx."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _JsonResp(Resp):
    """Resp plus the status half: ``.status_code`` and a loosely-httpx
    ``raise_for_status()`` — together the union of what the tests' local
    response stubs actually exercised."""

    def __init__(self, payload, status=200):
        super().__init__(payload)
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx2 as httpx
            raise httpx.HTTPStatusError(f"status {self.status_code}",
                                        request=None, response=self)


def json_response(payload, status=200):
    """A canned response for any stubbed HTTP seam (requests.get, httpx.get,
    gateway._request): ``.json()`` -> payload, ``.status_code``, and
    ``raise_for_status()`` raising from 400 up. One factory instead of the
    dozen local ``_Resp`` classes it replaced."""
    return _JsonResp(payload, status)


class FakeClient:
    """Stands in for ``httpx.Client`` inside the shared weather helper: a context
    manager whose ``get`` hands every request to the test's handler."""

    def __init__(self, handler):
        self._handler = handler

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kw):
        return self._handler(url, **kw)


@pytest.fixture
def stub_http(monkeypatch):
    """Call ``stub_http(handler)`` to route the sync weather helper's httpx.Client
    at ``handler(url, **kw) -> Resp``. Clears the helper's doc cache first — tests
    share (provider, lat, lon), so a stale doc would cross between them."""
    def _install(handler):
        from app import weather
        weather._cache.clear()
        monkeypatch.setattr(weather.httpx, "Client", lambda **kw: FakeClient(handler))
    return _install


# ---------------------------------------------------------------------------
# the socket guard — audit section B: the suite must run on a desert island
# ---------------------------------------------------------------------------
def _is_loopback(host) -> bool:
    h = str(host)
    return (h in ("localhost", "ip6-localhost", "::1", "0.0.0.0", "::", "")
            or h.startswith("127."))


@pytest.fixture(autouse=True)
def _no_outbound_network(request, monkeypatch):
    """Fail any test that opens a real TCP connection off the loopback.

    Allowed through:
      * loopback — several tests run a local HTTP server on 127.0.0.1;
      * UDP connect — it sends NO packet (gateway.detect_local_ip "connects"
        a datagram socket to 8.8.8.8 purely to read the chosen local address);
      * non-INET families (AF_UNIX addresses are strings, not tuples).
    """
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _check(sock, address):
        if not isinstance(address, tuple) or not address:
            return
        if sock.type == socket.SOCK_DGRAM:
            return
        host = address[0]
        if _is_loopback(host):
            return
        raise RuntimeError(
            f"{request.node.nodeid} tried to open a TCP connection to "
            f"{host}:{address[1] if len(address) > 1 else '?'} — tests must never touch "
            "the network. Stub the HTTP layer (requests.get, or conftest.stub_http for "
            "the weather helper).")

    def connect(self, address):
        _check(self, address)
        return real_connect(self, address)

    def connect_ex(self, address):
        _check(self, address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


# --- canvas test helper ------------------------------------------------------
# Production builds a CanvasSurface from the wall's device.Capabilities (the one
# construction path). Tests want ad-hoc capability combos without spelling a full
# Capabilities out — this maps the short flag names to the caps fields.
_CANVAS_FLAG_TO_FIELD = {
    "rect": "canvas_rect", "rects": "canvas_rects", "anim": "canvas_anim",
    "ticker": "canvas_ticker", "readback": "canvas_readback", "stream": "canvas_stream",
    "ops": "canvas_ops", "ops_bin": "canvas_ops_bin", "effect_params": "effect_params",
}


def canvas_surface(url, w, h, formats=(), effects=(), *, sprite=False, two_one=False, **flags):
    """A CanvasSurface for tests: ``canvas_surface(url, w, h, formats, effects, rects=True, ...)``.
    ``sprite=True`` adds the sprite op; ``two_one=True`` sets firmware 2.1 (overlay/transition/
    anim-library/GIF/fonts family)."""
    from app import canvas as canvas_mod
    from app import device
    fields = {_CANVAS_FLAG_TO_FIELD[k]: v for k, v in flags.items()}
    if sprite:
        fields["canvas_ops"] = tuple(set(fields.get("canvas_ops", ())) | {"sprite"})
    if two_one:
        fields["fw_version"] = (2, 1)
    caps = device.Capabilities(lowercase=True, pictographs=True, named_colors=True, indexed=True,
                               canvas_w=w, canvas_h=h, canvas_formats=tuple(formats),
                               effects=tuple(effects), **fields)
    return canvas_mod.CanvasSurface(url, caps)


@pytest.fixture
def gw_calls(monkeypatch):
    """Route the pooled gateway seam (``app.gateway._request``) into a list of
    ``(method, path, json, content)`` tuples, every call answered 200/{"active": True}
    — the wire-shape recorder behind the canvas transport tests."""
    calls = []
    import app.gateway as gateway
    monkeypatch.setattr(gateway, "_request",
                        lambda method, url, path, *, timeout, **kw:
                        (calls.append((method, path, kw.get("json"), kw.get("content")))
                         or json_response({"active": True})))
    return calls


def _borrow_toolkit(cls):
    """Give a Surface fake the real PIL text toolkit (descriptor copy), so apps that
    call canvas.fit_font/wrap/message/text_card/mix/dim run the production code."""
    from app.canvas import CanvasSurface as _CS
    for name in ("MIN_READABLE", "fit_font", "ink", "wrap", "wrap_fit", "text_top",
                 "message", "card_pages", "_card_header", "text_card", "mix", "dim"):
        setattr(cls, name, _CS.__dict__[name])
    return cls


@_borrow_toolkit
class _CaptureSurface:
    """A canvas fake that renders for real — blank/vgrad/font behave like
    CanvasSurface's, so an app's layout maths run — but ``frame()`` captures the
    image in ``.img`` instead of pushing it to a wall."""

    def __init__(self, w, h):
        self.width, self.height, self.img = w, h, None

    def blank(self, color=(0, 0, 0)):
        from PIL import Image
        return Image.new("RGB", (self.width, self.height), tuple(color))

    def vgrad(self, top, bottom):
        from PIL import Image
        img = Image.new("RGB", (self.width, self.height))
        for y in range(self.height):
            f = y / max(1, self.height - 1)
            row = tuple(int(top[i] + (bottom[i] - top[i]) * f) for i in range(3))
            img.paste(row, (0, y, self.width, y + 1))
        return img

    def font(self, size, name="DejaVuSans-Bold.ttf"):
        import os
        from PIL import ImageFont
        from app.canvas import _FONT_DIR
        return ImageFont.truetype(os.path.join(_FONT_DIR, name), max(5, int(size)))

    def frame(self, image):
        self.img = image
        return True


def capture_surface(w=256, h=64):
    """The lightweight PIL canvas fake the frame-push app tests share (the default
    size is the tests' common 256x64 panel)."""
    return _CaptureSurface(w, h)


def _lines(page, rows=None, cols=None, strip=False):
    """Split a rendered page string back into its rows of ``cols`` characters.

    ``rows`` given: exactly that many rows (a wall page is rows*cols long);
    ``rows=None``: every ``cols``-sized chunk of the string. ``strip=True`` trims
    each row — right for "is the word on the page" checks, wrong for column-
    alignment checks, which is why it is off by default."""
    if rows is None:
        out = [page[i:i + cols] for i in range(0, len(page), cols)]
    else:
        out = [page[r * cols:(r + 1) * cols] for r in range(rows)]
    return [l.strip() for l in out] if strip else out


@pytest.fixture(autouse=True)
def _fresh_gateway_clients():
    """Clear gateway's per-URL httpx client cache between tests, so a client built
    under one test's monkeypatched httpx never leaks into the next test."""
    yield
    from app import gateway
    with gateway._clients_lock:
        gateway._clients.clear()


async def until(pred, msg="condition never became true", timeout=3.0, step=0.02):
    """Poll ``pred`` to True instead of sleeping a fixed wall-clock amount — the
    anti-flake idiom for anything that completes "soon" but not instantly."""
    import asyncio, time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        await asyncio.sleep(step)
    raise AssertionError(msg)
