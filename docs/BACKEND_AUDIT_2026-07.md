# Backend audit — July 2026

> **Status (executed, commits `aef6174`→`b3a0686`):** sections A-F done in
> full, including both structurals (E1 router split: main.py 1798→916 lines
> into `routes/`; E4 conftest with an autouse network guard) and the CI from B
> (tests + hassfest + HACS validation, green). Deliberately left: the optional
> app.js file split (E6 note), and the LICENSE/topics HACS checks are skipped
> in CI pending a licensing decision — see the comments in ci.yml. Two later
> amendments: A4's non-root `USER` was considered and NOT adopted (the
> Dockerfile documents why), and §D's `skip_rotation` has since been deleted.

Companion audit of everything *except* apps/ (audited separately, see
[APP_AUDIT_2026-07.md](APP_AUDIT_2026-07.md)): the FastAPI backend, the web UI,
the display engine, the HACS integration, packaging/CI, and the test suite.
Method as before: a mechanical scan, then seven parallel deep reviews (API
surface, plugin runtime, display pipeline, helpers, frontend, integrations &
packaging, tests), every claim verified against the code — several by executing
it. Findings are a work list, ordered by what it buys. **S/M/L** = effort.
Line refs are as of `v2.5.0-beta.1`.

---

## A. Security (fix first — all Small)

1. **Manifest strings reach `innerHTML` unescaped.** app.js:429-438 (app tiles)
   and :577 (settings labels) interpolate `name`/`description`/`icon` from
   manifests; the server passes them through (plugins.py:770) and appaudit.py
   audits only Python. So an uploaded **channel** app — the kind that is
   "data-only, safe" — can carry `"name": "<img onerror=…>"` and run script in
   the companion origin, which also serves the gateway proxy and the MCP /
   Vestaboard tokens. `esc()` already exists at app.js:89; apply it.
2. **Uncapped zip extraction on app upload.** plugins.py:1148 `extractall` has
   no decompressed-size or entry-count cap; the 8 MB route cap (main.py:1245)
   is compressed-only, so a ~1000:1 bomb expands to ~8 GB in a tempdir that is
   tmpfs (= RAM) in Docker. Sum `ZipInfo.file_size` and cap entries first.
3. **`GET /api/config` returns live secrets.** `_redact` (main.py:162-167)
   masks only `mqtt.password`; `vestaboard.api_key`, the enablement token and
   `mcp.token` go out unredacted.
4. **Docker runs as root; the image ships the test suite.** No `USER` in the
   Dockerfile; `.dockerignore` misses `backend/tests/`, `.pytest_cache` (whose
   churn also invalidates the COPY layer on every local build).
5. **Workflow actions pinned by tag, not SHA**, with `packages: write`.

## B. Process — the highest-leverage finding

**No CI runs the test suite.** `.github/workflows/` contains only
publish-image.yml. The 3766 tests — including the guards that exist precisely
to block release drift (channel parity, changelog headings, image-tag-exists) —
fire only if someone remembers to run pytest before tagging. One `ci.yml`
(pytest + hassfest/HACS validation on push/PR) closes it. Related, same class:

- **scripts/publish-image.sh can move `:latest` onto a beta** — it re-implements
  the workflow's tagging with `TAG_LATEST=1` unconditional and no prerelease
  detection. Port the channel logic or cut it down to `--local`.
- **backend/VERSION is a dead, already-drifted twin** (says 2.4.0; nothing
  reads it — `app/__init__.py` reads the repo-root file). Delete.
- **Stable add-on text drifted**: addon/config.yaml:73-77 still says "until 1.9
  goes stable"; addon-beta/config.yaml:14-19 claims the stable folder doesn't
  exist. The parity test pins `options`/`schema` only — extend it to
  translations, fix the comments.
- **One test needs live internet**: test_plugins.py:686 renders the real metals
  app unstubbed — 11 outbound calls per run (gold-api, frankfurter, Nominatim)
  and it *fails offline*; the only such test in the suite. Stub it like
  test_app_layouts.py's `stub_net`, then add an **autouse socket guard** in
  conftest so the class can't recur.

## C. Correctness bugs

| Where | Bug | Effort |
|---|---|---|
| engine.py:289-291 + 376/446, 493 | Unchanged-page suppression is never invalidated: `_emit_page` swallows send failures yet `last_sent` is still set, and `fire_interrupt` doesn't reset it — a gateway reboot mid-rotation or a trigger over a static-page app leaves the wall stale/showing the trigger text **indefinitely** | S (fix in both loops, or extract the loop first — see E) |
| main.py ×6 (1072, 390-397, 978/1009, 1324, 1122-1127, 1110) | Module-global default-display aliases used where the request's display was meant: dev settings pull/push targets the **default wall's gateway** while writing display X's store (pull can overwrite X with wall 1's blob); every heartbeat carries the default wall's status; VB/MCP dev toggles silently no-op on non-default walls; `triggers_get` reads the default scheduler; per-display lang/locked can disagree; forced push drops the `displays` registry backup from the doc | S each; root fix in E |
| weather.py:597,636 | Cache hits hand the **same dict** to every app across executor threads — one mutating app poisons weather for all apps for up to the TTL. Deepcopy on store/hit | S |
| plugins.py:618-620, 973, 239/1103 | Fetch-lock creation is check-then-set (duplicate fetches defeat coalescing — use `setdefault`); `save_settings` pops only the bare app cache so override-keyed playlist entries keep pre-edit pages; `load()`/global-save clear caches while a fetch in flight re-caches stale results | S |
| scheduler.py:67 | Failure backoff uses `min(interval·2ᶠ, 600)` — a failing trigger with a 3600 s interval gets polled **6× more often** than a healthy one. `max()` | S |
| renderer.py:330 | `for_text`'s reverse color map tests the wrong variable and is always `{}` (verified by execution): MCP `get_display` leaks raw U+E000-06 PUA chars instead of 🟥 tiles | S |
| engine.py:494-507, 79 | A manual message sent during a trigger interrupt gets **blanked** when the interrupt ends; consecutive `show_temporary`s queue instead of replacing; `stop()` never cancels `_temp_task` | M |
| engine.py:276-280 + rest.py:180 | The batch path discards `Step.delay_after`, and the cells path's `dict(frames)` dedupes duplicate module ids — the slot style's spin-hold vanishes on a Matrix Gateway | M |
| custom_components | api.py has **no ClientTimeout** (default 5 min — hangs config flow, stacks polls) and catches only ClientError; coordinator.py:36-39 fetches the grid **once per HA run** (a resized wall garbles sensors/PNG until HA restarts); unique_ids are entry_id-based (remove+re-add orphans history — the flow already computes a stable id); coordinator built without `config_entry` kwarg (deprecation) | S each |
| location.py:51-97 | No negative caching: an un-geocodable ZIP or a Nominatim outage costs two blocking 6 s calls on **every** fetch, from every location app, against a 1-req/s-policy service | S |
| discovery.py:141-162 | mDNS resolves are fire-and-forget and the browser closes before they land (timeout is also only half the window) — late answers silently dropped; gather before close | S |
| gwproxy.py:150, 177-179 | New TCP connection per proxied request against a ~4-socket ESP32; and 3xx `Location` passes through unrewritten, so a gateway redirect escapes `/gw/` into the SPA | S |
| main.py:917-924, 1310 | `ConfigPatch.grid` accepts a non-numeric `rows` and poisons in-memory config (500s `/api/grid` until restart); playlist `entries` untyped — a non-dict entry 500s and **persists** | S |
| main.py:1096, 554 | `plugins.load()` (re-executes every app.py) runs on the event loop from dev-pull and from `POST/PATCH /api/displays` — the code's own comment says reloads belong in an executor | S |
| main.py:1558/1583 | Vestaboard routes resolve the display before the enablement guard — a disabled layer still enumerates display ids via 404 bodies | S |
| gateway.py:25 | `_settings_transfer` is process-global: one wall's settings push pauses sends to **every** display, and two concurrent transfers race the set/clear | S |
| app.js:1593, 158, 1559 | 300 ms poll with no in-flight guard (out-of-order repaints on slow links; stale wall state can land after a display switch); unguarded `/api/health` aborts all of `init()` for a cosmetic version string; several actions (`saveTriggers`, `runApp`, playlist save/delete) have no catch — failure gives zero feedback | S |

## D. Dead code (grep-verified)

- `_wants_weather/_wants_location/_wants_i18n/_wants_caps` (plugins.py:123-124,
  247-248, 413-415) — production reads now go through `_helper_kwargs`; only
  tests read the dicts. Unify: precompute one frozenset of wanted helpers per
  (app, fn) at load, drive `_helper_kwargs`, `_uses_location`, and the schema
  readers from it, delete the four dicts. **M**
- `POST /api/gateway/sync` (main.py:934) — zero consumers; exact duplicate of
  `/api/dev/resync`. `GET /weather` (main.py:1376) — zero consumers, and
  `weather.fetch_current` dies with it. **S**
- `transport.mqtt.prefix` — written by three sources (config.py:45,167;
  gateway.py:369), documented in docker-compose, read by nothing. **S**
- `gateway.supports_cells` (only its own test); `page_timing`'s
  `skip_rotation` key (produced, never consumed — same lever as app-audit A3:
  wire it or delete both ends); `device.settle_ms` (parsed, stored, never read
  — keep only if declared reserved); `weather._BANDS`; scripts/case_data.py
  (finished one-shot migration); css orphans (`.gwlink`, `.badge.ok/.warn`,
  `.grid2/.grid3`, `.days`, `fieldset/legend`, `.btn.sec`); `$("plSaved")`
  no-op (app.js:983); function-local `import re` shadow (gateway.py:255); stale
  doc refs (state.py:35 → `engine._show_current`, rest.py:169 →
  `renderer.colorize`). All **S**.

## E. Structure & reuse

1. **Thread `d` (the display) through the six default-alias helpers** — this is
   the prerequisite that *is* the fix for the C cross-display cluster — then
   split main.py (1776 lines) into routers along its real seams: displays /
   dev / apps / playlists+triggers / message / vestaboard. gwproxy already
   proves the `build(displays)` router pattern. **M-L, worthwhile.**
2. **Extract the page cycle** shared by `_app_loop` and `_playlist_loop`
   (engine.py:358-380 vs 430-450) — the stale-suppression bug must otherwise be
   fixed twice. **M**
3. **plugins.py seams**: the upload/install block (~190 lines → `installer.py`)
   and the settings-schema block (~250 lines). Fetch/caching + settings
   assembly are entangled with the splitflap-os contract — leave them. **M each**
4. **tests/conftest.py** — quantified duplication: `APPS` constant ×14 files,
   the `spec_from_file_location` app loader ×6, PluginRuntime builders ×13 (one
   via `__new__`, which breaks silently when `__init__` gains state), four
   byte-identical httpx `_FakeClient`s. Three fixtures (`load_app`,
   `make_runtime`, `stub_http`) + the autouse socket guard from B. ~250 lines
   removed. **M**
5. **Small dedup batch (all S)**: atomic write-fsync-replace (registry.py:146
   vs plugin_settings.py:236); `slugify`+`DEFAULT_ID` (registry.py:52 vs
   display.py:47); `lat,lon|name` parsing ×2 (plugins.py:531 vs 1077);
   per-app-language expression ×2 (plugins.py:457/605); one `_read_manifest()`;
   `resize_grid` trio ×4 in main.py; `_persistent_secret()` for the VB/MCP
   key-minting twins (main.py:985-1029/1493-1648); gateway.py's five one-shot
   HTTP helpers → one pooled per-gateway client; i18n/uilang shared
   `base_lang()`/fallback-chain core (written 6×/2×); weather `_forecast_entry`
   ×4 and `_fetch_air` split per provider; MCP `show_message` vs main.py's
   (parallel 15-line implementations).
6. **Frontend (S-M)**: one `buildForm(schema, values)` for the three settings
   dialogs (removes the `_formFields` global — do this before any file split);
   a VB/MCP toggle factory; `del()`/`patch()` fetch helpers; fold `pollStatus`
   into `pollState` with an in-flight guard; keyboard access + dialog
   semantics for tiles/chips/modal (**M**); `api()` should surface the server's
   JSON `detail`. app.js is already `type="module"` — a native-import split of
   the self-contained modules (settings engine, tools, displays) is viable but
   optional.
7. **Readability (S)**: weather.fetch_weather (65 lines → 3 functions);
   config._env_overrides table-driven; config.py docstrings updated to the real
   five merge layers + tests for the two unpinned precedence properties
   (gateway-sync-below-env, identity-above-env); uilang shouldn't cache a
   failed catalog read as `{}` forever.

## F. Perf (behind correctness in priority)

- updateActiveUI rebuilds the running-app banner via innerHTML and walks all
  tiles every 300 ms with identical content — diff before writing; same for the
  per-cell board writes. **S**
- plugins re-reads the central per-language app-i18n JSON once per app per
  `/api/apps` request (~4 reads × N apps); an mtime cache makes it free, and
  the same helper covers `available_list`'s manifest re-reads. **S**
- The legacy `/api/rs485/batch` path has no `_shown` diffing (cells path has
  it): a one-digit clock tick resends the full board — cost is gateway
  occupancy, not flap wear (unchanged modules don't flip). **M**
- engine busy-polls (0.05 s settings wait, 0.1 s interrupt park) → asyncio.Event;
  scheduler runs triggers serially — one slow trigger delays the tick. **S**
- HACS coordinator polls state+apps+playlists every 5 s per display — lists can
  poll slower. **S**
- Unbounded caches: `_first_error` (also never reset on success — stale error
  echoes), `_fetch_locks`/`_caches` per override combo, location/weather dicts.
  Bound or prune on `load()`. **S**

## G. Flagged by a reviewer, rejected on evidence

- `custom_components/splitflap/brand/` "dead" — **wrong**: HA ≥2026.3 serves
  brand icons from the integration's own `brand/` folder (the home-assistant/
  brands PR was closed for exactly this reason). Keep.
- appaudit.py "vestigial" — load-bearing: it gates the upload path
  (plugins.py:1173/1179); built-ins skip it by design.
- The numpy<2.4 pin, addon/addon-beta `options` duplication (pinned by the
  parity test), MCP server built while disabled, Vestaboard 201/plain-401
  byte-compat, `PluginSettings` deepcopy-per-get, the 0.2 s sleep and ~1 s
  poll-tests in the suite, `_openmeteo`'s unused params (dispatch-table
  signature), and the renderer's char/cells split (two thin wire adapters, one
  pipeline) — all deliberate and documented in place.

## H. Suggested execution order

1. **Security + CI day** (A1-A3, B's ci.yml + publish script + socket guard +
   metals stub) — all S, and CI then protects everything after it.
2. **Correctness sweep** (C) — engine suppression/interrupt fixes via the E2
   extraction, the six cross-display fixes via E1's `d`-threading, weather
   deepcopy, lock races, scheduler backoff, for_text, HACS timeout/grid/ids,
   location negative cache, discovery gather, gwproxy client+redirects, input
   validation.
3. **Dead-code + small-dedup batch** (D, E5) — mechanical, test-protected.
4. **conftest consolidation** (E4).
5. **Structural** (E1 router split, E3 installer/schema extraction, E6
   frontend form engine + a11y) — each independently shippable.
6. **Perf** (F) as opportunity allows.
