"""The SPA shell cache-busts its CSS/JS so browsers refetch after an app update
(no manual cache purge needed)."""

from app.main import _cache_bust


def test_cache_bust_adds_content_hash(tmp_path):
    (tmp_path / "styles.css").write_bytes(b"body{}")
    (tmp_path / "app.js").write_bytes(b"console.log(1)")
    out = _cache_bust('<link href="/styles.css"><script src="/app.js"></script>', tmp_path)
    assert '/styles.css?v=' in out
    assert '/app.js?v=' in out


def test_cache_bust_query_tracks_content(tmp_path):
    (tmp_path / "styles.css").write_bytes(b"body{}")
    before = _cache_bust('<link href="/styles.css">', tmp_path)
    (tmp_path / "styles.css").write_bytes(b"body{color:red}")
    after = _cache_bust('<link href="/styles.css">', tmp_path)
    assert before != after   # hash changes only when the file changes


def test_cache_bust_missing_asset_is_noop(tmp_path):
    html = '<link href="/styles.css">'
    assert _cache_bust(html, tmp_path) == html
