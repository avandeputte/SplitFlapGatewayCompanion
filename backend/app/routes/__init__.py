"""The companion API, split into routers along its real seams (backend audit E1):
displays / dev / apps / playlists+triggers / message / vestaboard local-api / the
root app-data helpers.

Each module exposes ``build(deps) -> APIRouter`` — gwproxy.py's pattern. ``deps`` is
the ``app.main`` module itself: the routers resolve every shared name (``displays``,
``display_for``, ``do_gateway_sync``, ``vestaboard_key``, ``_companion_url`` …)
through it at REQUEST time, which is exactly the late binding these routes had when
they were module-level in main.py. That is not a convenience — it is load-bearing:
a test that monkeypatches ``main.vestaboard_key`` must still be what /local-api
checks, and ``main._companion_url`` is set by the lifespan AFTER the routers are
built, so capturing it at import would freeze it empty.

Nothing here imports main — main imports these modules and hands itself in, so
there is no cycle. What stays in main.py is what is not a route: the lifespan and
the per-display start/stop it drives, the module aliases the background loops use,
the secret minting the MCP gate needs at ASGI level, the MCP mount (an ASGI app
cannot be mounted after startup), the middleware, and the static SPA.

House rule, unchanged from main.py and pinned by test_display.py: a route resolves
its display through ``deps.display_for(request)`` — never through the default
display's aliases — except the deliberately process-wide toggles, which say so.
"""
