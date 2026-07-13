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
  page is `rows × cols` chars), with **one deliberate divergence**:

  > **Vertical centering.** When an app returns fewer lines than the wall is tall,
  > splitflap-os pads only at the bottom; we pad above and below. On a 3-row wall —
  > what splitflap-os targets — the two are identical, because apps fill it. On a
  > taller wall (a 5×15 MatrixPortal, say) bottom-padding strands a 3-line app at the
  > top with two dead rows beneath it. An app is unaffected either way: it still
  > returns the same lines, and `get_rows()` still tells it how much room it has.
  >
  > **The divergence is opt-out.** An app that wants the original behaviour — or that
  > builds its own layout and needs its rows left where it put them — declares it in
  > its manifest:
  >
  > ```json
  > { "vertical_align": "top" }      // "center" (default) | "top" | "bottom"
  > ```
  >
  > `"top"` is byte-for-byte splitflap-os padding. The key is additive: absent means
  > `"center"`, so every existing app — and every unmodified splitflap-os app — behaves
  > exactly as it does today without being touched. `format_lines`'s **signature does not
  > change**: the runtime hands each app a `format_lines` already bound to that app's
  > alignment, so apps keep calling `format_lines(*lines)`.
  >
  > Declaring `"top"` is what an app needs if it places its own blank rows. Without it,
  > an app that centres its own block gets centred a *second* time and drifts below the
  > middle — which is exactly what happened to three vendored apps in 1.9.0-beta.5.
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
- **Rendering** — the emoji→colour-code map and all transition orderings (apps
  emit colour codes and expect the same animation styles).
- **Caching / paging** — results cached per `refresh_interval`; each page shown
  `loop_delay` seconds.

## Where the companion intentionally differs

Final character normalization is **companion-specific** and deliberately *not* a
port of splitflap-os. Because the gateway and modules support the full
**Windows-1252** set, the companion sends accented letters, `€` and punctuation
through verbatim (upper-cased in a cp1252-aware way, so `ß` and accents survive)
instead of policing them against a fixed `FLAP_CHARS` set or substituting the
currency `$`/`"`→`q`. This changes only the *final glyphs on the wire*, never what
an app sees — the plugin ABI above is still identical in both directions, so apps
stay interchangeable with stock splitflap-os.

## How it's enforced

- `backend/tests/test_renderer.py` guards normalization (cp1252-aware
  upper-casing + verbatim Windows-1252 passthrough) and the animation orderings
  (a drift here is a compatibility regression).
- `backend/tests/test_plugins.py::test_every_app_loads` imports every `apps/*`
  and asserts its manifest + `fetch`/`data` satisfy this contract. The manual
  drop-in check still holds: copy a not-pre-vendored splitflap-os app into
  `apps/` and confirm it appears, configures, and runs with no changes.

## When upstream changes

If splitflap-os adds a field type, helper, or endpoint, bump the pinned commit
above, port the addition, and extend the conformance test. Never diverge the ABI
silently.
