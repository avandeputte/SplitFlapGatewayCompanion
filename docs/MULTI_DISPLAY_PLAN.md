# Plan: one companion, many displays

Today the companion is **one process, one gateway, one wall**. The goal: run several
gateways side by side — each with its own geometry, settings, apps, playlists and
triggers — and switch between them in the UI.

## What actually stands in the way

Not the feature; the *singletons*. Everything is created once at import time in
`main.py`:

```python
config      = Config()                       # grid, transport, gateway_url, display
state       = DisplayState(config.module_count())
controller  = DisplayController(config, state)     # active_app, active_playlist, _task
plugin_settings = PluginSettings(config.data_dir)  # ONE app_settings.json
plugins     = PluginRuntime(config, plugin_settings, ...)
scheduler   = Scheduler(controller, plugins)
ha          = HomeAssistant(config, plugins, controller)
mcp         = mcp_server.build(config, state, controller, plugins, ...)
```

51 endpoints close over those names (`config.` ×57, `controller.` ×37, `plugins.` ×29).
`gateway.py` keeps module-level `_gateway_tabs`. The engine persists a single
`last_run`. `GATEWAY_URL` is required at boot and *is* the identity of the one display.

So the work is **90% de-singletonisation, 10% new feature**. The risk isn't the
concept, it's touching 51 endpoints and every consumer at once.

## The shape

A **Display** is the unit: a gateway URL + its config + its settings store + its
runtime. One object owns what is today six module-level names.

```python
class Display:
    id: str                 # stable slug, e.g. "kitchen"
    name: str               # "Kitchen wall"
    config: Config          # grid, transport.gateway_url, display, vestaboard, mcp, ha
    state: DisplayState
    controller: DisplayController
    settings: PluginSettings     # its own app_settings.json
    plugins: PluginRuntime
    scheduler: Scheduler
    ha: HomeAssistant
```

A **DisplayManager** owns the set, the default/active id, and lifecycle (add, remove,
start, stop, resync). `main.py` keeps *one* module-level name: `displays`.

### What is per-display and what is shared

**Every setting is per display. There is no shared settings store.** This is forced by
the storage model, not chosen for tidiness, and it is the decision that shapes the rest:

> The **gateway is the backup** for its wall's settings. `setup_settings_sync` mirrors a
> display's whole settings doc onto its gateway (3.1+), and a rebuilt host with no local
> file **restores from it**. So a setting that does not live in a display's own store has
> no gateway to live on — it can never be backed up, and never recovered.

A `globals.json` holding the credentials for all displays was tried and reverted for
exactly this reason: it was a companion-local file with no home on any gateway, i.e. an
invisible hole in the recovery story. The cost of the rule is entering an API key once
per wall. That is the price of every wall's settings being recoverable from its own box.

| Per display (its own store → its own gateway) | Shared |
|---|---|
| `gateway_url`, MQTT broker/password | the **app library on disk** (`apps/`, uploads) |
| `grid` (rows/cols/module_id_base) | the **UI language** and its catalogs |
| `display` (transition style/speed) | `app_i18n` catalogs |
| `installed_apps` | |
| `saved_app_playlists`, `triggers` | |
| all `plugin_<id>_<key>` app settings | |
| `active_app`, `active_playlist`, `last_run` | |
| Vestaboard key, MCP token | |
| **every catalog global** — `zip_code`, `timezone`, `language`, `global_loop_delay`, **and the credentials** (`weather_api_key`, `yt_api_key`, `weather_provider`) | |

Adding a display **copies** the global settings from an existing one so nobody retypes an
API key — but it is a copy, not a link: from that moment they are the new display's own,
and they ride to the new display's gateway like everything else it has. (Skipped when the
new gateway already holds a settings blob of its own; that blob wins.)

## Phases

Each phase ships and is useful on its own. Nothing here is a big-bang rewrite.

### Phase 0 — make one display an object (no behaviour change) ✅ DONE

Move the six singletons into `Display`, instantiate exactly one, and have `main.py`
resolve it per request. Endpoints change from `controller.run_app(...)` to
`d = displays.current(request); d.controller.run_app(...)`. The API is unchanged, the
tests are unchanged, and nothing in the UI moves. **This is the whole risk of the
project, taken once, with a green suite either side.**

Also here: `gateway.py`'s module-level `_gateway_tabs` becomes per-display state.

**Landed.** `display.py` holds `Display` (config, state, controller, settings, plugins,
scheduler, ha, gateway_tabs) and `DisplayManager`. `main.py` builds exactly one through
the manager and keeps the old module names as aliases *to that display's objects* — the
lifespan and background loops still need them, and they are the same instances, so there
is one source of truth. All 39 display-touching routes now resolve through
`display_for(request)`; a test walks main.py's AST and fails if any route reaches for a
module global instead. The per-wall lifecycle went with it: `do_gateway_sync`,
`resume_last_run`, `_remember_driver`, `_companion_heartbeat`, `setup_settings_sync` and
`_settings_flush_loop` all take a display.

**The settings blob is per gateway** — each display mirrors its own store to its own box
(`url = d.gateway_url`), so a second wall can never overwrite the first's installed apps,
playlists or triggers.

**The default is explicit** (`DisplayManager.set_default`), never inferred from what is
currently on screen. Phase 1 persists it.

Two things deliberately still resolve to the default and want a decision in Phase 2:
the **UI chrome language** (level 2 of the uilang chain reads a settings store — which
display's?) and the **MCP/Vestaboard** surfaces (Phase 3 gives them an explicit display).

### Phase 1 — storage and identity ✅ DONE

- `data/displays/<id>/app_settings.json` per display — the whole store, credentials
  included, so all of it can be mirrored to (and restored from) that display's gateway.
  Migration on first boot: an existing `app_settings.json` becomes display `default`,
  wholesale, with nothing split out.
- `data/displays.json`: the registry (`id`, `name`, `gateway_url`, `enabled`, `order`,
  and the persisted `default_display`).
- `GATEWAY_URL` env / `gateway_url` add-on option stays, and *seeds* display `default`
  when the registry is empty — so an existing install upgrades with zero config and
  the add-on's single required option keeps working.
- Per-display uploaded apps? **No.** Uploads stay shared (`data/apps/`); which apps are
  *installed* is per display. Otherwise the same zip lives twice.

**Landed.** `registry.py` holds `DisplayRecord` + `DisplayRegistry` + the migration;
`plugin_settings.py` gains a per-display settings path. `DisplayManager.load_registry()`
builds one Display per *enabled* record — a disabled one keeps its settings but costs no
sync loop, no MQTT device and no app task.

**Everything is per display, credentials included** — see the storage rule above. A
`globals.json` for shared credentials was built and then reverted: a companion-local file
has no gateway to live on, so nothing in it could ever be backed up or restored, which
would have been an invisible hole in the recovery story. Adding a display copies the
globals from an existing wall (a copy, not a link) so nobody retypes an API key.

**The migration does not destroy anything.** The old `app_settings.json` is *copied*,
never moved — this is one-way, and a 1.x companion cannot read the new layout, so the
one unforgivable bug would be losing the only copy of settings someone spent an evening
building. It is idempotent, and seeding globals.json fills blanks only.

**The add-on option still owns display `default`.** `GATEWAY_URL` / the add-on's
`gateway_url` is re-adopted on every boot (`adopt_env_gateway`), because that
Configuration tab is where a Home Assistant user has always set their gateway: if the
registry silently outranked it, someone correcting a typo'd IP there would watch nothing
happen and have no way to tell why. Displays 2..n are the registry's (and the UI's).

A display's `gateway_url` outranks even the env inside its own `Config`, because it is
not a preference — it is *which wall this object is*. Otherwise `GATEWAY_URL` would drag
every display onto one gateway and the second wall would drive the first.

**Adding a display brings it up immediately** — no restart. `POST /api/displays` builds
it and starts its runtime; `DELETE` stops it and forgets it, leaving its settings on
disk so re-adding the same id gets its playlists back. Registry API: `GET /api/displays`,
`POST /api/displays`, `PATCH /api/displays/<id>`, `DELETE /api/displays/<id>`,
`POST /api/displays/<id>/default`.

Re-pointing a *running* display at a different gateway is the one thing still deferred to
a restart: the settings mirror, the HA device and the heartbeat are all bound to the old
URL, and swapping it underneath them is how you mirror one wall's playlists onto
another's box.

### Phase 2 — routing and the switcher ✅ DONE

- Every display-scoped endpoint takes the display id. Two options, and the choice
  matters for the HACS integration and any scripts people have:
  - **A. Path prefix** — `/api/d/<id>/apps/run`. Explicit, cacheable, obvious.
  - **B. Query/header** — `/api/apps/run?display=<id>`, defaulting to the current one.
  - **Recommendation: B with A as an alias.** Existing URLs keep working against the
    default display, so every script, the Vestaboard clients and the HACS integration
    survive the upgrade untouched; new callers can be explicit. This is the same
    "old callers keep working" discipline the channel-app sidecars used.
- The SPA gets a display picker in the header (next to the ⚙). Switching sets the
  active display, re-fetches grid/apps/state, and re-points the gateway proxy tabs.
- `/gw/` proxy becomes `/gw/<display-id>/`, since it targets *that* gateway.

### Phase 3 — the surfaces that assume one wall ✅ DONE

Each needs a deliberate decision, and each is a place a naive implementation breaks:

- **Vestaboard API** (`/local-api/message`): a Vestaboard *is* one board, and clients
  send no display id. Keep `/local-api/*` bound to the default display, and expose
  `/local-api/<display-id>/message` for the rest. Per-display keys already exist in the
  model. Anything else breaks `ha-vestaboard`.
- **MCP** (11 tools): add an optional `display` argument to each tool, defaulting to the
  current one, plus a `list_displays` tool. An LLM asking "what's on the kitchen wall?"
  is exactly the kind of thing this unlocks — but a tool that *requires* the argument
  would regress every existing prompt.
- **MQTT / HA device** (`homeassistant.py`): `node_id`/`topic_prefix` are per-display
  already in the config tree — one HA device per display, ids suffixed with the display
  id. The gateway publishes its own device per gateway, so this lines up.
- **HACS integration**: today one config entry per companion URL, with `App`/`Playlist`
  selects. Becomes one **device per display** under a single entry (the coordinator
  fans out over `/api/displays`). `splitflap.message` gains an optional `display`
  target. Existing entries must keep working against the default display.
- **Scheduler / triggers**: per display (they live in the per-display settings). One
  scheduler task per display, not one global one.
- **Resume after restart**: `last_run` is per display already once settings split.

### Phase 4 — the UI ✅ DONE

- Header switcher; the live preview, Compose, Playlists and Triggers all follow the
  active display. Compose's grid comes from *that* display's geometry.
- A **Displays** settings page: add/remove/rename, gateway URL, test connection,
  reorder, pick the default.
- Nice-to-have, later: "push this message to all displays" and mirroring one display
  onto another. Explicitly out of scope for v1.

## Risks and the honest cost

- **The 51-endpoint refactor is the whole project.** Phase 0 exists precisely so that
  lands separately, provably behaviour-neutral (the existing 347 tests are the check).
- **Gateway registration is bidirectional**: each gateway registers *this* companion's
  URL and advertises its tabs. With N gateways the companion registers with each, and
  the tab strip must show the *active* one's tabs. `_gateway_tabs` becoming per-display
  is what makes that correct rather than last-writer-wins.
- **One process, N gateways** means N sync loops, N MQTT connections, N app-loop tasks.
  Fine for a handful; each display's `PluginRuntime` also holds its own fetch caches, so
  two displays running Weather fetch twice. A shared fetch cache keyed by
  (app, resolved settings) would fix that — worth doing only if it bites.
- **Data migration is one-way.** Downgrading to 1.x after the split leaves a companion
  that can't read its own settings. Ship the migration behind a version stamp in
  `displays.json`, and keep the pre-migration `app_settings.json` untouched as a backup.

## Suggested order

| Step | Ships | Size |
|---|---|---|
| 0 | `Display` object, one instance, API unchanged | large refactor, no user-visible change |
| 1 | per-display storage + migration + registry | medium |
| 2 | display-scoped API + UI switcher | medium |
| 3 | Vestaboard / MCP / MQTT / HACS per display | medium, mostly decisions |
| 4 | Displays management page, polish | small |

Phase 0+1 is the point of no return; 2 is where it becomes a feature; 3 is where it
stops surprising anyone's existing automation.


---

## What landed (Phases 2-4)

**Routing.** `?display=<id>` on any `/api/…` route, resolved by the one seam
(`display_for`). Omitted, it means the default display, so every existing URL, script,
Vestaboard client and HACS entry keeps working untouched. An unknown id is a **404**, not
a silent fallback — driving the wrong wall is worse than failing.

**The SPA follows one variable.** `url()` appends the active display to every `/api/` call
— the client-side twin of `display_for`, so switching walls is one variable rather than a
change at ~40 call sites. Switching re-reads the grid, apps, playlists, triggers and the
gateway's tabs, because all of them belong to a wall. The chosen wall is remembered per
browser. **With one display the switcher is hidden entirely**, so the overwhelming
majority of installs see no new chrome at all.

**The gateway proxy is addressed by PATH** (`/gw/<id>/…`), not a query param: it rewrites
the proxied page's own links to a base, so `?display=` would be dropped on the first click
*inside* the gateway's page and the next request would land on the default wall. A bare
`/gw/…` stays the default display, so existing bookmarks and the gateway's own absolute
links keep working.

**MQTT / Home Assistant: one device per wall.** `node_id` and `topic_prefix` come from
config, which is identical for every display — two walls would have published to the same
topics under the same device identifier and the second one's discovery would have
overwritten the first's. Each display now gets its own, **except the default, which keeps
the historic unsuffixed ids**: suffixing it would orphan every existing entity and
silently break any automation pointing at `select.splitflap_companion_app`.

**Vestaboard.** `/local-api/message` stays bound to the default display — a Vestaboard *is*
one board, and every existing client (ha-vestaboard included) posts to that fixed path with
no way to name a wall. `/local-api/<display-id>/message` addresses the others.

**MCP.** Every tool takes an optional `display`; omitted, it is the default. Requiring it
would have regressed every prompt ever written against this server. A new `list_displays`
tool is how an agent finds the others.

**`GATEWAY_URL` takes a comma-delimited list** — `http://kitchen,http://office` — so a Home
Assistant user configures both walls from the single Configuration-tab option they already
have. The first entry owns display `default`. Entries only ever **add**: a display created
in the UI is never removed because it stopped appearing in the env, which would take its
playlists and triggers with it. (The add-on schema had to move from `url` to `str`; the
`url` validator rejects a list outright.)

**Displays page** in the Tools menu: add, rename, re-point, remove, choose the default.
Adding a wall starts it immediately — no restart — and copies the global settings from an
existing one so nobody retypes an API key. Removing one leaves its settings on disk.

### Still open

- **The HACS integration lives in another repo** and is unchanged. Its existing config
  entries keep working: they address no display, so they drive the default one. Giving it a
  device per display means teaching its coordinator to fan out over `/api/displays` and
  adding an optional `display` target to `splitflap.message` — a change to make there, not
  here.
- **Re-pointing a running display at a different gateway needs a restart.** The settings
  mirror, the HA device and the heartbeat are all bound to the old URL, and swapping it
  underneath them is how you mirror one wall's playlists onto another's box. Add/remove and
  enable/disable are all live.
- **UI chrome language** still resolves from the default display's settings (level 2 of the
  uilang chain). With N walls there is no obviously right answer, and it is one language for
  one browser, so it stays as it is until someone asks.
- **Shared fetch caches.** Two displays running Weather fetch it twice; each `PluginRuntime`
  holds its own caches. Worth a shared cache keyed by (app, resolved settings) only if it
  bites.
