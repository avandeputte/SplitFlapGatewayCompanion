"""
transport/rest.py — drive the gateway over its HTTP REST API.

No MQTT broker required. A whole page is drawn in ONE request via the gateway's
batch endpoint ``/api/rs485/batch`` ({"frames":[...], "step_ms":N}) — this is what
closes the animation gap vs MQTT (each module would otherwise be its own HTTP
round-trip). The companion targets Gateway 3.0+, so the batch endpoint is always
available; there's no per-frame legacy fallback.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from .. import device, renderer
from .base import DisplayTransport, frame_for


def _is_404(exc: Exception) -> bool:
    """Is this the gateway saying "I do not have that endpoint"?

    Only a 404. A 500 means the endpoint EXISTS and something went wrong behind it, and
    quietly changing wire format on the strength of that would hide a real fault.
    """
    r = getattr(exc, "response", None)
    return getattr(r, "status_code", None) == 404

log = logging.getLogger("companion.transport.rest")

# The gateway and split-flap modules render the Windows-1252 code page, so the
# request body is serialized as cp1252 (not httpx's default UTF-8). That keeps
# every accented character a single byte — the byte the module expects — instead
# of a UTF-8 multibyte sequence. JSON punctuation is ASCII, so only the frame
# string values carry the high bytes.
_JSON_1252_HEADERS = {"Content-Type": "application/json; charset=windows-1252"}

# How often (seconds) to resend the whole page instead of just the changed cells, to heal a
# shown-cell cache that has drifted from the actual wall (see RestTransport._shown). A repaint is
# invisible where the cache is right (an unchanged module does not re-flip) and corrects it where
# it is not. TIME-based, not page-count-based: a stale flap must heal on a wall-clock bound even
# when the running app rarely changes its page (the engine drives a heartbeat re-emit so this
# fires during a hold too — see DisplayController._play_app_pages).
_REPAINT_SECONDS = 15.0


def _win1252_body(payload: dict) -> bytes:
    import json
    return json.dumps(payload, ensure_ascii=False).encode("cp1252", "replace")


class RestTransport(DisplayTransport):
    type_name = "rest"
    batch_capable = True   # the engine will hand us whole pages to batch

    def __init__(self, gateway_url: str, timeout: float = 5.0):
        if not gateway_url:
            raise ValueError("REST transport requires a gateway_url")
        self.base = gateway_url.rstrip("/")
        # Batching can block the gateway for the page's cascade duration, so use
        # a longer timeout than the single-frame path.
        self.timeout = timeout
        self._client = None
        self._connected = False
        self._last_error: str | None = None
        # What the wall on the other end can show. Probed on connect: it is a property of
        # the gateway, not a setting, and with several displays the answer differs per wall.
        self.caps = device.SPLIT_FLAP
        # What we last put in each module. Lets us send `skip` for the cells that did not
        # change: a clock repainting 75 modules every second to move one digit is traffic
        # the wall does not need, and flaps that should not be moving.
        self._shown: dict[int, str] = {}
        # ...but the cache is only our BELIEF about the wall, and anything that changed the panel
        # without going through here — another client (MQTT / the MCP server / a second companion),
        # the gateway's own compose page, a reboot's re-home — leaves it stale, and a cell we
        # wrongly believe is right gets skipped, so an old flap lingers "until it finally changes".
        # So every _REPAINT_SECONDS we resend the WHOLE page: a module already showing the value it
        # is sent does not re-flip (it is a no-op on the wall), so the repaint is invisible except
        # where the cache had drifted — which is exactly where it heals it.
        self._last_repaint = 0.0

    async def connect(self) -> None:
        import httpx

        self._client = httpx.AsyncClient(
            base_url=self.base,
            timeout=self.timeout,
            headers={"Content-Type": "application/json"},
        )
        # We do not know what is on the wall until we have put it there.
        self._shown.clear()
        # Probe the gateway so the UI can show a truthful status pill.
        try:
            r = await self._client.get("/api/status")
            self._connected = r.status_code < 500
            self._last_error = None if self._connected else f"status {r.status_code}"
        except Exception as e:
            self._connected = False
            self._last_error = f"gateway unreachable: {e}"
            log.warning("REST %s", self._last_error)
        # …and ask what it CAN do.
        await self.probe_capabilities()

    async def probe_capabilities(self) -> None:
        """Ask the wall what it can show. Called on connect, and again on a resync.

        ASKED, not inferred. The gateway knows its own reel — it is the thing that configures
        the modules — and it tells us: which characters every module carries, whether
        it has lowercase and pictograph flaps, whether colors are named. A guess from the
        product name and a firmware number cannot see a physical wall's alphabet at all,
        so it is only the fallback: a gateway too old to answer gets the inference
        (device.of) and keeps working.
        """
        if self._client is None:
            return
        try:
            r = await self._client.get("/api/capabilities")
            caps = device.from_capabilities(r.json()) if r.status_code < 400 else None
        except Exception:
            caps = None

        if caps is not None:
            self.caps = caps
            log.info(
                "gateway %s: %d characters on every module%s%s%s", self.base,
                len(caps.charset),
                "" if caps.uniform else " (a MIXED wall — this is the intersection)",
                ", lowercase" if caps.lowercase else "",
                ", pictographs" if caps.pictographs else "",
            )
            return

        # Older firmware: no /api/capabilities. Guess from the product, as we always did — and
        # accept that we do not know this wall's alphabet, so nothing gets degraded for it.
        try:
            cfg = (await self._client.get("/api/config")).json()
            self.caps = device.of(cfg)
            log.info("gateway %s has no /api/capabilities; assuming %s from its product name",
                     self.base, "a Matrix Portal" if self.caps.indexed else "a split-flap")
        except Exception:
            self.caps = device.SPLIT_FLAP

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def forget(self) -> None:
        """Drop the shown-cell cache so the next page repaints every module. Used
        after a Matrix panel leaves canvas mode: while a canvas app had the panel,
        it drew straight to the framebuffer without going through ``_shown``, so
        what we last recorded there no longer matches the wall — and the unchanged-
        cell diff would otherwise skip flaps that need to come back."""
        self._shown.clear()

    async def send_frame(self, module_id: int, char: str) -> None:
        if self._client is None:
            raise RuntimeError("REST transport not connected")
        try:
            r = await self._client.post(
                "/api/rs485/send",
                content=_win1252_body({"data": frame_for(module_id, char)}),
                headers=_JSON_1252_HEADERS,
            )
            r.raise_for_status()
            self._connected = True
            self._last_error = None
        except Exception as e:
            self._connected = False
            self._last_error = str(e)
            raise

    # -- the index-addressed path (Matrix Portal) -----------------------------
    def _cell(self, ch: str):
        """One character as a cell for POST /api/display/cells.

        The firmware REJECTS the whole page if any character has no flap ("a half-written
        wall is worse than a rejected request"), which is the right call for it and a trap
        for us: one stray glyph from an app would blank the wall. So anything the reel
        cannot show is turned into something it can, HERE, before it is sent.
        """
        # A color is a color because the PAGE said so (renderer.normalize made it an
        # explicit COLOR_PUA codepoint), never because the character happens to be one of
        # the seven letters. Guessing here is how "Hello" comes out as "Hell<orange>".
        if renderer.is_color(ch):
            return {"color": renderer.PUA_TO_NAME[ch]}
        if ch in renderer.PICTOGRAPHS or renderer.in_cp1252(ch):
            return {"ch": ch}
        return {"ch": " "}                      # no flap for it: a blank, not a 400

    async def _send_cells(self, frames: list[tuple[int, str]], step_ms: int) -> None:
        """Duplicate module ids in ``frames`` are ORDERED repaints — the slot style spins
        a module and then locks it in. Collapsing them into a dict would keep only the
        last frame, so the whole spin phase silently vanished on this path. Split into
        passes of unique ids and send them in order instead."""
        seen: set[int] = set()
        passes: list[list[tuple[int, str]]] = [[]]
        for mid, ch in frames:
            if mid in seen:
                passes.append([])
                seen = set()
            seen.add(mid)
            passes[-1].append((mid, ch))
        for chunk in passes:
            if chunk:
                await self._send_cells_once(chunk, step_ms)

    async def _send_cells_once(self, frames: list[tuple[int, str]], step_ms: int) -> None:
        base = min(m for m, _ in frames)
        by_id = dict(frames)
        cells, sent = [], 0
        for mid in range(base, max(by_id) + 1):
            ch = by_id.get(mid)
            if ch is None:
                cells.append({"skip": True})            # not ours to touch
            elif self._shown.get(mid) == ch:
                cells.append({"skip": True})            # already showing it — leave it alone
            else:
                cells.append(self._cell(ch))
                sent += 1
        if not sent:
            return                                       # the wall already says this
        payload = {"start": base, "step_ms": int(step_ms), "cells": cells}
        import json
        r = await self._client.post(
            "/api/display/cells",
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=30.0)
        r.raise_for_status()
        self._shown.update(by_id)

    async def send_batch(self, frames: list[tuple[int, str]], step_ms: int) -> None:
        """Draw a whole page in one request.

        On a Matrix Portal this goes to /api/display/cells, which addresses flaps by INDEX:
        lowercase and accents survive, pictographs are reachable at all, colors are named
        rather than stealing seven letters — and unchanged cells are skipped, so moving one
        digit of a clock does not repaint seventy-five modules.

        Everywhere else it is /api/rs485/batch (Gateway 3.0+). step_ms
        paces the cascade device-side, so the call blocks for roughly the page's animation
        duration — one round-trip for the whole page. Raises on failure (the caller logs it).
        """
        if self._client is None:
            raise RuntimeError("REST transport not connected")
        # Every _REPAINT_SECONDS, forget the diff so this page goes out whole — self-heals a
        # shown-cell cache that drifted from the wall (an unchanged module no-ops, so it's unseen).
        import time
        now = time.monotonic()
        if now - self._last_repaint >= _REPAINT_SECONDS:
            self._last_repaint = now
            self._shown.clear()
        if self.caps.indexed:
            try:
                await self._send_cells(frames, step_ms)
                self._connected = True
                self._last_error = None
                return
            except Exception as e:
                # The wall is now in an unknown state, so the next page must be sent whole.
                self._shown.clear()
                if _is_404(e):
                    # The gateway does not HAVE /api/display/cells. Whatever its capabilities
                    # said, the endpoint is the truth, and a 404 is it telling us plainly.
                    #
                    # This is a fallback rather than an error because of what the alternative
                    # costs: a wall in somebody's hallway goes dark and the UI says "offline"
                    # while the gateway is sitting there answering everything else perfectly.
                    # A physical gateway can advertise the `index` feature (which is
                    # POST /api/flap/index, one module by flap number) and have it read as
                    # the bulk cells API. One wrong word in a feature list, and every page 404s.
                    #
                    # So: believe the endpoint, downgrade for the life of this transport, say so
                    # once, and send the page on the wire that works.
                    log.warning(
                        "gateway %s advertised the index-addressed page API but POST "
                        "/api/display/cells is 404 — it does not have it. Falling back to "
                        "/api/rs485/batch for this gateway.", self.base)
                    self.caps = replace(self.caps, indexed=False)
                    # fall through to the legacy path below, with this same page
                else:
                    self._connected = False
                    self._last_error = str(e)
                    raise
        # The same unchanged-cell diffing the cells path has: a clock moving one digit
        # should occupy the gateway for one frame, not the whole board. Only when we
        # KNOW what every one of these modules shows — a fresh connect, a failed send
        # or a resize leaves `_shown` unknown/mismatched, and then the page goes whole.
        # Duplicate ids are an ordered repaint (the slot spin); never diff those.
        send = frames
        ids = [m for m, _ in frames]
        if len(set(ids)) == len(ids) and all(m in self._shown for m in ids):
            send = [(m, c) for m, c in frames if self._shown.get(m) != c]
            if not send:
                self._connected = True       # the wall already says this
                self._last_error = None
                return
        payload = {"frames": [frame_for(mid, renderer.for_legacy(ch)) for mid, ch in send],
                   "step_ms": int(step_ms)}
        try:
            # allow the gateway to pace a long page without a client timeout
            r = await self._client.post("/api/rs485/batch",
                                        content=_win1252_body(payload),
                                        headers=_JSON_1252_HEADERS, timeout=30.0)
            r.raise_for_status()
            self._connected = True
            self._last_error = None
            self._shown.update(frames)       # dict semantics: the LAST frame per id wins
        except Exception as e:
            # The wall is in an unknown state; the next page must be sent whole.
            self._shown.clear()
            self._connected = False
            self._last_error = str(e)
            raise
