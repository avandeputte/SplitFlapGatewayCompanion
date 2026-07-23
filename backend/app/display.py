"""A Display — one gateway, one wall — and the manager that owns the set.

Each Display bundles the per-wall objects a gateway needs — its own geometry, settings store, and
app/playlist loop:

    config, state, controller, plugin_settings, plugins, scheduler, ha

`main` asks the `DisplayManager` for one rather than reaching for a global, and
`displays.current(request)` is the single seam that decides *which* wall an endpoint is talking to
(`?display=<id>`, else the default). One place builds and tears each one down.

The default display is **explicit**, not inferred: `DisplayManager.default` is which wall the
display-less surfaces resolve to — the bare `/api/...` routes, `/local-api/message` (a Vestaboard
client sends no display id), an MCP call with no `display` argument, an existing HACS entry. It is
persisted in the registry and settable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .engine import DisplayController
from .events import StateHub
from .homeassistant import HomeAssistant
from .plugin_settings import PluginSettings
from .plugins import PluginRuntime
from .scheduler import Scheduler
from .state import DisplayState

log = logging.getLogger("companion.display")

# The id vocabulary is the registry's (it persists these); one implementation.
from .registry import DEFAULT_ID, slugify  # noqa: E402  (re-export for callers)


@dataclass
class Display:
    """One gateway and everything that belongs to it.

    Nothing here is global:
    two Displays can run side by side, each with its own geometry, settings store,
    installed apps, playlists, triggers and running app.
    """

    id: str
    name: str
    config: Config
    state: DisplayState
    controller: DisplayController
    settings: PluginSettings
    plugins: PluginRuntime
    scheduler: Scheduler
    ha: HomeAssistant
    # The tabs this gateway advertises about itself (Gateway 3.4+). Per-display on
    # purpose: a module-level global would be last-writer-wins with two gateways —
    # the nav would show whichever one registered most recently.
    gateway_tabs: list[dict[str, str]] = field(default_factory=list)
    # Pushes this display's live state to the browser over SSE (GET /api/events).
    # Set in build() once state + controller exist; None only for a bare hand-built
    # Display in a test that never touches the stream.
    events: StateHub | None = None

    @classmethod
    def build(cls, *, apps_dir: Path, id: str = DEFAULT_ID, name: str = "",
              config: Config | None = None, data_dir: Path | None = None,
              gateway_url: str = "", own_settings: bool = False) -> "Display":
        """Construct a display and everything it owns, in the order they depend on
        each other.

        `own_settings` puts this display's store in ``data/displays/<id>/``. Off by default so a
        bare Display.build() reads the single-display file, which is what the registry-less
        tests and callers mean.
        """
        cfg = config or Config(data_dir, gateway_url=gateway_url)
        state = DisplayState(cfg.module_count())
        controller = DisplayController(cfg, state)
        # Wholly this display's own — and therefore wholly mirrorable to its gateway,
        # which is what makes it recoverable. There is no shared store (see plugin_settings).
        settings = PluginSettings(cfg.data_dir,
                                  display_id=id if own_settings else None)
        # Uploaded apps are SHARED across displays (data/apps/): which apps a wall has
        # *installed* is per display, but the same zip should not live on disk twice.
        plugins = PluginRuntime(cfg, settings, apps_dir, cfg.data_dir / "apps")
        controller.attach_plugins(plugins)
        # An app can ask what this wall can SHOW, so it can offer a pictograph where the
        # wall has one and a word where it does not (see PluginRuntime.attach_caps).
        plugins.attach_caps(lambda: controller.caps)
        disp = cls(
            id=id,
            name=name or "SplitFlap",
            config=cfg,
            state=state,
            controller=controller,
            settings=settings,
            plugins=plugins,
            scheduler=Scheduler(controller, plugins),
            # Its own HA device, on its own MQTT topics — two walls must not fight over one.
            ha=HomeAssistant(cfg, plugins, controller,
                             display_id=id, display_name=name or "SplitFlap"),
        )
        disp.events = StateHub(disp.live_snapshot)
        return disp

    # -- convenience -----------------------------------------------------------
    @property
    def gateway_url(self) -> str:
        return (self.config.transport.get("gateway_url") or "").strip()

    def live_snapshot(self) -> dict:
        """The live-preview state — what's on the wall, plus whether a canvas is drawing
        (which sends the preview to the panel image instead of the stale flap grid). The
        single source of truth for both ``GET /api/current_state`` and the SSE stream, so
        the two can never disagree about what the browser should show."""
        snap = self.state.snapshot()
        snap["canvas"] = self.controller.has_canvas_preview()
        return snap

    def grid_changed(self) -> None:
        """The one correct reaction to new geometry, in the one correct order:
        resize the engine's canvas, then drop pages that were laid out for the
        old grid. Written once so no call site can get the pair wrong."""
        self.controller.resize_grid()
        self.plugins.on_grid_changed()

    def status(self) -> dict:
        """What this display is doing — the shape the UI's switcher will want."""
        return {
            "id": self.id,
            "name": self.name,
            "gateway_url": self.gateway_url,
            "grid": dict(self.config.grid),
            "module_count": self.config.module_count(),
            "active_app": self.controller.active_app,
            "active_playlist": self.controller.active_playlist,
            # Whether this wall can show lowercase, accents and pictographs (a Matrix
            # Portal can; a physical reel cannot). Per display: one companion can drive
            # both, and they do not have the same alphabet.
            "rich": self.controller.rich,
        }


class DisplayManager:
    """The set of displays, and which one the display-less surfaces mean. `current(request)` is the
    seam every endpoint goes through — honouring `?display=<id>` (or a path prefix) lives in that one
    method rather than in the call sites."""

    def __init__(self, apps_dir: Path, *, registry=None, data_dir: Path | None = None):
        self.apps_dir = Path(apps_dir)
        self.registry = registry          # registry.DisplayRegistry, when one is in play
        self.data_dir = Path(data_dir) if data_dir else None
        self._displays: dict[str, Display] = {}
        self._default_id: str = DEFAULT_ID

    # -- membership ------------------------------------------------------------
    def add(self, display: Display) -> Display:
        self._displays[display.id] = display
        if len(self._displays) == 1:
            self._default_id = display.id
        return display

    def build_default(self, *, name: str = "", config: Config | None = None) -> Display:
        """The single display an upgrade starts with: the gateway_url the companion
        was already configured with, under the id `default`."""
        return self.add(Display.build(apps_dir=self.apps_dir, id=DEFAULT_ID,
                                      name=name, config=config))

    def build_from(self, record) -> Display:
        """Build the runtime Display for one registry record."""
        return self.add(Display.build(
            apps_dir=self.apps_dir,
            id=record.id,
            name=record.name,
            data_dir=self.data_dir,
            gateway_url=record.gateway_url,
            own_settings=True,
        ))

    def load_registry(self) -> list[Display]:
        """Build one Display per *enabled* record, and take the persisted default.

        A disabled display is deliberately not built: it keeps its settings on disk but
        costs no sync loop, no MQTT connection and no app task.
        """
        if self.registry is None:
            raise LookupError("no registry attached")
        self._displays.clear()
        for rec in self.registry.enabled():
            self.build_from(rec)
        if not self._displays:
            raise LookupError("the registry lists no enabled displays")
        # The default is whatever the user chose and we persisted — never inferred.
        wanted = self.registry.default_id
        self._default_id = wanted if wanted in self._displays else next(iter(self._displays))
        return self.all()

    def all(self) -> list[Display]:
        return list(self._displays.values())

    def ids(self) -> list[str]:
        return list(self._displays)

    def get(self, display_id: str | None) -> Display | None:
        if not display_id:
            return None
        return self._displays.get(str(display_id))

    def remove(self, display_id: str) -> Display | None:
        """Drop a display from the running set. The caller stops it first — this only
        forgets it. Never leaves the default pointing at a display that is gone."""
        d = self._displays.pop(str(display_id), None)
        if d is not None and self._default_id == display_id and self._displays:
            self._default_id = next(iter(self._displays))
            log.info("default display was removed; it is now %r", self._default_id)
        return d

    # -- the default is a CHOICE, never an inference ---------------------------
    @property
    def default_id(self) -> str:
        return self._default_id

    @property
    def default(self) -> Display:
        d = self._displays.get(self._default_id)
        if d is None:                       # only if someone removed it
            if not self._displays:
                raise LookupError("no displays configured")
            d = next(iter(self._displays.values()))
        return d

    def set_default(self, display_id: str) -> Display:
        """Which display the legacy, display-less surfaces resolve to: the bare
        /api/... routes, /local-api/message (a Vestaboard client sends no id), an MCP
        call with no `display` argument, an existing HACS entry. Explicit on purpose —
        inferring it from "whatever is on screen" would surprise people later."""
        if display_id not in self._displays:
            raise KeyError(display_id)
        self._default_id = display_id
        if self.registry is not None:      # a choice this deliberate must survive a restart
            self.registry.set_default(display_id)
        log.info("default display is now %r", display_id)
        return self._displays[display_id]

    def adopt_default(self, display_id: str) -> None:
        """Follow the registry's stored default, without writing it back. Used after a
        removal, where the registry has already picked the survivor — the two must not
        pick differently, or the runtime would drive a different wall from the one the
        file says is the default."""
        if display_id in self._displays:
            self._default_id = display_id

    def status(self) -> dict:
        """Everything the UI's display switcher needs, in registry order."""
        return {
            "default": self.default_id,
            "displays": [d.status() for d in self.all()],
        }

    # -- the resolution seam ---------------------------------------------------
    def current(self, request=None) -> Display:
        """The display this request is about: ``?display=<id>`` when present, else the default.
        An unknown id raises rather than silently driving the wrong wall — so a URL with no
        ``display`` param keeps meaning the default, and every script / ha-vestaboard client / HACS
        entry keeps working untouched."""
        if request is not None:
            requested = None
            try:
                requested = request.query_params.get("display")
            except Exception:
                requested = None
            if requested:
                d = self.get(requested)
                if d is not None:
                    return d
                # An unknown id must not silently drive the wrong wall.
                raise KeyError(requested)
        return self.default
