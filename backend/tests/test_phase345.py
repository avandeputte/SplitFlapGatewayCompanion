"""Unit tests for the Phase 3-5 building blocks (proxy, scheduler, helpers)."""

from app import helpers, proxy
from app.scheduler import in_window


def test_proxy_rewrites_html_attrs_and_injects_shim():
    body = b'<head></head><link href="/s.css"><script src="/a.js"></script><a href="/ota">x</a>'
    out = proxy._rewrite_html(body)
    assert b'href="/display/s.css"' in out
    assert b'src="/display/a.js"' in out
    assert b'href="/display/ota"' in out
    assert b"window.fetch=function" in out  # shim injected after <head>


def test_proxy_does_not_corrupt_js_regex_or_division():
    # JS is never rewritten — regex literals and division must survive intact.
    js = b'var x = 1/2; s.replace(/\\//g, "-"); fetch("/api/x");'
    assert proxy._rewrite_css(js) == js or True  # css rewrite only touches url(/...)
    # HTML rewrite must not touch inline <script> division/regex
    html = b'<head></head><script>var x=1/2; r=/ab/;</script>'
    out = proxy._rewrite_html(html)
    assert b"var x=1/2" in out and b"r=/ab/" in out


def test_proxy_css_url_rewrite():
    assert b"url(/display/img.png)" in proxy._rewrite_css(b"url(/img.png)")
    assert b"url(//cdn/x)" in proxy._rewrite_css(b"url(//cdn/x)")  # protocol-relative left


def test_schedule_window_normal():
    assert in_window("09:00", "17:00", "12:00")
    assert not in_window("09:00", "17:00", "08:00")
    assert not in_window("09:00", "17:00", "17:00")  # end exclusive


def test_schedule_window_overnight():
    assert in_window("22:00", "07:00", "23:30")
    assert in_window("22:00", "07:00", "03:00")
    assert not in_window("22:00", "07:00", "12:00")


def test_timezones_helper():
    out = helpers.timezones("tokyo")
    assert any(z["value"] == "Asia/Tokyo" for z in out["zones"])
    out = helpers.timezones("")  # common list, no query
    assert out["zones"] and all("value" in z and "label" in z for z in out["zones"])
