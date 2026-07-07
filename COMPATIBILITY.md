# Plugin compatibility contract

**Goal:** any app folder from
[splitflap-os](https://github.com/csader/splitflap-os) drops into this project's
`apps/` and works **unmodified**, and any app authored here runs on a stock
splitflap-os. The companion's engine and UI are original, but the **plugin ABI is
a faithful, behavior-identical port** — that compatibility is a feature, not a
coincidence, and this document is the contract we hold it to.

**Pinned upstream:** splitflap-os commit
`12df2773cbbe9890a7d6f92fdc60d2be920129bd` (`VERSION` 0.3.0).

## What must stay identical

- **Layout & loading:** `apps/<id>/{manifest.json, app.py}` (+ bundled data /
  submodules). The loader makes each app's own directory importable so relative
  imports and data files resolve. Both `functional` (has `app.py`) and `channel`
  (data-only) app types load.
- **`fetch(settings, format_lines, get_rows, get_cols) -> list[str]`** — same
  argument order and semantics. `format_lines(*lines, cols=None)`,
  `get_rows()`, `get_cols()` are behaviour copies (padding/centering/width; each
  page is `rows × cols` chars).
- **`settings` dict** — manifest defaults merged with saved user values, same key
  resolution, including any global keys apps read (e.g. `currency_symbol`).
- **Triggers** — `trigger(settings, conditions) -> bool` on a
  `trigger_interval`, with `trigger_display_seconds` / `trigger_cooldown` /
  `trigger_conditions`, and the `setattr(fetch, '_state', …)` state pattern.
- **Manifest schema / settings fields** — `text, number, password,
  datetime-local, textarea, select, toggle, search_chips, computed,
  inline_toggle`, plus `visible_when`, `sync_values` / `sync_parent`, `stepper`,
  string/object `options`, and the `LUCIDE_APP_ICONS` opt-in.
- **`search_chips` `searchUrl` endpoints** — served at the same paths with the
  same response shapes: `/sports_leagues`, `/sports_teams/<league>`,
  `/sports_follow`, `/location_search`, `/location_timezone`, `/timezones`,
  `/stocks_search`, `/crypto_search`.
- **Rendering** — `FLAP_CHARS`, the emoji→colour-code map, the currency `$`
  alias, `"`→`q`, and all transition orderings.
- **Caching / paging** — results cached per `refresh_interval`; each page shown
  `loop_delay` seconds.

## How it's enforced

- `backend/tests/test_renderer.py` guards the character set, normalization and
  animation orderings (a drift here is a compatibility regression).
- Later phases add a loader conformance test that imports every `apps/*` and
  asserts its manifest + `fetch`/`trigger` signatures satisfy this contract, plus
  a manual drop-in check: copy a not-pre-vendored splitflap-os app into `apps/`
  and confirm it appears, configures, and runs with no changes.

## When upstream changes

If splitflap-os adds a field type, helper, or endpoint, bump the pinned commit
above, port the addition, and extend the conformance test. Never diverge the ABI
silently.
