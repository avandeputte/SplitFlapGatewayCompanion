"""gwproxy.py — serve the gateway's own web UI *through* the companion, at /gw/.

Why this exists, in one sentence: Home Assistant can only put the **add-on's own port**
in the sidebar, and the gateway is a different device — so the only way the gateway's UI
can appear inside Home Assistant is if we serve it ourselves.

Without this, the gateway tabs are links to http://<gateway>/, which leave Home Assistant
entirely (on mobile, they leave the app). And the gateway's own "Companion" tabs link back
with ``target='_top'``, which punches out of the ingress iframe — so the round trip never
returns. Proxying puts both ends on one origin and the whole thing stays in the sidebar.

It also answers the theming question for free: because the page passes through here, we can
add a stylesheet to it, and the gateway's CSS is driven by the *same* custom properties as
the companion's (``--bg``, ``--card``, ``--acc``, ``--brand``, ``--txt`` …). So the Home
Assistant skin is an override of a dozen variables, not a rewrite — and it needs no
firmware change.

What has to be rewritten on the way through, and why:

* **Its API calls.** The gateway's UI calls ``fetch("/api/...")`` with absolute literals.
  Served under ``/gw/``, those would resolve against the *companion's* root and hit our own
  ``/api/*`` — a different API entirely. An injected shim prefixes them instead. (It patches
  fetch and XHR rather than rewriting the JS text: the calls are built in code, and string
  substitution across 100 KB of minified JS is a good way to break it.)
* **``target='_top'``.** The gateway's link back to the companion breaks out of the iframe
  on purpose — correct when it is a foreign site, wrong when we are both inside Home
  Assistant. Clicks on those are caught and navigated in-frame.
* **The companion URL the gateway hands its own JS.** It advertises us as
  ``http://192.168.1.220:8000``; inside Home Assistant the browser has to go to our ingress
  path instead, or the link leaves HA even without ``target``.
"""

from __future__ import annotations

import logging
import re

import httpx
from fastapi import APIRouter, Request, Response

log = logging.getLogger("companion.gwproxy")

PREFIX = "/gw"

# Everything the browser must not resolve against our own root: an absolute path in the
# gateway's page means "the gateway's root", and unrewritten it means OURS, where there is
# no /logo.svg to serve.
#
# BOTH quote styles. The gateway mixes them — its nav is written with double quotes but its
# brand image is `<img src='/logo.svg'>` — and a rule that only knew about double quotes
# left exactly that one asset pointing at us. The captured quote is put back verbatim, so
# the attribute is not re-quoted (which would break a value containing the other quote).
_ABS_URL = re.compile(r"""\b(href|src|action)=(["'])/(?!/)""")

_SHIM = """<script>
(function () {
  var B = %(base)s;                       // where the gateway lives under the companion
  var C = %(companion)s;                  // where the companion itself lives (ingress path)
  function fix(u) {
    return (typeof u === "string" && u.charAt(0) === "/" && u.indexOf(B) !== 0) ? B + u : u;
  }
  // The page calls fetch("/api/...") — under the proxy that would hit the COMPANION's API.
  var of = window.fetch;
  window.fetch = function (u, o) { return of.call(this, fix(u), o); };
  var ox = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (m, u) {
    return ox.apply(this, [m, fix(u)].concat([].slice.call(arguments, 2)));
  };
  // The gateway's own Companion tabs are built with target='_top' — right when it is a
  // foreign site, wrong when we are both inside Home Assistant: it would break out of the
  // sidebar. Keep the navigation in the frame.
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest('a[target="_top"]');
    if (!a) return;
    var h = a.getAttribute("href") || "";
    if (!h || h.charAt(0) === "#") return;
    e.preventDefault();
    window.location.href = h;
  }, true);
})();
</script>"""


def _bases(request: Request, display_id: str = "") -> tuple[str, str]:
    """(gateway-under-us, companion) as the *browser* sees them.

    Under Home Assistant the page is served from /api/hassio_ingress/<token>/, and every
    absolute path the browser builds has to carry that prefix or it resolves against the
    Home Assistant root. Same header the SPA shell uses (see main.spa_index).

    With several displays the base carries the display id, because the proxy REWRITES the
    gateway's own links to it: a `?display=` query param would be dropped the moment you
    clicked anything inside the proxied page, and the next click would silently land on
    the default wall's gateway.
    """
    ingress = (request.headers.get("X-Ingress-Path") or "").rstrip("/")
    seg = f"/{display_id}" if display_id else ""
    return f"{ingress}{PREFIX}{seg}", ingress


def build(displays) -> APIRouter:
    """`displays` is the DisplayManager: which gateway we proxy depends on the URL."""
    router = APIRouter()

    # ONE long-lived client with keep-alive, for every proxied request. A client per
    # request meant a fresh TCP connection per asset against an ESP32 that has about
    # four sockets to its name. Lives as long as the router (the process); redirects
    # must pass through un-followed so we can rewrite their Location below.
    client = httpx.AsyncClient(
        timeout=30, follow_redirects=False,
        limits=httpx.Limits(max_keepalive_connections=4, max_connections=8))

    def _resolve(path: str):
        """Split `<display-id>/<path>` from a plain `<path>` on the default gateway.

        `/gw/<path>` has always meant the one gateway, and bookmarks (and the gateway's
        own absolute links, which we rewrite) rely on it, so it must keep working. So the
        first segment is treated as a display id only when it actually names one — a
        gateway path that collides with a display id is fixable by renaming the display,
        whereas breaking every existing /gw/ URL is not.
        """
        head, _, rest = path.lstrip("/").partition("/")
        d = displays.get(head)
        if d is not None:
            return d, rest, head
        return displays.default, path, ""

    def _rewrite_html(html: str, base: str, companion: str) -> str:
        shim = _SHIM % {"base": _js(base), "companion": _js(companion)}
        # The Home Assistant look is the project's one look now. Newer gateway firmware
        # ships it natively; injecting the override here keeps an OLDER gateway matching
        # too. The gateway's CSS uses the same variables we do, so this re-points a dozen
        # values rather than restyling — harmless when the firmware already has them.
        head = shim + f'<link rel="stylesheet" href="{companion}/gateway-theme.css">'
        html = _ABS_URL.sub(rf'\1=\2{base}/', html)
        return html.replace("</head>", head + "</head>", 1)

    @router.api_route(PREFIX, methods=["GET"])
    @router.api_route(PREFIX + "/{path:path}",
                      methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def proxy(request: Request, path: str = ""):
        display, path, display_id = _resolve(path)
        gw = display.gateway_url.rstrip("/")
        if not gw:
            return Response("no gateway configured", status_code=503)

        base, companion = _bases(request, display_id)
        target = f"{gw}/{path.lstrip('/')}"
        body = await request.body()
        # Forward only the few short headers an ESP32 web server actually needs, as a
        # WHITELIST — not a blacklist. The gateway's HTTP server (ESP-IDF esp_http_server,
        # firmware 3.0) has small per-request header buffers and answers 431 "Header fields
        # are too long" to anything larger; a browser request — especially through Home
        # Assistant ingress — carries a big Cookie and a long Referer that blow straight
        # past that. So we pass the body's content type, content negotiation, and byte
        # ranges (all short) and drop everything else, which is either ours to set
        # (host / accept-encoding / content-length) or irrelevant to the gateway (cookies,
        # referer, user-agent, the ingress and forwarded-for families). Conditional headers
        # go too: we rewrite the page, so the gateway's ETag doesn't match what we return.
        keep = {"content-type", "accept", "range"}
        headers = {k: v for k, v in request.headers.items() if k.lower() in keep}

        try:
            r = await client.request(request.method, target, params=request.query_params,
                                     content=body or None, headers=headers)
        except Exception as e:
            log.warning("gateway proxy: %s %s failed: %s", request.method, target, e)
            return Response(f"gateway unreachable: {e}", status_code=502)

        ctype = r.headers.get("content-type", "")

        # A gateway redirect points into the GATEWAY — absolute ("/update") or fully
        # qualified ("http://192.168.1.229/update"). Passed through untouched, the
        # browser follows it out of /gw/ and lands on the companion's SPA (or leaves
        # Home Assistant entirely). Rewrite it to stay under the proxy.
        if 300 <= r.status_code < 400 and r.headers.get("location"):
            loc = r.headers["location"]
            if loc == gw or loc.startswith(gw + "/"):
                loc = loc[len(gw):] or "/"        # the gateway's own absolute URL
            if loc.startswith("/") and not loc.startswith("//"):
                out = {k: v for k, v in r.headers.items()
                       if k.lower() not in ("content-encoding", "content-length",
                                            "transfer-encoding", "location")}
                out["location"] = base + loc
                return Response(r.content, status_code=r.status_code, headers=out)
            # anywhere else (another host) is not ours to rewrite — fall through

        if "text/html" in ctype:
            return Response(_rewrite_html(r.text, base, companion),
                            status_code=r.status_code, media_type="text/html",
                            headers={"Cache-Control": "no-cache"})

        # The gateway tells its own page where the companion is, as the LAN URL it
        # registered (http://192.168.1.220:8000). In Home Assistant the browser must go to
        # our ingress path instead, or the link leaves HA. Point it back at us.
        if path.strip("/") == "api/companion" and "json" in ctype:
            try:
                doc = r.json()
                if doc.get("url"):
                    doc["url"] = companion or "/"
                    return Response(_json(doc), status_code=r.status_code,
                                    media_type="application/json")
            except Exception:
                pass

        out = {k: v for k, v in r.headers.items()
               if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")}
        return Response(r.content, status_code=r.status_code, headers=out)

    return router


def _js(s: str) -> str:
    import json
    return json.dumps(s)


def _json(doc) -> str:
    import json
    return json.dumps(doc)
