"""
proxy.py — reverse-proxy the SplitFlapGateway's own web UI under /display/*.

This is what makes the companion + gateway feel like one app: the gateway's
calibration / modules / diagnostics pages are served through the companion at a
single origin, so there's no second host to visit and no CORS.

The gateway is an embedded (ESP32) UI that uses root-relative URLs ("/app.js",
fetch("/api/status")). We rewrite those in HTML/CSS/JS responses to sit under
/display/ so they resolve back through the proxy rather than hitting the
companion's own routes. Binary responses stream through untouched.
"""

from __future__ import annotations

import logging
import re

import httpx
from fastapi import Request, Response

log = logging.getLogger("companion.proxy")

# Hop-by-hop headers we must not forward.
_STRIP = {"host", "content-length", "transfer-encoding", "connection",
          "keep-alive", "content-encoding", "accept-encoding"}

# Rewrite root-relative URLs ( ="/x , ('/x , url(/x ) to /display/ — but leave
# protocol-relative //x and already-prefixed /display/ alone.
_URL_RE = re.compile(rb"""(?P<q>["'(=])/(?!/|display/)""")
_REWRITE_TYPES = ("text/html", "text/css", "application/javascript", "text/javascript")


def _rewrite(body: bytes) -> bytes:
    return _URL_RE.sub(rb"\g<q>/display/", body)


async def proxy(gateway_url: str, subpath: str, request: Request) -> Response:
    if not gateway_url:
        return Response("No gateway_url configured. Set it in Settings.",
                        status_code=502, media_type="text/plain")
    base = gateway_url.rstrip("/")
    url = f"{base}/{subpath}"
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _STRIP}
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            upstream = await client.request(
                request.method, url, params=request.query_params,
                headers=fwd_headers, content=body or None)
    except Exception as e:
        log.warning("gateway proxy error for %s: %s", url, e)
        return Response(f"Gateway unreachable at {base}: {e}",
                        status_code=502, media_type="text/plain")

    ctype = upstream.headers.get("content-type", "")
    content = upstream.content
    if any(t in ctype for t in _REWRITE_TYPES):
        content = _rewrite(content)

    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _STRIP}
    # Keep redirects inside the proxy namespace.
    loc = upstream.headers.get("location")
    if loc and loc.startswith("/") and not loc.startswith("/display/"):
        resp_headers["location"] = "/display" + loc
    return Response(content=content, status_code=upstream.status_code,
                    headers=resp_headers, media_type=ctype or None)
