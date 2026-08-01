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
   write: ``send_text`` stops whatever app or playlist was running. That is what
   "show this" means on a wall with a single surface.
2. Reads report **what is actually on the flaps** — a running app's output included —
   not the last thing someone sent.
"""

from __future__ import annotations

import asyncio
import logging

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from . import gateway, renderer, vestaboard
from .plugins import app_id_from_ref

log = logging.getLogger("companion.mcp")


def build(displays) -> MCPServer:
    """Wire the tools onto the live DisplayManager and return the server.

    Every tool takes an optional `display`. Omitted, it means the DEFAULT display — which
    is what keeps existing prompts and clients working: they were written when there was
    one wall and send no display id. Requiring the argument would have regressed all of
    them. `list_displays` is how an agent finds the others.

    ``stateless_http`` keeps every call self-contained: there is no per-client session
    held on the server, so a plain POST works and a client that reconnects has nothing
    to re-establish. ``streamable_http_path="/"`` puts the endpoint at the mount point
    itself — mounted at ``/mcp``, that is ``/mcp``, not ``/mcp/mcp``.
    """
    mcp = MCPServer(
        "SplitFlap Gateway Companion",
        instructions=(
            "Controls a physical split-flap display: a grid of character modules on a "
            "wall. Showing a message takes the display over and stops any running app "
            "or playlist. Reads report what is physically on the flaps right now."
        ),
    )

    def _res(display: str = ""):
        """The wall a call is about. No id -> the default one."""
        if not display:
            return displays.default
        d = displays.get(display)
        if d is None:
            known = ", ".join(displays.ids())
            raise ValueError(f"no such display: {display!r} (known: {known})")
        return d

    def _grid(d) -> tuple[int, int]:
        g = d.config.grid
        return int(g["rows"]), int(g["cols"])

    def _app_name(d, app_id: str | None) -> str | None:
        if not app_id:
            return None
        m = d.plugins.manifest(app_id) or {}
        return m.get("name", app_id)

    @mcp.tool()
    def list_displays() -> list[dict]:
        """The split-flap displays this companion drives.

        Most setups have one, and every other tool defaults to it, so you only need this
        when the user talks about a particular wall ("what's on the kitchen display?").
        Pass an `id` from here as the `display` argument to any other tool. `is_default`
        marks the one used when no display is given.
        """
        out = []
        for d in displays.all():
            st = d.status()
            out.append({
                "id": st["id"],
                "name": st["name"],
                "rows": (st["grid"] or {}).get("rows"),
                "cols": (st["grid"] or {}).get("cols"),
                "is_default": st["id"] == displays.default_id,
                "showing": st["active_app"] or st["active_playlist"] or None,
            })
        return out

    @mcp.tool()
    def get_display(display: str = "") -> dict:
        """What the split-flap display is showing right now.

        `display` picks the wall (see list_displays); omit it for the default one.

        `lines` is the actual flaps, a running app's output included. `driver` says what
        is in control: "app", "playlist", "message" (a manual/one-off message), or "idle".
        `showing` is the app whose output is on screen this moment — which is NOT the same
        as the driver: while a playlist runs it cycles through several apps, and `showing`
        is the one currently up (null during a composed message). When a playlist is
        driving, `playlist` places it in the rotation: which entry, of how many, what's next.
        """
        d = _res(display)
        rows, cols = _grid(d)
        snap = d.state.snapshot()
        chars = snap["chars"]

        if snap.get("active_playlist"):
            driver = "playlist"
        elif snap.get("active_app"):
            driver = "app"
        elif "".join(chars).strip():
            driver = "message"           # something on the board, but no app/playlist owns it
        else:
            driver = "idle"

        showing = snap.get("current_app")
        out = {
            "rows": rows,
            "cols": cols,
            # A color flap has no letter to be reported as (the letter r is now the
            # letter r), so it comes back as the tile a person would have typed.
            "lines": ["".join(renderer.for_text(c) for c in chars[r * cols:(r + 1) * cols])
                      for r in range(rows)],
            "driver": driver,
            "showing": {"app_id": showing, "name": _app_name(d, showing)} if showing else None,
        }
        if driver == "playlist":
            labels = snap.get("playlist_entries") or []
            idx = snap.get("playlist_index")
            nxt = labels[(idx + 1) % len(labels)] if labels and idx is not None else None
            out["playlist"] = {
                "name": snap.get("active_playlist"),
                "index": idx,
                "count": len(labels),
                "apps": labels,
                "next": nxt,
            }
        return out

    @mcp.tool()
    async def show_message(text: str, style: str | None = None,
                           seconds: int | None = None, display: str = "") -> dict:
        """Show text on the display, centered and word-wrapped (newlines force a line break).

        `display` picks the wall (see list_displays); omit it for the default one.

        `style` is the flap transition (see list_styles); omit it for the display's default.

        `seconds`: leave unset to take the display over until something else changes it. Set
        it to show the message *temporarily* — after that many seconds the display reverts to
        whatever was playing before (an app or playlist keeps running underneath and comes
        back on its own; if nothing was playing, the board blanks). Use this for a heads-up
        that shouldn't clobber the running rotation — "dinner's ready", a doorbell, an alert.

        Without `seconds`, returns once the flaps have landed on the message.
        """
        if style and style not in renderer.ALL_STYLES:
            raise ValueError(
                f"unknown style {style!r} — one of: {', '.join(renderer.ALL_STYLES)}")
        d = _res(display)
        rows, cols = _grid(d)
        # The same steps a Vestaboard text write makes, for the same reasons.
        # layout_text is the shared "center it on the wall" layout; its result is final
        # characters, sent raw — uppercasing here would turn a color flap (lowercase
        # r/o/y/g/b/p/w) into a letter. Case folding happens on the wall, last, for
        # everyone (engine._normalize); we fold only the `lines` we REPORT back, so what
        # we tell the caller matches what the wall will actually show.
        page = vestaboard.layout_text(text, rows, cols, d.controller.caps)
        shown = page if d.controller.shows_lowercase else renderer.fold(page)
        lines = [shown[r * cols:(r + 1) * cols] for r in range(rows)]

        if seconds and seconds > 0:
            # A temporary takeover: the running app/playlist parks and resumes after. Runs
            # in the background (the message can last minutes) so the tool returns at once.
            running = d.controller.show_temporary(page, seconds, style=style or "ltr")
            d.ha.publish_state()
            return {"ok": True, "lines": lines, "seconds": seconds,
                    "reverts_to": "the running app/playlist" if running else "blank"}

        # send_text, not send_text_bg: both stop the running app, but this one awaits
        # the transition. A Compose push can return early because a human is watching
        # the wall — an agent is not, and it will call get_display next. If the tool
        # returned while the flaps were still turning, it would read back the OLD board.
        await d.controller.send_text(page, style=style)
        d.ha.publish_state()
        return {"ok": True, "lines": lines}

    @mcp.tool()
    async def clear_display(display: str = "") -> dict:
        """Blank every module, stopping any running app or playlist.

        `display` picks the wall (see list_displays); omit it for the default one."""
        d = _res(display)
        await d.controller.clear()
        d.ha.publish_state()
        return {"ok": True}

    @mcp.tool()
    def list_apps(display: str = "") -> list[dict]:
        """The apps installed on this display (weather, clock, stocks, ...).

        Installed apps are PER display, so two walls can have different ones.

        Pass an `id` to run_app. `configurable` marks apps with their own settings — use
        get_app_settings / configure_app on those. These are the web UI's Apps tab list.
        """
        d = _res(display)
        out = []
        for a in d.plugins.app_list():
            try:
                configurable = bool(d.plugins.app_settings_public(a["id"]))
            except Exception:
                configurable = False        # a broken/odd app must not sink the whole list
            out.append({"id": a["id"], "name": a["name"],
                        "description": a.get("description", ""), "configurable": configurable})
        return out

    @mcp.tool()
    async def run_app(app_id: str, display: str = "") -> dict:
        """Run an installed app on the display (ids come from list_apps).

        `display` picks the wall (see list_displays); omit it for the default one."""
        d = _res(display)
        app_id = app_id_from_ref(app_id)
        try:
            await d.controller.run_app(app_id)
        except KeyError:
            raise ValueError(f"app not installed: {app_id}")
        d.ha.publish_state()
        return {"ok": True, "active_app": app_id, "display": d.id}

    @mcp.tool()
    def get_app_settings(app_id: str, display: str = "") -> dict:
        """An app's settings and their current values — what configure_app can change.

        Settings are PER display: the same app on two walls has two sets of them.

        Names are short (e.g. "stocks_list", "location"); pass those same names back to
        configure_app. `type` tells you the shape ("number", "toggle", "select" with
        `options`, "search_chips" for a place/ticker list, "text"/"password").
        """
        d = _res(display)
        app_id = app_id_from_ref(app_id)
        try:
            return {"app_id": app_id, "settings": d.plugins.app_settings_public(app_id)}
        except KeyError:
            raise ValueError(f"app not installed: {app_id}")

    @mcp.tool()
    async def configure_app(app_id: str, settings: dict, display: str = "") -> dict:
        """Change an app's settings (see get_app_settings for the names and current values).

        Changes only the wall you name (default: the default one) — the same app on another
        display keeps its own settings.

        `settings` maps short names to values, e.g. {"stocks_list": "AAPL,MSFT"} or
        {"location": "Paris, France"}. If the app is on the display now, it restarts so the
        change shows immediately. Only that app's own settings are accepted — display-wide
        options (Language, Location, Timezone) live in the global settings, not here.
        """
        d = _res(display)
        app_id = app_id_from_ref(app_id)
        try:
            valid = {f["name"] for f in d.plugins.app_settings_public(app_id)}
        except KeyError:
            raise ValueError(f"app not installed: {app_id}")
        if not isinstance(settings, dict) or not settings:
            raise ValueError("settings must be a non-empty object of name -> value")

        unknown = [k for k in settings if k.lstrip("_") not in valid
                   and k.replace(f"plugin_{app_id}_", "") not in valid]
        if unknown:
            raise ValueError(f"unknown setting(s) for {app_id}: {', '.join(unknown)} — "
                             f"valid names: {', '.join(sorted(valid))}")

        # Store under the plugin_<id>_ keys the settings store expects.
        prefixed = {f"plugin_{app_id}_{k.replace(f'plugin_{app_id}_', '')}": v
                    for k, v in settings.items()}
        d.plugins.save_settings(app_id, prefixed)
        # Restart it if it's the one on screen, so the change takes effect now (page dwell,
        # refresh cadence, content) — the same thing the web UI's settings dialog does.
        if d.controller.active_app == app_id:
            await d.controller.run_app(app_id)
        d.ha.publish_state()
        return {"ok": True, "app_id": app_id, "settings": d.plugins.app_settings_public(app_id)}

    @mcp.tool()
    def list_playlists(display: str = "") -> list[dict]:
        """The saved playlists (run one with run_playlist).

        Playlists are PER display — each wall has its own.

        `apps` is the running order — the app ids in sequence, "(message)" for a composed
        entry — so you can say what a playlist contains and in what order without running it.
        """
        from .engine import _entry_label
        d = _res(display)
        saved = d.settings.get("saved_app_playlists", {}) or {}
        return [
            {"name": n,
             "apps": [_entry_label(e) for e in (p or {}).get("entries", [])],
             "loop": bool((p or {}).get("loop"))}
            for n, p in saved.items()
        ]

    @mcp.tool()
    async def run_playlist(name: str, display: str = "") -> dict:
        """Run a saved playlist by name (names come from list_playlists).

        `display` picks the wall (see list_displays); omit it for the default one."""
        d = _res(display)
        saved = d.settings.get("saved_app_playlists", {}) or {}
        pl = saved.get(name)
        if not pl:
            raise ValueError(f"no such playlist: {name}")
        entries = pl.get("entries") or []
        if not entries:
            raise ValueError(f"playlist {name!r} has no entries")
        await d.controller.run_playlist(entries, pl.get("loop", True), name)
        d.ha.publish_state()
        return {"ok": True, "active_playlist": d.controller.active_playlist, "display": d.id}

    @mcp.tool()
    async def stop(display: str = "") -> dict:
        """Stop the running app or playlist. The board goes blank — nothing is running, so
        it shows nothing.

        `display` picks the wall (see list_displays); omit it for the default one."""
        d = _res(display)
        await d.controller.stop_app()
        d.ha.publish_state()
        return {"ok": True}

    @mcp.tool()
    def list_styles() -> list[str]:
        """The flap transition styles show_message accepts."""
        return list(renderer.ALL_STYLES)

    # -- the Matrix Gateway's kitchen timer + daily alarms (fw "timer"/"alarms") ------

    def _timer_wall(display: str, need: str = "timer"):
        """(display, gateway_url) for a wall whose CAPABILITIES advertise the on-device
        feature — ``need`` is "timer" or "alarms" (device.Capabilities.can_timer /
        can_alarms, from the gateway's feature tokens). A clear error for the physical
        split-flap gateway, which has neither."""
        d = _res(display)
        caps = d.controller._caps()
        if not getattr(caps, f"can_{need}", False):
            raise ValueError(f"this display's gateway does not advertise the {need!r} "
                             "capability (a Matrix Gateway feature)")
        return d, str(d.config.transport.get("gateway_url") or "").strip()

    @mcp.tool()
    async def get_timer(display: str = "") -> dict:
        """The Matrix Gateway's kitchen timer: whether it is running, seconds remaining,
        and whether an alarm is FIRING right now (beeping/flashing on the panel).

        `display` picks the wall (see list_displays); omit it for the default one."""
        _d, url = _timer_wall(display)
        st = await asyncio.to_thread(gateway.timer_get, url)
        if not st:
            raise ValueError("the gateway did not answer /api/timer")
        return {"active": bool(st.get("active")), "remaining_seconds": int(st.get("remaining") or 0),
                "alarm_firing": bool(st.get("alarmFiring"))}

    @mcp.tool()
    async def start_timer(seconds: int, display: str = "") -> dict:
        """Start the on-device kitchen countdown (1 second .. 24 hours). The panel shows
        it and beeps at zero; it replaces any countdown already running."""
        if not 1 <= int(seconds) <= 86400:
            raise ValueError("seconds must be 1 .. 86400 (24 h)")
        _d, url = _timer_wall(display)
        got = await asyncio.to_thread(gateway.timer_start, url, int(seconds))
        if not got.get("ok"):
            raise ValueError("the gateway refused the timer (1 s .. 24 h)")
        return {"ok": True, "remaining_seconds": int(got.get("remaining") or seconds)}

    @mcp.tool()
    async def stop_timer(display: str = "") -> dict:
        """Cancel the running countdown AND dismiss a firing alarm (one call does both —
        the firmware couples them, so this is also 'silence the alarm')."""
        _d, url = _timer_wall(display)
        if not await asyncio.to_thread(gateway.timer_stop, url):
            raise ValueError("the gateway did not answer /api/timer")
        return {"ok": True}

    @mcp.tool()
    async def list_alarms(display: str = "") -> list[dict]:
        """The Matrix Gateway's four daily alarm slots: time (HH:MM, gateway-local),
        days ('daily', 'weekdays', 'weekends' or 'mon,tue,…') and whether enabled."""
        _d, url = _timer_wall(display, "alarms")
        slots = await asyncio.to_thread(gateway.alarms_get, url)
        if not slots:
            raise ValueError("the gateway did not answer /api/alarms")
        return [{"slot": i, "time": s.get("time", "07:00"),
                 "days": gateway.mask_to_days(int(s.get("days") or 0x7F)),
                 "enabled": bool(s.get("enabled"))} for i, s in enumerate(slots)]

    @mcp.tool()
    async def set_alarm(slot: int, time: str, days: str = "daily", enabled: bool = True,
                        display: str = "") -> dict:
        """Program one alarm slot (0-3): `time` is HH:MM in the gateway's local time,
        `days` is 'daily', 'weekdays', 'weekends' or day names like 'mon,wed,fri'.
        The other slots are left as they are."""
        if not 0 <= int(slot) <= 3:
            raise ValueError("slot must be 0 .. 3")
        import re
        if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", str(time).strip()):
            raise ValueError("time must be HH:MM (24-hour)")
        mask = gateway.days_to_mask(days)
        _d, url = _timer_wall(display, "alarms")
        slots = await asyncio.to_thread(gateway.alarms_get, url)
        if not slots:
            raise ValueError("the gateway did not answer /api/alarms")
        while len(slots) < 4:
            slots.append({"time": "07:00", "days": 0x7F, "enabled": False})
        slots[int(slot)] = {"time": str(time).strip(), "days": mask, "enabled": bool(enabled)}
        if not await asyncio.to_thread(gateway.alarms_set, url, slots):
            raise ValueError("the gateway refused the alarm update")
        return {"ok": True, "slot": int(slot), "time": str(time).strip(),
                "days": gateway.mask_to_days(mask), "enabled": bool(enabled)}

    @mcp.tool()
    async def get_gateway_settings(display: str = "") -> dict:
        """The wall's device settings, by capability: Quiet Time (whether it is on NOW
        plus the daily schedule), the speaker (enabled + master volume 0-100), panel
        brightness (1-255) and the nightly dim schedule. Sections the gateway lacks
        are marked unsupported and omitted."""
        d = _res(display)
        return await asyncio.to_thread(
            gateway.read_settings,
            str(d.config.transport.get("gateway_url") or "").strip(),
            d.controller._caps())

    @mcp.tool()
    async def set_gateway_settings(patch: dict, display: str = "") -> dict:
        """Change the wall's device settings — pass ONLY what should change, in the same
        shape get_gateway_settings returns:

          {"quiet": {"on": true}}                              silence the wall now
          {"quiet": {"schedule": {"enabled": true, "start": "22:00", "end": "07:00",
                                  "days": "daily"}}}           the nightly window
          {"sound": {"enabled": true, "volume": 60}}           the speaker
          {"brightness": 180}                                  panel brightness 1-255
          {"dim": {"enabled": true, "start": "22:00", "end": "07:00", "level": 40}}

        Note: while a Quiet-Time SCHEDULE window is active the firmware refuses a manual
        quiet OFF (the schedule wins) — the reply's applied.quiet_on says what holds."""
        d = _res(display)
        try:
            applied = await asyncio.to_thread(
                gateway.apply_settings_patch,
                str(d.config.transport.get("gateway_url") or "").strip(),
                d.controller._caps(), patch)
        except (LookupError, RuntimeError) as e:
            raise ValueError(str(e))
        return {"ok": True, "applied": applied}

    @mcp.tool()
    async def clear_alarm(slot: int, display: str = "") -> dict:
        """Disable one alarm slot (0-3). The slot keeps its time and days for later."""
        if not 0 <= int(slot) <= 3:
            raise ValueError("slot must be 0 .. 3")
        _d, url = _timer_wall(display, "alarms")
        slots = await asyncio.to_thread(gateway.alarms_get, url)
        if not slots:
            raise ValueError("the gateway did not answer /api/alarms")
        while len(slots) < 4:
            slots.append({"time": "07:00", "days": 0x7F, "enabled": False})
        slots[int(slot)]["enabled"] = False
        if not await asyncio.to_thread(gateway.alarms_set, url, slots):
            raise ValueError("the gateway refused the alarm update")
        return {"ok": True, "slot": int(slot)}

    return mcp


def http_app(server: MCPServer):
    """The configured streamable-HTTP ASGI app for a built server — main.py mounts this.

    In mcp 2.0 the transport config lives here, not on the MCPServer constructor.
    ``stateless_http`` keeps every call self-contained (a plain POST works; a reconnecting
    client has nothing to re-establish). ``streamable_http_path="/"`` puts the endpoint at
    the mount point — mounted at ``/mcp`` that is ``/mcp``, not ``/mcp/mcp``.

    DNS-rebinding protection is turned OFF: its default allowed_hosts is
    [127.0.0.1, localhost, ::1], but we are a LAN service reached by whatever name the user
    has (homeassistant.local:8000, 192.168.1.60:8000, a reverse-proxy hostname), so the
    default would answer every one with 421 Misdirected Request, and there is no allow-any
    wildcard. Safe here: _MCPGuard in main.py demands a bearer token BEFORE a request
    reaches this app, and the token is not an ambient cookie a hostile page could replay
    cross-origin — which is the only thing rebinding protection defends against.
    """
    return server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
