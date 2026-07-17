"""App-data helper endpoints — served at the fixed root paths that dropped-in app
manifests' searchUrl / resultKey point at, so they work unchanged.

Split out of main.py (audit E1); the bodies and behaviour are main.py's, verbatim.
``deps`` is the app.main module (see routes/__init__.py) — unused here, because
these routes are display-less by design: they delegate straight to helpers.py.
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import helpers


def build(deps) -> APIRouter:
    # dependency_overrides_provider is what @app.<method> bakes into an APIRoute;
    # these routes join app.routes FLAT (see main._include_flat), so they carry it
    # themselves. deps.app exists by the time main calls build().
    router = APIRouter(dependency_overrides_provider=deps.app)

    @router.get("/location_search")
    async def h_location_search(q: str = ""):
        return await helpers.location_search(q)

    @router.get("/location_reverse")
    async def h_location_reverse(lat: str = "", lon: str = ""):
        return await helpers.location_reverse(lat, lon)

    @router.get("/location_timezone")
    async def h_location_timezone(lat: str = "", lon: str = ""):
        return await helpers.location_timezone(lat, lon)

    @router.get("/timezones")
    async def h_timezones(q: str = ""):
        return helpers.timezones(q)

    @router.get("/stocks_search")
    async def h_stocks_search(q: str = ""):
        return await helpers.stocks_search(q)

    @router.get("/crypto_search")
    async def h_crypto_search(q: str = ""):
        return await helpers.crypto_search(q)

    @router.get("/sports_search")
    async def h_sports_search(q: str = ""):
        return await helpers.sports_search(q)

    return router
