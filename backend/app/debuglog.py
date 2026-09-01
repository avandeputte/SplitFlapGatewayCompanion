"""debuglog.py — an opt-in "wire log" for debugging what the companion sends the gateway.

Turned on from the ⚙ Tools menu (POST /api/dev/debug-log) and downloadable from the same
place. While on, three things are captured as plain-text lines to a bounded file under the
data dir:

  * every SEND to a gateway device and the RECEIVE that answers it — the flap batch
    (`/api/rs485/batch`, `/api/display/cells`), canvas ops/ticker, settings, config polls —
    WITH a hex+ascii preview of the payload, because "the gateway is getting garbage" is a
    question you answer by looking at the actual bytes on the wire;
  * every app DATA FETCH (the `requests` traffic an app makes to its upstream API) and its
    result, so a bad page can be traced back to the data it was built from;
  * canvas draw-stream open/close/error, so a stream that keeps re-opening is visible.

OFF by default and a cheap no-op then: every instrumentation point early-returns on
``is_enabled()`` before formatting anything. One process-wide file (all displays + all app
fetches funnel here); a single ``threading.Lock`` serialises writes from the event loop and
the worker threads alike. The file is capped and rotated once (``.1``) so it can never grow
without bound; the download concatenates the two halves oldest-first.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# How much of a body to render. Bodies are previews, not captures — enough to see a
# mis-encoded character or a truncated JSON, not a full 2 MB frame readback.
_PREVIEW_CHARS = 400        # text/JSON bodies
_HEX_BYTES = 256            # binary bodies, shown as hex + an ascii-safe gutter
_DEFAULT_MAX = 8_000_000    # per-file cap; the log rotates to <file>.1 at this size

# Query-string parameters whose VALUE is a secret — redacted in logged URLs so a log a
# user hands over for debugging never carries their API keys.
_SECRET_PARAMS = {"key", "apikey", "api_key", "appid", "app_id", "token", "access_token",
                  "secret", "client_secret", "password", "pwd", "auth"}

_lock = threading.Lock()
_S: dict = {
    "enabled": False,
    "path": None,       # Path to the current log file
    "prev": None,       # Path to the rotated-out half (<file>.1)
    "fh": None,         # open text handle while enabled
    "written": 0,       # bytes in the current file (for the rotation check)
    "max": _DEFAULT_MAX,
    "events": 0,        # lines written since this process started
}


# --------------------------------------------------------------------------- state
def configure(path, enabled: bool, max_bytes: int = _DEFAULT_MAX) -> None:
    """Point the log at ``path`` and set its initial on/off state — called once at
    startup. Remembers the path so :func:`set_enabled` can toggle later without it."""
    with _lock:
        _S["path"] = Path(path)
        _S["prev"] = _S["path"].with_name(_S["path"].name + ".1")
        _S["max"] = int(max_bytes)
        _apply_locked(bool(enabled))


def set_enabled(on: bool) -> bool:
    """Runtime toggle (the Tools menu). Returns the resulting state. No-op without a
    prior :func:`configure` to supply the path."""
    with _lock:
        if _S["path"] is None:
            return False
        _apply_locked(bool(on))
        return _S["enabled"]


def is_enabled() -> bool:
    return _S["enabled"]


def status() -> dict:
    """For the Tools menu: whether it's on, and how big the log is right now."""
    with _lock:
        return {"enabled": _S["enabled"], "bytes": _total_size_locked(),
                "events": _S["events"], "path": str(_S["path"] or "")}


def clear() -> None:
    """Truncate the log (both halves), keeping it open if it was on."""
    with _lock:
        was = _S["enabled"]
        _close_locked()
        for p in (_S["prev"], _S["path"]):
            try:
                if p and p.exists():
                    p.unlink()
            except OSError:
                pass
        _S["events"] = 0
        if was:
            _open_locked()
            _emit_locked("·", "log cleared")


def read_bytes() -> bytes:
    """The whole log, oldest half first — for the download. Flushes first so an
    in-flight session's newest lines are included."""
    with _lock:
        fh = _S["fh"]
        if fh is not None:
            try:
                fh.flush()
            except Exception:
                pass
        out = b""
        for p in (_S["prev"], _S["path"]):
            try:
                if p and p.exists():
                    out += p.read_bytes()
            except OSError:
                pass
        return out


# ------------------------------------------------------------------- event helpers
def gw_send(method: str, base: str, path: str, body=None, ctype: str | None = None,
            note: str = "") -> None:
    """A request going OUT to a gateway device."""
    if not _S["enabled"]:
        return
    _rec("→", _join(f"GW {method} {_ep(base, path)}", note), _fmt_body(body, ctype))


def gw_recv(method: str, base: str, path: str, status_code, ms: float, body=None,
            ctype: str | None = None, error=None) -> None:
    """The gateway's answer (or the failure to get one)."""
    if not _S["enabled"]:
        return
    ep = _ep(base, path)
    if error is not None:
        _rec("✗", f"GW {method} {ep}  FAILED after {ms:.0f}ms", str(error))
    else:
        _rec("←", f"GW {status_code} {method} {ep}  {ms:.0f}ms", _fmt_body(body, ctype))


def app_send(method: str, url: str) -> None:
    """An app about to fetch upstream data."""
    if not _S["enabled"]:
        return
    _rec("→", f"APP {method} {_safe_url(url)}")


def app_recv(method: str, url: str, status_code, ms: float, size=None, error=None) -> None:
    """The result of an app data fetch."""
    if not _S["enabled"]:
        return
    if error is not None:
        _rec("✗", f"APP {method} {_safe_url(url)}  FAILED after {ms:.0f}ms", str(error))
    else:
        n = f"{size}B" if size is not None else "?B"
        _rec("←", f"APP {status_code} {method} {_safe_url(url)}  {n}  {ms:.0f}ms")


def stream_event(base: str, event: str, note: str = "") -> None:
    """A canvas draw-stream lifecycle event (open / close / error)."""
    if not _S["enabled"]:
        return
    _rec("·", _join(f"STREAM {event} {_host(base)}", note))


def install_http_hooks() -> None:
    """Wrap the two HTTP entry points apps fetch data through, ONCE each, so every app
    fetch is captured whichever one it uses (a survey of the shipped apps found both). Each
    is idempotent, a no-op while logging is off, and never lets a logging failure break a
    real fetch. Gateway traffic is NOT caught here — it rides httpx2, instrumented directly
    at its own chokepoints (gateway._request, the REST transport)."""
    _install_requests_hook()
    _install_urllib_hook()


def _install_requests_hook() -> None:
    """``requests.Session.request`` — the seam every ``requests.get(...)`` funnels through."""
    try:
        import requests.sessions as _rs
    except Exception:
        return
    if getattr(_rs.Session, "_companion_wire_wrapped", False):
        return
    orig = _rs.Session.request

    def wrapped(self, method, url, **kw):
        if not _S["enabled"]:
            return orig(self, method, url, **kw)
        m = str(method).upper()
        try:
            app_send(m, url)
        except Exception:
            pass
        t0 = time.monotonic()
        try:
            r = orig(self, method, url, **kw)
        except Exception as e:
            try:
                app_recv(m, url, "—", (time.monotonic() - t0) * 1000, error=e)
            except Exception:
                pass
            raise
        try:
            # A streamed response hasn't read its body — don't force it just to size it.
            size = None if kw.get("stream") else len(r.content)
            app_recv(m, url, r.status_code, (time.monotonic() - t0) * 1000, size=size)
        except Exception:
            pass
        return r

    wrapped._companion_wire_wrapped = True          # type: ignore[attr-defined]
    _rs.Session.request = wrapped
    _rs.Session._companion_wire_wrapped = True       # type: ignore[attr-defined]


def _install_urllib_hook() -> None:
    """``urllib.request.urlopen`` — several apps fetch with the stdlib instead of requests.
    The body is NOT read (a urllib response is a one-shot stream the app must read itself),
    so only the request line and the status are logged."""
    try:
        import urllib.request as _ur
    except Exception:
        return
    if getattr(_ur, "_companion_wire_wrapped", False):
        return
    orig = _ur.urlopen

    def wrapped(url, *a, **kw):
        if not _S["enabled"]:
            return orig(url, *a, **kw)
        # `url` is a str or a Request; pull the method/URL off whichever it is.
        if hasattr(url, "full_url"):
            u, m = url.full_url, url.get_method()
        else:
            u, m = str(url), "GET"
        try:
            app_send(m, u)
        except Exception:
            pass
        t0 = time.monotonic()
        try:
            r = orig(url, *a, **kw)
        except Exception as e:
            try:
                app_recv(m, u, "—", (time.monotonic() - t0) * 1000, error=e)
            except Exception:
                pass
            raise
        try:
            status = getattr(r, "status", None)
            if status is None and hasattr(r, "getcode"):
                status = r.getcode()
            app_recv(m, u, status if status is not None else "?", (time.monotonic() - t0) * 1000)
        except Exception:
            pass
        return r

    wrapped._companion_wire_wrapped = True          # type: ignore[attr-defined]
    _ur.urlopen = wrapped
    _ur._companion_wire_wrapped = True               # type: ignore[attr-defined]


# ------------------------------------------------------------------------- internals
def _rec(arrow: str, summary: str, detail=None) -> None:
    """The one write path. Cheap early-out, then a locked append."""
    if not _S["enabled"]:
        return
    with _lock:
        if _S["enabled"]:
            _emit_locked(arrow, summary, detail)


def _emit_locked(arrow: str, summary: str, detail=None) -> None:
    fh = _S["fh"]
    if fh is None:
        return
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    line = f"{ts}  {arrow}  {summary}\n"
    if detail:
        for dl in str(detail).splitlines() or [""]:
            line += f"                  {dl}\n"
    try:
        fh.write(line)
    except Exception:
        return
    _S["written"] += len(line.encode("utf-8", "replace"))
    _S["events"] += 1
    if _S["written"] >= _S["max"]:
        _rotate_locked()


def _apply_locked(enabled: bool) -> None:
    if enabled and not _S["enabled"]:
        _open_locked()
        _S["enabled"] = True
        _emit_locked("·", f"debug logging ON (pid {os.getpid()})")
    elif not enabled and _S["enabled"]:
        _emit_locked("·", "debug logging OFF")
        _S["enabled"] = False
        _close_locked()


def _open_locked() -> None:
    p = _S["path"]
    if p is None:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    # Line-buffered so a mid-session download sees the newest lines without a flush race.
    _S["fh"] = p.open("a", buffering=1, encoding="utf-8", errors="replace")
    try:
        _S["written"] = p.stat().st_size
    except OSError:
        _S["written"] = 0


def _close_locked() -> None:
    fh = _S["fh"]
    _S["fh"] = None
    if fh is not None:
        try:
            fh.close()
        except Exception:
            pass


def _rotate_locked() -> None:
    """Current file is full: close it, replace <file>.1 with it, reopen fresh. Bounds the
    log to ~2× the cap and keeps the download to two files."""
    _close_locked()
    prev, path = _S["prev"], _S["path"]
    try:
        if prev and prev.exists():
            prev.unlink()
        if path and path.exists():
            path.rename(prev)
    except OSError:
        pass
    _open_locked()


def _total_size_locked() -> int:
    total = 0
    for p in (_S["prev"], _S["path"]):
        try:
            if p and p.exists():
                total += p.stat().st_size
        except OSError:
            pass
    return total


# ------------------------------------------------------------------- formatting
def _fmt_body(body, ctype: str | None = None):
    """A short, safe preview of a request/response body. Binary shows hex + an ascii
    gutter (the point of the whole feature: seeing the actual bytes); text/JSON is
    truncated. Never raises."""
    if body is None:
        return None
    try:
        if isinstance(body, (bytes, bytearray, memoryview)):
            b = bytes(body)
            head = b[:_HEX_BYTES]
            asc = "".join(chr(c) if 32 <= c < 127 else "." for c in head)
            more = f"  …(+{len(b) - _HEX_BYTES}B)" if len(b) > _HEX_BYTES else ""
            return f"[{len(b)}B]  hex: {head.hex(' ')}{more}\nasc: {asc}"
        if isinstance(body, str):
            s = body
        else:
            s = json.dumps(body, ensure_ascii=False)
    except Exception:
        try:
            s = repr(body)
        except Exception:
            return "<unrenderable body>"
    s = s.replace("\r", " ").replace("\n", " ")
    return s if len(s) <= _PREVIEW_CHARS else s[:_PREVIEW_CHARS] + f"…(+{len(s) - _PREVIEW_CHARS} chars)"


def _ep(base: str, path: str) -> str:
    """host + path, query dropped (it's rarely on gateway calls and never the interesting part)."""
    return f"{_host(base)}{str(path).split('?', 1)[0]}"


def _host(base: str) -> str:
    try:
        sp = urlsplit(base if "://" in str(base) else f"//{base}")
        return sp.netloc or str(base).rstrip("/")
    except Exception:
        return str(base).rstrip("/")


def _safe_url(url: str) -> str:
    """A URL with secret-looking query values redacted, so a shared log leaks no keys."""
    try:
        sp = urlsplit(str(url))
        if not sp.query:
            return str(url)
        q = [(k, "REDACTED" if k.lower() in _SECRET_PARAMS else v)
             for k, v in parse_qsl(sp.query, keep_blank_values=True)]
        return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(q), sp.fragment))
    except Exception:
        return str(url)


def _join(summary: str, note: str) -> str:
    return f"{summary}  {note}" if note else summary
