# Plan: a fully multi-lingual companion UI

Where the project stands: the *content* pipeline is localized — functional apps get a
`Localizer`, channel apps pick `data_<lang>.json` sidecars, and locale drives dates,
numbers, currency and holidays. What still speaks English regardless of the Language
setting is the **chrome**: the web UI itself, the settings forms, error pages rendered
to the flaps, and the Home Assistant surfaces. This plan closes that gap.

**Inventory** (measured, not guessed): the SPA is ~1,330 lines (`app.js` + `index.html`)
carrying roughly **100–150 user-facing strings**; the settings catalog has 14 labeled
fields plus option labels; ~6 server-side strings render *onto the display* ("APP
ERROR", "OFFLINE", "NO DATA"…); the HACS integration and the add-on each already have
an English translation file in Home Assistant's own format. This is a small surface —
the work is mostly discipline, not volume.

## Principles

1. **One Language setting rules everything.** The UI follows the existing global
   Language (`i18n_data.json` list) — no separate "UI language" knob unless a user asks.
   Fallback chain everywhere: exact locale → base language → English. Same rule the
   channel apps and fortunes already use, so the whole product degrades identically.
2. **No build step.** The SPA is vanilla JS served statically; i18n must be too: a JSON
   string catalog fetched at boot, not a framework.
3. **English lives in the code.** The English string *is* the key
   (`t("Add to playlist")`), so untranslated strings render fine, diffs stay readable,
   and there is no key-naming bikeshed. A missing translation is never an error.
4. **Translations are data, reviewed like data.** Same pattern as the fortune files:
   one file per language, validated in CI for shape and cp1252, contributed one file at
   a time.

## Phase 1 — string catalog + `t()` in the SPA *(the bulk of the value)*

- `backend/app/static/i18n/<lang>.json`: flat `{"English string": "Übersetzung"}` maps.
- A ~20-line `t(s, ...args)` helper in `app.js`; boot fetches the catalog for the
  effective language (exact → base) and falls through to English on any miss. The
  companion already stamps `window.__BASE__`; stamp `window.__LANG__` the same way from
  `spa_index`, so the UI language is decided server-side with the same precedence rules.
- `index.html`: static strings get `data-i18n` attributes, translated in one DOM pass at
  boot (the file is 141 lines; there are ~14 such strings).
- Interpolation via `t("Delete %s?", name)` — positional only, keep it dumb.
- **Extraction is grep-driven, then frozen by a test**: a script walks `app.js` for
  user-facing literals and writes `i18n/en.json` (identity map). A pytest asserts every
  `t("...")` key in the source exists in `en.json` and that every language file's keys
  are a subset of it — so catalogs can't drift from the code silently.

## Phase 2 — strings the *server* renders

- **Flap-rendered fallbacks** ("APP ERROR", "OFFLINE", "NO DATA", "PLUGIN ERROR"):
  route through the existing `i18n.py` Localizer — its `strings` table in
  `i18n_data.json` is already the home for translated display text (holidays live
  there today). These must respect the flap character set, so translations here stay
  A–Z where possible ("HORS LIGNE", "FEHLER").
- **Settings catalog** (`catalog.py` labels + option labels, and manifest-declared
  app settings): the API that serves the settings form gains a `lang` pass that maps
  labels through the same catalog before returning JSON. App manifests may add
  optional `label_<lang>` keys; absent ones fall back — third-party apps stay valid
  unchanged, same contract philosophy as the `data_<lang>.json` sidecars.

## Phase 3 — Home Assistant surfaces (native mechanisms, not ours)

- **HACS integration**: `translations/en.json` already exists; add sibling files
  (`fr.json`, `de.json`…) — HA picks them by the *HA* UI language automatically.
- **Add-on**: same for `addon/translations/<lang>.yaml` (config option descriptions).
- These are checkbox work; HA's loader does everything.

## Phase 4 — polish and guardrails

- **Locale-aware formatting in the SPA**: dates/times/counts through `Intl.*` with the
  language code (zero dependencies; the server already does this with babel).
- **CI**: one test file validating every catalog — JSON shape, subset-of-en keys,
  cp1252-encodable for anything that can reach the flaps, and a pseudo-locale
  (`xx` = "§" + string + "§") smoke-run to catch layout overflow early.
- **Contribution doc**: a short section in WRITING_APPS/README: copy `en.json`,
  translate the values, PR. Identical to the fortunes workflow people have now seen.

## Explicit non-goals

- **RTL** — no RTL language is in the offered list (and the flap display itself is LTR
  hardware). Revisit only if Arabic/Hebrew are ever added.
- **Gateway firmware web UI** — embedded C string tables on an ESP32; different repo,
  different cost curve. If ever wanted, the companion's `/gw/` proxy shim is the
  cheaper injection point (it already rewrites gateway HTML), but it's out of scope
  here.
- **Translating log lines or the REST/MCP API surface** — machine-facing, stays English.

## Order and effort

| Step | Scope | Rough size |
|---|---|---|
| 1 | `t()` + en.json extraction + wiring, `window.__LANG__` | the one real code change (~1 day) |
| 2 | fr, de, es catalogs (the languages with full content today) | data only |
| 3 | flap fallbacks + catalog labels through Localizer | small, server-side |
| 4 | HACS + add-on translation files | mechanical |
| 5 | remaining languages (it, pt, nl, da, no, sv, ca) | data only, any time |

Phase 1 alone makes the product *feel* localized (a French user sees a French UI over
already-French content); everything after is incremental files.
