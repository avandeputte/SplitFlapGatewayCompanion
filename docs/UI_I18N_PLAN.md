# Plan: a fully multi-lingual companion UI

> **Status: implemented in 1.9.0-beta.1** (phases 1–3 plus the fr/de/es data;
> the remaining languages are data-only follow-ups). This document stays as the
> design record.

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

1. **Chrome is per-viewer; content is per-display.** The flap content language must stay
   a single server-side setting — the wall is shared hardware, and two browsers can't
   each have it render in their own language. The UI chrome has no such constraint, so
   it follows **the viewer's browser by default**, with three explicit overrides:

   | Wins | Source | Set by | Scope |
   |---|---|---|---|
   | 1 | `?lang=fr` URL parameter | whoever crafts the link | that tab |
   | 2 | the global **Language** setting, *when explicitly saved* | the user, in Settings | the install |
   | 3 | `COMPANION_UI_LANGUAGE` env var / `ui_language` add-on option | the operator | the deployment |
   | 4 | `Accept-Language` / `navigator.languages` | the browser | that viewer |

   Unset means "pass to the next level." One wrinkle, verified in the code: the settings
   store is *seeded* with `language: "en-US"` (`plugin_settings._defaults()`) and the
   save path persists catalog keys wholesale — so today "never touched" is
   indistinguishable from "chose en-US." Phase 1 therefore adds a `language_explicit`
   flag, set the first time the Language control is actually saved; the settings level
   participates only when it's true. Migration for existing installs: a stored language
   that differs from the default also counts as explicit (someone picked it), so only
   the harmless case — an old install that explicitly saved en-US — falls through to
   the browser. Whatever level wins, the resolved code then degrades exact locale →
   base language → English, the same chain the channel apps and fortunes already use.

   A French wall viewed from a German phone therefore shows German chrome over French
   content until the owner saves a Language — which is correct: the setting describes
   the display, the browser describes the reader.
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
  effective language (exact → base) and falls through to English on any miss.
- **Resolution happens in `spa_index`**, which already stamps `window.__BASE__` per
  request: it also stamps `window.__LANG__` by walking the chain above — the `?lang=`
  query param (the SPA index request carries it), the saved Language setting, the
  `COMPANION_UI_LANGUAGE` env var (surfaced as an add-on option like every other env),
  then the request's `Accept-Language` header, best-match against the offered list.
  One resolver, server-side, so ingress, curl and the browser all agree; the client
  never needs its own logic beyond reading `window.__LANG__`.
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
- **App-store metadata** (names, descriptions, settings labels) — **not in
  `manifest.json`**, for three reasons. First, compatibility is load-bearing: the
  manifest contract is "drops into splitflap-os unchanged," and splitflap-os reads
  `manifest.get("description", "")[:30]` — a plain string, sliced — so turning
  `description` into a per-language object breaks it outright, and flat
  `description_fr` keys mean editing 60 vendored manifests × N languages, churn that
  collides with every future re-vendor. Second, translators shouldn't touch app code
  to contribute a language. Third, we already have a working idiom: sidecars.

  Two layers, same shape:
  1. **Central catalog** `backend/app/app_i18n/<lang>.json` —
     `{"<app_id>": {"name": …, "description": …, "settings": {"<key>": "<label>"}}}`.
     Covers the whole vendored library in one reviewable file per language; coverage
     is CI-measurable; vendored app folders stay byte-identical to upstream.
  2. **Per-app sidecar** `apps/<id>/i18n/<lang>.json` (same shape, minus the app-id
     level) — wins over the central file, travels inside an uploaded `.zip`, and is
     validated at upload like the `data_<lang>.json` sidecars. This is how a
     third-party app ships its own translations.

  Lookup happens at the single serving point (the registry serializer,
  `plugins.py` `"description": manifest.get(...)`): app sidecar → central catalog →
  manifest English. The language is the **viewer's chrome language** — the store is
  chrome, so it uses the same resolver as `spa_index`, not the content Language.
  Names are translated too — "Weather" should read "Météo"/"Wetter"/"El Temps" in the
  grid and the store, and the schema carries `name` for exactly that. The only names a
  translator should leave alone are true brands ("Star Wars"), which is an editorial
  call made per entry in the language file, not a mechanism limit. Anything that
  *renders a name onto the flaps* (the NO DATA / OFFLINE fallback pages format
  `manifest["name"].upper()` into the page) resolves against the **content Language**
  — the wall is shared, unlike the store — and must stay within the flap character
  set;
  that's the one place a translated name can be overridden by a `flap_name` key if the
  pretty name doesn't survive the reel. `catalog.py`'s global settings labels are
  ordinary chrome strings and go through the Phase-1 UI catalog instead.

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
