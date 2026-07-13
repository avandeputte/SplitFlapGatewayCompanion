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

This is the decision that shapes everything else. Grounded in the current code:

| Per display | Shared across displays |
|---|---|
| `gateway_url`, MQTT broker/password | the **app library on disk** (`apps/`, uploads) |
| `grid` (rows/cols/module_id_base) | the **UI language** and its catalogs |
| `display` (transition style/speed) | provider **API keys**? (see below) |
| `installed_apps` | `app_i18n` catalogs |
| `saved_app_playlists`, `triggers` | |
| all `plugin_<id>_<key>` app settings | |
| `active_app`, `active_playlist`, `last_run` | |
| Vestaboard key, MCP token | |

The catalog globals (`zip_code`, `timezone`, `language`, `weather_api_key`,
`yt_api_key`, `weather_provider`, `global_loop_delay`) are the interesting case. A
weather API key is genuinely account-level; a *location* plausibly differs per wall
(kitchen shows home, office shows the office). **Proposal:** keep one
`globals.json` for credentials (`weather_api_key`, `yt_api_key`, `weather_provider`)
and make the rest per-display, with a per-display "follow global" blank — the exact
convention the per-app Language override already uses, so it needs no new UI idiom.

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

### Phase 1 — storage and identity

- `data/displays/<id>/app_settings.json` per display; `data/globals.json` for the
  shared credentials. Migration on first boot: an existing `app_settings.json` becomes
  display `default` (named from the gateway's product string), credentials lifted out.
- `data/displays.json`: the registry (`id`, `name`, `gateway_url`, `enabled`, `order`).
- `GATEWAY_URL` env / `gateway_url` add-on option stays, and *seeds* display `default`
  when the registry is empty — so an existing install upgrades with zero config and
  the add-on's single required option keeps working. Adding displays 2..n is a UI job.
- Per-display uploaded apps? **No.** Uploads stay shared (`data/apps/`); which apps are
  *installed* is per display. Otherwise the same zip lives twice.

### Phase 2 — routing and the switcher

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

### Phase 3 — the surfaces that assume one wall

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

### Phase 4 — the UI

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
