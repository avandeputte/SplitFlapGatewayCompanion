# Writing a SplitFlap Companion app

This guide explains how to build a new **app** for the SplitFlap Gateway
Companion — the plugins that produce the content shown on the display (clocks,
weather, quotes, animations, …). It covers every file an app needs, the exact
contents of each, the functions your code must expose, and enough background on
the runtime to write non-trivial apps.

Apps here use the **same plugin ABI as [csader/splitflap-os](https://github.com/csader/splitflap-os)**,
so an app you write drops into a stock splitflap-os install unchanged, and vice
versa. That compatibility is a hard contract — see [COMPATIBILITY.md](COMPATIBILITY.md).

---

## 1. How it works (the runtime in one page)

An app is just a **folder** under `apps/<id>/`. The companion scans that folder
(plus a user-upload folder), reads each app's `manifest.json`, and — for
*installed* apps — loads it. There are two kinds of app:

- **Functional** — has an `app.py` exposing a `fetch()` function that returns the
  content. Use this for anything dynamic (an API call, the current time, a
  computed animation).
- **Channel** — has a `data.json` holding a fixed list of pages. Use this for
  static content (a rotating set of quotes) with no code.

The **display** is a grid of `rows × cols` modules. A single screen is called a
**page**: a string of exactly `rows × cols` characters, laid out row-major
(`module index = row × cols + col`). An app returns a **list of pages**.

The **play loop** then:

1. Calls your app to get its pages (functional: runs `fetch()`; channel: reads
   `data.json`).
2. Shows the pages one at a time, each for **`loop_delay`** seconds, cycling.
3. Re-fetches when the cached result is older than **`refresh_interval`** seconds
   (functional apps only — the result is cached in between, so a 5-minute weather
   refresh isn't hit on every page flip).

Your `fetch()` may block on network I/O; the runtime runs it in a thread, so
`requests.get(...)` is fine.

```
apps/
└── my-app/
    ├── manifest.json     ← always required
    ├── app.py            ← functional apps (has fetch())
    └── data.json         ← channel apps (static pages)
```

The folder name is the app **id** (`my-app` above). Ids may contain letters,
digits, `-` and `_`. Folders beginning with `.` or `_` are ignored.

---

## 2. `manifest.json` — the app descriptor

Required for every app. It's JSON describing the app and its settings. Minimal
functional example:

```json
{
  "name": "My App",
  "icon": "✨",
  "description": "One-line summary shown on the app tile",
  "type": "functional",
  "category": "data",
  "refresh_interval": 60,
  "loop_delay": 5
}
```

### Top-level fields

| Field | Type | Required | Meaning |
|---|---|---|---|
| `name` | string | **yes** | Display name (app tile, HA select, menus). |
| `type` | `"functional"` \| `"channel"` | **yes** | Determines whether the app has `app.py` or `data.json`. |
| `icon` | string (emoji) | no | Tile icon. Default `🧩`. |
| `description` | string | no | One-line blurb on the tile / library. |
| `category` | string | no | Grouping in the App Library. Common: `time`, `data`, `finance`, `sports`, `news`, `entertainment`, `education`, `lifestyle`, `animation`. Default `other`. |
| `id` | string | no | Overrides the id; normally leave it out and let the folder name win. |
| `version` | string | no | Informational (e.g. `"1.0"`). |
| `refresh_interval` | number (s) | no | How long `fetch()` output is cached before re-running. Default `300`. Use `0`/`1` for always-fresh (clocks, animations). Ignored for channel apps. |
| `loop_delay` | number (s) | no | How long each page is shown before advancing. Default = the global loop delay (`5`). |
| `min_rows` / `min_cols` | number | no | Hide/disable the app unless the grid is at least this size. |
| `min_modules` | number | no | Require this many modules **total**, any shape (e.g. `45` works on 1×45 or 3×15). |
| `animation` | bool | no | Marks the app as an animation (see §6). Also inferred when the id starts with `anim_`. |
| `skip_rotation_wait` | bool | no | Advance pages without waiting for the flaps' mechanical settle (snappier multi-page apps). |
| `settings` | array | no | The app's settings form — see §7. |
| `trigger_interval`, `trigger_display_seconds`, `trigger_cooldown`, `trigger_conditions` | — | no | Only for trigger apps — see §8. |

---

## 3. The display model (what a page is)

A **page** is a string of exactly `rows × cols` characters. Row 0 is the first
`cols` characters, row 1 the next `cols`, and so on. You almost never build that
string by hand — use the `format_lines` helper (below), which centres each line
and pads/truncates to the grid.

**Characters.** Text is upper-cased on the way to the display (Windows-1252
aware, so `É`, `Ü`, `ç`, `ß` survive), and every glyph is passed through to the
gateway verbatim — the companion does **not** restrict you to a fixed character
set. Each physical module simply shows a blank for any character its own flap set
doesn't include. So write normal text; punctuation, accents and `€` are fine.

**Colour tiles.** Lower-case letters `r o y g b p w` are the firmware colour
codes (red, orange, yellow, green, blue, purple, white). A page made of these
shows solid colour flaps. In *page text* you may also use emoji colour squares,
which map to those codes:

| Emoji | Code | Colour |
|---|---|---|
| 🟥 | `r` | red |
| 🟧 | `o` | orange |
| 🟨 | `y` | yellow |
| 🟩 | `g` | green |
| 🟦 | `b` | blue |
| 🟪 | `p` | purple |
| ⬜ | `w` | white |
| ⬛ | (space) | blank |

> Case matters: `y` = a yellow tile, `Y` = the letter Y.

---

## 4. Functional apps — `app.py`

A functional app's `app.py` must define **`fetch`**:

```python
def fetch(settings, format_lines, get_rows, get_cols):
    ...
    return ["<page string>", "<page string>", ...]
```

### The four arguments

- **`settings`** — a flat `dict` of the app's resolved settings plus shared
  global settings (see §7). Read values with `settings.get("key", default)`.
- **`format_lines(*lines, cols=None)`** — build a page from up to `rows` text
  lines. Each line is centred in `cols` (default = grid width) and truncated;
  missing lines are blank. Returns one `rows × cols` page string. This is the
  normal way to build a page.
- **`get_rows()`** / **`get_cols()`** — the current grid dimensions as ints. Call
  these and adapt your layout — a good app renders sensibly at 1×N and 3×N.

### The return value

Return a **list of page strings**. Each should be `rows × cols` characters — i.e.
each should come from `format_lines(...)` (or be built to match). One page = one
screen; multiple pages rotate at `loop_delay`. Always return **at least one**
page. (A non-list return value is coerced to a single page.)

### Caching & errors

- Your `fetch()` result is cached for `refresh_interval` seconds; the play loop
  reuses it for every page flip in between. Don't do your own caching for that.
- If `fetch()` raises, the runtime falls back to the **last good cached pages**;
  if there are none, it shows a generic error page. Exceptions whose text
  mentions `timeout`/`connection`/`network` render an "OFFLINE" page. So it's
  fine to let a network error propagate — but catching it yourself and returning
  a friendly page (as the examples do) is nicer.

### Example — the built-in `date` app

```python
def fetch(settings, format_lines, get_rows, get_cols):
    from datetime import datetime
    import pytz
    tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
    now = datetime.now(tz)
    time_str  = now.strftime('%I:%M %p')
    month_day = now.strftime('%B %d')
    weekday   = now.strftime('%A')
    rows = get_rows()
    if rows == 2:
        return [format_lines(month_day, weekday)]
    return [format_lines(time_str, month_day, weekday)]
```

Note how it reads a **global** setting (`timezone`) and **adapts to the grid**
(`rows == 2`).

### Dependencies

`app.py` runs in the companion's Python process, so it may `import` anything the
companion already ships: the standard library plus `requests`, `pytz`,
`httpx`, `yfinance` (pandas/numpy), `paho-mqtt`, and FastAPI's stack. A
**built-in** app that needs a new package requires adding it to
`backend/requirements.txt` and rebuilding the image. An **uploaded** app can only
use packages already present (there's no per-app dependency install).

---

## 5. Channel apps — `data.json`

A channel app has no code. Its `data.json` lists the pages:

```json
{
  "pages": [
    {"lines": ["MAY THE FORCE", "BE WITH YOU", "- OBI WAN"]},
    {"lines": ["DO OR DO NOT", "THERE IS NO TRY", "- YODA"]},
    "R O Y G B P W"
  ]
}
```

Each entry in `pages` is either:

- an **object** `{"lines": [...]}` — the lines are run through `format_lines`
  (centred & padded to the grid), or
- a **raw string** — used exactly as-is (must already be `rows × cols`; handy for
  colour rows).

The manifest `type` must be `"channel"`, and channel apps ignore
`refresh_interval` (the pages are static). Pages rotate at `loop_delay`.

---

## 6. Animations

An animation is just a functional app that returns many pages (frames) built from
colour codes, marked with `"animation": true` (or an `anim_` id). The play loop
plays the frames back-to-back using the global animation speed/order rather than
the normal per-app timing.

```json
{ "name": "Rainbow", "icon": "🌈", "type": "functional",
  "animation": true, "refresh_interval": 0, "loop_delay": 0.4,
  "category": "animation" }
```

```python
def fetch(settings, format_lines, get_rows, get_cols):
    colors = 'roygbpw'
    rows, cols = get_rows(), get_cols()
    return [''.join(colors[(c + off) % 7] for r in range(rows) for c in range(cols))
            for off in range(7)]
```

Each string here is a full `rows × cols` frame of colour codes; the seven frames
form a scrolling rainbow. `refresh_interval: 0` keeps frames regenerating.

---

## 7. Settings (the app's config form)

`settings` in the manifest is an array of field descriptors. The companion
renders them into the app's settings dialog and passes the saved values into
`fetch()` (and `trigger()`) via the `settings` dict.

```json
"settings": [
  {
    "key": "city",
    "label": "City",
    "type": "text",
    "ph": "Boston",
    "default": ""
  },
  {
    "key": "units",
    "label": "Units",
    "type": "select",
    "options": ["metric", "imperial"],
    "default": "metric"
  }
]
```

### How keys reach your code

- A normal setting `"key": "city"` is stored per-app and appears in the
  `settings` dict under **`"city"`**. (Internally it's namespaced as
  `plugin_<id>_city`, but your code just reads `settings.get("city")`.)
- The companion has a **fixed built-in catalog of global settings** — the API
  keys, location, timezone, weather provider and default page dwell
  (`weather_api_key`, `weather_provider`, `zip_code`, `location_lat/lon/name`,
  `timezone`, `yt_api_key`, `global_loop_delay`). Reading one of those keys
  (`settings.get("weather_api_key")`) returns the shared global value.
- **Everything else is per-app** — each app keeps its own value even if two apps
  use the same key name. On the companion a manifest's `"global_key": true` is
  *ignored* (only the catalog is global); it still works on stock splitflap-os,
  so it's harmless to leave in, but it won't make a non-catalog key shared here.

The `settings` dict your code receives always includes the catalog globals plus
this app's own keys, so you can read shared config (`settings['zip_code']`) even
without declaring it.

### Setting object fields

| Field | Meaning |
|---|---|
| `key` | **Required.** The setting name (see key resolution above). |
| `label` | Field label in the form. |
| `type` | Field type (table below). Default `text`. |
| `default` | Initial value before the user saves anything. |
| `global_key` | `true` → shared global setting instead of per-app. |
| `options` | For `select`/`toggle`: array of strings, or `{"value","label"}` objects. |
| `ph` | Placeholder text. |
| `min` / `max` / `step` | For `number` inputs. |
| `stepper` | `true` → show −/+ stepper buttons around a number field. |
| `searchUrl` / `resultKey` / `maxItems` | For `search_chips` (see below). |
| `visible_when` | `{ "otherKey": "value" }` — only show this field when another field equals a value. |
| `inline_toggle` | Attach a small secondary toggle to the field (its own `key`/`default`/`global_key`). |
| `compute` / `watches` | For `computed` read-only fields derived from other fields. |
| `title` / `text` / `items` / `icon` / `linkText` / `linkHref` / `variant` / `size` | Passed through to the frontend for presentational field types. |

### Field types

| `type` | Renders as |
|---|---|
| `text` | Single-line text input. |
| `number` | Numeric input (with optional `stepper`, `min`/`max`/`step`). |
| `password` | Masked text input (API keys). |
| `textarea` | Multi-line text. |
| `select` | Dropdown from `options`. |
| `toggle` | Segmented buttons from `options` (`{value,label}`). |
| `search_chips` | Live-search field that adds chips — see below. |
| `computed` | Read-only value derived from other fields (`compute`/`watches`). |
| `notice` | Static informational text (`label`/`text`) — not an input. |

### `search_chips` and the built-in search endpoints

A `search_chips` field queries an endpoint as the user types and lets them pick
results as chips. Point `searchUrl` at one of the companion's built-in search
endpoints (same paths and response shapes as splitflap-os):

| `searchUrl` | For |
|---|---|
| `/location_search` | Cities / locations |
| `/location_timezone` | Timezone for a location |
| `/timezones` | Timezone names |
| `/stocks_search` | Stock tickers |
| `/crypto_search` | Cryptocurrencies |
| `/sports_leagues` | Sports leagues |
| `/sports_teams/<league>` | Teams in a league |
| `/sports_follow` | Follow a team |

`resultKey` names the array in the JSON response to read, and `maxItems` caps how
many chips can be selected. Example (the `date` app's timezone override):

```json
{ "key": "timezone", "label": "Timezone (override global)",
  "type": "search_chips", "searchUrl": "/timezones", "resultKey": "zones",
  "maxItems": 1, "global_key": true }
```

---

## 8. Triggers (optional, functional apps)

A trigger lets an app **interrupt** the display when something happens (the ISS
passes overhead, a game starts). Add a `trigger` function to `app.py`:

```python
def trigger(settings, conditions) -> bool:
    # return True to fire the interrupt now, False otherwise
    ...
```

- `settings` — same dict as `fetch()`.
- `conditions` — the values the user configured from `trigger_conditions` (below).
- Return **`True`** to interrupt and show this app for `trigger_display_seconds`,
  then respect `trigger_cooldown` before it can fire again.

Declare the schedule and the config UI in the manifest:

```json
"trigger_interval": 60,
"trigger_display_seconds": 30,
"trigger_cooldown": 3600,
"trigger_conditions": [
  { "key": "condition_type", "label": "Fire when", "type": "toggle",
    "default": "overhead",
    "options": [
      {"value": "overhead", "label": "ISS overhead"},
      {"value": "crew_change", "label": "Crew change"}
    ] }
]
```

| Manifest field | Meaning |
|---|---|
| `trigger_interval` | How often (seconds) the runtime calls `trigger()`. |
| `trigger_display_seconds` | How long to show the app when it fires. |
| `trigger_cooldown` | Minimum seconds between firings. |
| `trigger_conditions` | Setting fields (same schema as §7) whose values arrive in `conditions`. |

### Keeping state across calls

`trigger()` (and `fetch()`) are plain functions, but you can stash state on the
function object itself — the runtime keeps the module loaded, so it persists
between calls:

```python
def trigger(settings, conditions):
    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'last_crew': None}
        setattr(trigger, '_state', state)
    ...
```

This is how the built-in `iss` app remembers the previous crew roster to detect a
change.

---

## 9. Installing your app

Two ways:

1. **Drop-in (built-in style).** Put the folder in `apps/<id>/` and (re)start the
   companion. It appears in the **App Library**; enable it there to load it. This
   is how the vendored apps ship.

2. **Upload a `.zip` (no restart).** In the UI: **App Library → Upload**. Zip the
   **app folder** so the archive contains exactly one `manifest.json` (its parent
   folder becomes the id):

   ```
   my-app/
     manifest.json
     app.py          (or data.json)
   ```
   ```bash
   cd my-app && zip -r ../my-app.zip .        # or zip the folder itself
   ```

   The upload is validated: the manifest must have a `name` and a valid `type`; a
   functional app must include `app.py` and it must **import cleanly and expose
   `fetch()`**; a channel app must include `data.json`. Uploaded apps are written
   to the persistent data volume (so they survive restarts and image upgrades),
   enabled, and loaded immediately. They show a **· uploaded** tag and a 🗑 to
   remove; built-ins can't be deleted. An uploaded app with the same id as a
   built-in **overrides** it.

> ⚠️ **Security:** installing a functional app *runs its `app.py`* (the upload
> validator imports it, and the runtime executes `fetch()`). That's arbitrary
> Python on the companion host — only install apps you trust. Same trust model as
> splitflap-os plugins.

---

## 10. Checklist & tips

- [ ] Folder `apps/<id>/` with a valid `manifest.json` (`name` + `type`).
- [ ] Functional: `app.py` defines `fetch(settings, format_lines, get_rows, get_cols)` returning a **list of pages**; each page from `format_lines(...)`.
- [ ] Channel: `data.json` with a `pages` array (`{"lines":[...]}` or raw strings).
- [ ] Read grid size with `get_rows()/get_cols()` and lay out for at least a couple of shapes; set `min_rows`/`min_cols`/`min_modules` if it needs a minimum.
- [ ] Pick sensible `refresh_interval` (cache) and `loop_delay` (page dwell).
- [ ] Only `import` packages the companion ships (or add to `requirements.txt` for a built-in).
- [ ] Let network errors raise (you get OFFLINE/cached fallback) or catch and return a friendly page.
- [ ] The conformance test `backend/tests/test_plugins.py::test_every_app_loads` loads every app and checks the contract — run `pytest` after adding a built-in.

For the formal compatibility contract (and the one place the companion
intentionally differs from splitflap-os — character normalization), see
[COMPATIBILITY.md](COMPATIBILITY.md).
