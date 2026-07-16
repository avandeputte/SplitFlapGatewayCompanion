"""Vestaboard-compatible Local API (off unless COMPANION_VESTABOARD=1, or the dev
menu turns it on). Anything that speaks to a Vestaboard — a Home Assistant
rest_command, the HACS integration, a script — can then drive this display by
URL alone. The codec is in vestaboard.py; these are the endpoints.

The paths are Vestaboard's, so they sit at the root rather than under /api/*,
like the app-data helpers — and so this router must be included BEFORE the SPA
is mounted at "/" (main.py's ordering guarantees it). A real board answers on
port 7000; publish the container as `-p 7000:8000` and clients that hard-code
that port are satisfied too.

NOTE: this key guards these routes ONLY. The rest of the companion's API is
unauthenticated, as it always was — the key is Vestaboard compatibility, not a
security boundary for the host. The key itself is minted in main.py
(vestaboard_key / _persistent_secret): it is process-wide, the dev menu shows
it, and the MCP bearer token is its twin.

Split out of main.py (audit E1); the bodies, docstrings and behaviour are
main.py's, verbatim. ``deps`` is the app.main module — see routes/__init__.py.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .. import vestaboard

# A wrong key must answer exactly this — plain text, this string — because that is what a
# real Vestaboard returns and what clients test for verbatim (the popular ha-vestaboard
# does `resp.status == 401 and resp.text() == "Invalid API key"` to offer re-auth; a JSON
# {"detail": ...} body instead just reads as an unknown error).
VB_INVALID_KEY = "Invalid API key"


def build(deps) -> APIRouter:
    # dependency_overrides_provider is what @app.<method> bakes into an APIRoute;
    # these routes join app.routes FLAT (see main._include_flat), so they carry it
    # themselves. deps.app exists by the time main calls build().
    router = APIRouter(dependency_overrides_provider=deps.app)

    def _require_vestaboard() -> dict:
        """The Vestaboard config, or 404 when the layer is off — so the whole surface
        genuinely vanishes rather than answering 401s nobody can satisfy."""
        if not deps.config.vestaboard_enabled:
            raise HTTPException(404, "Vestaboard API is off (set COMPANION_VESTABOARD=1)")
        return deps.config.vestaboard

    def _key_error(request: Request) -> PlainTextResponse | None:
        """The 401 to return if the Vestaboard key is missing/wrong, else None. Returns the
        response rather than raising so the caller controls the exact body (see VB_INVALID_KEY)."""
        key = request.headers.get("X-Vestaboard-Local-Api-Key", "")
        if not key or not secrets.compare_digest(key, deps.vestaboard_key()):
            return PlainTextResponse(VB_INVALID_KEY, status_code=401)
        return None

    @router.post("/local-api/enablement")
    async def vb_enablement(request: Request):
        """Vestaboard's enablement handshake: present the token, get the API key back.
        On a real board the token is emailed to the owner; here it is whatever you set
        in COMPANION_VESTABOARD_ENABLEMENT_TOKEN. With no token set there is nothing to
        verify, so the exchange is refused (the key is in the Dev menu instead)."""
        vb = _require_vestaboard()
        token = vb.get("enablement_token") or ""
        if not token:
            raise HTTPException(403, "no enablement token configured "
                                     "(set COMPANION_VESTABOARD_ENABLEMENT_TOKEN)")
        sent = request.headers.get("X-Vestaboard-Local-Api-Enablement-Token", "")
        if not sent or not secrets.compare_digest(sent, token):
            raise HTTPException(403, "invalid enablement token")
        return {"message": "Local API enabled", "apiKey": deps.vestaboard_key()}

    @router.get("/local-api/{display_id}/message")
    @router.get("/local-api/message")
    async def vb_read_message(request: Request, display_id: str | None = None):
        """The board as it stands — whatever the flaps are showing (a running app's output
        included), not merely the last message someone posted, which is what this endpoint
        means on real hardware.

        The matrix is wrapped in ``{"message": [[...]]}``, matching the real Local API: every
        Vestaboard client reads it back as ``response["message"]``. Returning a bare array
        (as we did) makes a client crash the moment it does ``.get("message")`` on a list —
        which is why the reference integration would not even finish setup against us.

        /local-api/message stays bound to the DEFAULT display: a Vestaboard IS one board, and
        every existing client (ha-vestaboard included) posts to that fixed path with no way to
        name a wall. /local-api/<display-id>/message addresses the others."""
        # Guard BEFORE resolving the display: a disabled layer must be a flat 404,
        # not an oracle that enumerates display ids through its error bodies.
        _require_vestaboard()
        d = deps.display_by_id(display_id) if display_id else deps.display_for(request)
        if err := _key_error(request):
            return err
        g = d.config.grid
        rows, cols = int(g["rows"]), int(g["cols"])
        return {"message": vestaboard.encode(d.state.current_chars, rows, cols)}

    @router.post("/local-api/{display_id}/message")
    @router.post("/local-api/message")
    async def vb_send_message(request: Request, display_id: str | None = None):
        """Post a message. Takes every shape a Vestaboard client sends:

            [[0,8,5,...], ...]                        a bare character-code matrix
            {"characters": [[...]], "strategy": ...}  ...with an animation
            {"text": "HELLO"}                         an extension of ours, because most
                                                      Home Assistant setups send text

        Like a compose push, this takes the display over: any running app or playlist is
        cancelled (send_text_bg), which is what posting to a Vestaboard implies.

        The bare path drives the DEFAULT display, because that is the only one an existing
        Vestaboard client can reach; /local-api/<display-id>/message drives a named wall.
        """
        _require_vestaboard()
        d = deps.display_by_id(display_id) if display_id else deps.display_for(request)
        if err := _key_error(request):
            return err
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "body must be JSON")

        g = d.config.grid
        rows, cols = int(g["rows"]), int(g["cols"])
        strategy = None

        try:
            if isinstance(body, list):                       # the bare-matrix form
                page = vestaboard.fit(vestaboard.decode(body), rows, cols)
            elif isinstance(body, dict) and body.get("characters") is not None:
                strategy = body.get("strategy")
                page = vestaboard.fit(vestaboard.decode(body["characters"]), rows, cols)
            elif isinstance(body, dict) and isinstance(body.get("text"), str):
                strategy = body.get("strategy")
                # The board has no lowercase flaps; uppercase exactly the way every other
                # text path here does (cp1252-aware, so accents survive as one cell).
                page = vestaboard.layout_text(body["text"], rows, cols, d.controller.caps)
            else:
                raise HTTPException(422, "expected a character matrix, {\"characters\": [[...]]}, "
                                         "or {\"text\": \"...\"}")
        except vestaboard.VestaboardError as e:
            raise HTTPException(422, str(e))

        style = vestaboard.style_for(strategy, d.config.display.get("transition_style", "ltr"))
        # Not a frame: the codec already turned every colour chip into a COLOUR (its own
        # codepoint), so nothing here is a lowercase letter standing in for one.
        d.controller.send_text_bg(page, style=style)
        d.ha.publish_state()
        # 201, not 200: the real Local API returns 201 Created on a successful write, and
        # clients treat anything else as failure (ha-vestaboard's coordinator raises
        # UpdateFailed unless the write returns 201, so a 200 broke every message it sent).
        return JSONResponse({"ok": True}, status_code=201)

    return router
