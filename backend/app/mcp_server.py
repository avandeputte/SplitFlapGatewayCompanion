"""mcp_server.py — the companion as an MCP server.

Model Context Protocol is what an LLM client (Claude, an agent, an IDE) speaks to
call tools. Expose the display as tools and any such client can drive the wall in
words — "put the standup time on the board", "what's showing right now?" — without
knowing anything about this project's HTTP API.

Shape (deliberately the same as the Vestaboard layer, see vestaboard.py):

* **Off by default** — ``COMPANION_MCP=1``, or the Dev-menu switch at runtime.
* **Bearer-authenticated**, with a token generated once and kept with the settings.
* When off, the whole ``/mcp`` surface **404s** — it doesn't exist, rather than
  answering 401s nobody can satisfy.

The gate and the token live in main.py (they need the settings store); this module
is only the tools. It takes its collaborators as arguments rather than importing
main, which would be a cycle — main imports this.

Two house rules carried over from the rest of the codebase:

1. A message **takes the display over**, exactly like a Compose push or a Vestaboard
   write: ``send_text_bg`` cancels whatever app or playlist was running. That is what
   "show this" means on a wall with a single surface.
2. Reads report **what is actually on the flaps** — a running app's output included —
   not the last thing someone sent.
"""

from __future__ import annotations

import logging

from mcp.server import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import renderer, vestaboard

log = logging.getLogger("companion.mcp")


def build(config, state, controller, plugins, plugin_settings, ha) -> FastMCP:
    """Wire the tools onto the app's live singletons and return the server.

    ``stateless_http`` keeps every call self-contained: there is no per-client session
    held on the server, so a plain POST works and a client that reconnects has nothing
    to re-establish. ``streamable_http_path="/"`` puts the endpoint at the mount point
    itself — mounted at ``/mcp``, that is ``/mcp``, not ``/mcp/mcp``.
    """
    mcp = FastMCP(
        "SplitFlap Gateway Companion",
        instructions=(
            "Controls a physical split-flap display: a grid of character modules on a "
            "wall. Showing a message takes the display over and stops any running app "
            "or playlist. Reads report what is physically on the flaps right now."
        ),
        stateless_http=True,
        streamable_http_path="/",
        # FastMCP's default host is 127.0.0.1, and on that default it quietly turns on
        # DNS-rebinding protection with allowed_hosts=[127.0.0.1, localhost, ::1]. We are
        # a LAN service reached by whatever name the user has — homeassistant.local:8000,
        # 192.168.1.60:8000, a reverse-proxy hostname — so that default answers every one
        # of them with 421 Misdirected Request. There is no wildcard that allows any host,
        # so the check has to come off.
        #
        # Safe here because it is not what guards this endpoint: _MCPGuard in main.py
        # demands a bearer token BEFORE the request ever reaches this app. Rebinding
        # protection exists to stop a browser replaying *ambient* credentials (a cookie)
        # at a LAN address; there are none to replay — the token is not a cookie, and a
        # hostile page cannot read it cross-origin.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    def _grid() -> tuple[int, int]:
        g = config.grid
        return int(g["rows"]), int(g["cols"])

    @mcp.tool()
    def get_display() -> dict:
        """What the split-flap display is showing right now.

        Reports the actual flaps — including the output of a running app — as one
        string per row, plus the grid size and whatever app or playlist is driving it.
        """
        rows, cols = _grid()
        snap = state.snapshot()
        chars = snap["chars"]
        return {
            "rows": rows,
            "cols": cols,
            "lines": ["".join(chars[r * cols:(r + 1) * cols]) for r in range(rows)],
            "active_app": snap.get("active_app"),
            "active_playlist": snap.get("active_playlist"),
        }

    @mcp.tool()
    async def show_message(text: str, style: str | None = None) -> dict:
        """Show text on the display. Stops any running app or playlist.

        The text is centred on the board and word-wrapped to fit; newlines force a
        line break. `style` is the flap transition (see list_styles) — omit it for the
        display's configured default.

        Returns once the flaps have actually landed on the message.
        """
        if style and style not in renderer.ALL_STYLES:
            raise ValueError(
                f"unknown style {style!r} — one of: {', '.join(renderer.ALL_STYLES)}")
        rows, cols = _grid()
        # The same two steps a Vestaboard text write makes, and for the same reasons:
        # the board has no lowercase flaps (cp1252_upper keeps accents one cell wide),
        # and layout_text is the shared "centre it on the wall" layout. The result is
        # final characters, so it must go out raw — otherwise a colour flap (lowercase
        # r/o/y/g/b/p/w) would be uppercased into a letter.
        page = vestaboard.layout_text(renderer.cp1252_upper(text), rows, cols)
        # send_text, not send_text_bg: both stop the running app, but this one awaits
        # the transition. A Compose push can return early because a human is watching
        # the wall — an agent is not, and it will call get_display next. If the tool
        # returned while the flaps were still turning, it would read back the OLD board.
        await controller.send_text(page, style=style, raw=True)
        ha.publish_state()
        return {"ok": True, "lines": [page[r * cols:(r + 1) * cols] for r in range(rows)]}

    @mcp.tool()
    async def clear_display() -> dict:
        """Blank every module, stopping any running app or playlist."""
        await controller.clear()
        ha.publish_state()
        return {"ok": True}

    @mcp.tool()
    def list_apps() -> list[dict]:
        """The apps installed on this display (weather, clock, stocks, ...).

        Pass an `id` to run_app. These are the apps the web UI's Apps tab lists.
        """
        return [
            {"id": a["id"], "name": a["name"], "description": a.get("description", "")}
            for a in plugins.app_list()
        ]

    @mcp.tool()
    async def run_app(app_id: str) -> dict:
        """Run an installed app on the display (ids come from list_apps)."""
        app_id = app_id[7:] if app_id.startswith("plugin_") else app_id
        try:
            await controller.run_app(app_id)
        except KeyError:
            raise ValueError(f"app not installed: {app_id}")
        ha.publish_state()
        return {"ok": True, "active_app": app_id}

    @mcp.tool()
    def list_playlists() -> list[dict]:
        """The saved playlists, by name (run one with run_playlist)."""
        saved = plugin_settings.get("saved_app_playlists", {}) or {}
        return [
            {"name": n,
             "apps": len((p or {}).get("entries", [])),
             "loop": bool((p or {}).get("loop"))}
            for n, p in saved.items()
        ]

    @mcp.tool()
    async def run_playlist(name: str) -> dict:
        """Run a saved playlist by name (names come from list_playlists)."""
        saved = plugin_settings.get("saved_app_playlists", {}) or {}
        pl = saved.get(name)
        if not pl:
            raise ValueError(f"no such playlist: {name}")
        entries = pl.get("entries") or []
        if not entries:
            raise ValueError(f"playlist {name!r} has no entries")
        await controller.run_playlist(entries, pl.get("loop", True), name)
        ha.publish_state()
        return {"ok": True, "active_playlist": controller.active_playlist}

    @mcp.tool()
    async def stop() -> dict:
        """Stop the running app or playlist, leaving what it last drew on the board."""
        await controller.stop_app()
        ha.publish_state()
        return {"ok": True}

    @mcp.tool()
    def list_styles() -> list[str]:
        """The flap transition styles show_message accepts."""
        return list(renderer.ALL_STYLES)

    return mcp
