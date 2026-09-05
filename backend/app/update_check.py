"""update_check.py — an optional "is there a newer release?" check against GitHub.

Off by default; enabled by the companion-wide ``check_for_updates`` setting (the ⚙ Tools
menu). When on, the SPA shows a one-line banner on every page linking to the release notes.

The GitHub answer is cached PROCESS-WIDE (~6h) and served stale on any error, so a flaky
network — or GitHub's 60-requests/hour anonymous rate limit — never breaks the UI or spams the
API. `releases/latest` returns the newest NON-prerelease, so a beta build simply never sees an
"update" until a stable passes it.
"""

from __future__ import annotations

import logging
import re
import threading
import time

from . import __version__

log = logging.getLogger("companion.update")

_REPO = "avandeputte/SplitFlapGatewayCompanion"
_TTL = 6 * 3600.0
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "latest": "", "url": ""}


def _vtuple(s: str) -> tuple:
    """Leading numeric version as a comparable tuple: 'v2.10.15-beta.1' -> (2, 10, 15). The
    prerelease suffix is dropped — releases/latest is a stable, so bases are what we compare."""
    base = str(s).strip().lstrip("vV").split("-")[0].split("+")[0]
    nums = re.findall(r"\d+", base)
    return tuple(int(n) for n in nums[:3]) if nums else (0,)


def current_version() -> str:
    return __version__


def _fetch_latest() -> tuple[str, str]:
    """(tag_name, html_url) of the newest published stable release. Raises on any failure."""
    import requests
    r = requests.get(
        f"https://api.github.com/repos/{_REPO}/releases/latest",
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"SplitFlapGatewayCompanion/{__version__}"},
        timeout=8)
    r.raise_for_status()
    d = r.json()
    return str(d.get("tag_name") or ""), str(d.get("html_url") or "")


def status(force: bool = False) -> dict:
    """``{current, latest, url, update_available}``. Cached ~6h; keeps the last good value on
    error. ``force`` refreshes now (used when the setting is just turned on)."""
    now = time.monotonic()
    with _lock:
        fresh = bool(_cache["latest"]) and (now - _cache["at"] < _TTL)
        latest, url = _cache["latest"], _cache["url"]
    if force or not fresh:
        try:
            latest, url = _fetch_latest()
            with _lock:
                _cache.update(at=now, latest=latest, url=url)
        except Exception as e:
            log.debug("update check failed (keeping any cached result): %s", e)
    cur = current_version()
    available = bool(latest) and _vtuple(latest) > _vtuple(cur)
    return {"current": cur, "latest": latest.lstrip("vV"), "url": url,
            "update_available": available}
