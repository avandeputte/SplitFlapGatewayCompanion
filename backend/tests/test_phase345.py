"""Unit tests for the Phase 3-5 building blocks (proxy, scheduler, helpers)."""

from app import helpers, proxy
from app.scheduler import in_window


def test_proxy_rewrites_root_relative_urls():
    body = b'<link href="/s.css"><script src="/a.js"></script><a href="/ota">x</a>'
    out = proxy._rewrite(body)
    assert b'href="/display/s.css"' in out
    assert b'src="/display/a.js"' in out
    assert b'href="/display/ota"' in out


def test_proxy_rewrites_fetch_and_css_url():
    assert b'fetch("/display/api/x")' in proxy._rewrite(b'fetch("/api/x")')
    assert b"url(/display/img.png)" in proxy._rewrite(b"url(/img.png)")


def test_proxy_leaves_protocol_relative_and_prefixed():
    body = b'src="//cdn/x.js" href="/display/already"'
    out = proxy._rewrite(body)
    assert b'src="//cdn/x.js"' in out          # protocol-relative untouched
    assert b'href="/display/already"' in out    # not double-prefixed


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
