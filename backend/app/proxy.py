"""
proxy.py — reverse-proxy the SplitFlapGateway's own web UI under /display/*.

This is what makes the companion + gateway feel like one app: the gateway's
calibration / modules / diagnostics pages are served through the companion at a
single origin, so there's no second host to visit and no CORS.

The gateway is an embedded (ESP32) UI that uses root-relative URLs. We make it
work under /display/ two ways, without ever touching JavaScript source (rewriting
JS is unsafe — it corrupts regex literals and `a/b` division):

  * HTML: rewrite only ``href/src/action`` attributes to /display/, and inject a
    tiny shim that patches ``fetch`` and ``XMLHttpRequest`` so the gateway's own
    runtime calls to "/api/…" transparently go to "/display/api/…".
  * CSS: rewrite ``url(/…)``.
  * Everything else (JS, JSON, binary): streamed through untouched.
"""

from __future__ import annotations

import logging
import re

import httpx
from fastapi import Request, Response

log = logging.getLogger("companion.proxy")

_STRIP = {"host", "content-length", "transfer-encoding", "connection",
          "keep-alive", "content-encoding", "accept-encoding"}

# Only rewrite real URL-bearing HTML attributes (never JS bodies).
_ATTR_RE = re.compile(rb"""(?i)\b(href|src|action)=(["'])/(?!/|display/)""")
_CSS_URL_RE = re.compile(rb"""url\((["']?)/(?!/|display/)""")

# Injected into the <head> so the gateway's own fetch/XHR calls to "/…" resolve
# through the proxy. Leaves protocol-relative (//) and already-prefixed URLs alone.
_SHIM = (
    b"<script>(function(){var P='/display';"
    b"function fix(u){try{if(typeof u==='string'&&u.charAt(0)==='/'"
    b"&&u.substr(0,2)!=='//'&&u.indexOf(P+'/')!==0)return P+u;}catch(e){}return u;}"
    b"var of=window.fetch;if(of)window.fetch=function(u,o){return of.call(this,fix(u),o);};"
    b"var xo=XMLHttpRequest.prototype.open;"
    b"XMLHttpRequest.prototype.open=function(){var a=[].slice.call(arguments);"
    b"a[1]=fix(a[1]);return xo.apply(this,a);};})();</script>"
)


def _rewrite_html(body: bytes) -> bytes:
    body = _ATTR_RE.sub(rb"\1=\2/display/", body)
    m = re.search(rb"(?i)<head[^>]*>", body)
    if m:
        return body[:m.end()] + _SHIM + body[m.end():]
    return _SHIM + body


def _rewrite_css(body: bytes) -> bytes:
    return _CSS_URL_RE.sub(rb"url(\1/display/", body)


async def proxy(gateway_url: str, subpath: str, request: Request) -> Response:
    if not gateway_url:
        return Response("No gateway configured. Set COMPANION_GATEWAY_URL.",
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
    if "text/html" in ctype:
        content = _rewrite_html(content)
    elif "css" in ctype:
        content = _rewrite_css(content)

    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _STRIP}
    loc = upstream.headers.get("location")
    if loc and loc.startswith("/") and not loc.startswith("/display/"):
        resp_headers["location"] = "/display" + loc
    return Response(content=content, status_code=upstream.status_code,
                    headers=resp_headers, media_type=ctype or None)
