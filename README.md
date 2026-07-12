# SplitFlap Gateway Companion

A web app that sends rich content — clocks, weather, stocks, sports, quotes,
animations, composed messages — to a split-flap display driven by
**[SplitFlapGateway](https://github.com/avandeputte/SplitFlapGateway)**.

The gateway (an ESP32) owns the *hardware*: the RS-485 bus, module discovery,
provisioning, calibration and diagnostics. The companion owns the *content*: apps,
playlists, schedules, triggers and a live preview. The two run on separate machines
but feel like **one product** — they share a look and a tab bar that cross-links
between them, and the companion registers itself so a **Companion** tab appears on
the gateway. Each side tells the other which tabs it has, so the navigation always
matches what actually exists on both.

The content apps are the **plugin library from
[csader/splitflap-os](https://github.com/csader/splitflap-os)**, run through a
**behavior-identical plugin runtime** so any splitflap-os app drops in unchanged.
Building your own? **[WRITING_APPS.md](WRITING_APPS.md)** is a full guide; see also
[COMPATIBILITY.md](COMPATIBILITY.md) and [ATTRIBUTION.md](ATTRIBUTION.md).

> **License:** CC BY-NC-SA 4.0 (non-commercial, share-alike, attribution) — a
> derivative of splitflap-os. See [LICENSE](LICENSE).

---

## What it does

- **Apps** — the vendored splitflap-os apps (functional + channel), loaded through a
  drop-in-compatible runtime (a conformance test asserts every app satisfies the
  contract). A tile grid with one-tap run and live "▶ running" state, an **App
  Library** to add or remove apps, and **upload your own** (a `.zip` of the app
  folder) — persisted to the data volume. The library shows each app's description,
  type, version and category, with search and category filters.
- **Manifest-driven settings** — a full renderer: text / number / password /
  textarea / select / toggle, **search_chips** (live search for locations, stocks,
  crypto, timezones), stepper, inline-toggle, `visible_when`, `sync_values` and
  computed fields.
- **Compose** — a click-to-type grid with colour tiles and every transition style
  (`ltr`, `rtl`, `spiral`, `slot`, `columns`, `outside_in`, …).
- **Live preview + Home all** — the board mirrors what's on the wall, and **⌂ Home
  all** returns every module to its blank home position, stopping whatever is playing.
- **Playlists** — sequence apps and messages with per-entry durations; save, run,
  loop. **Per-entry settings** let the same app appear more than once configured
  differently (e.g. weather for two cities in two languages).
- **Schedules** — time-of-day windows that run an app or playlist, or turn the
  display off, per weekday, plus **quiet hours**.
- **Triggers** — apps that watch for events (the ISS overhead, a game, weather) and
  briefly **interrupt** the display, with a per-trigger cooldown.
- **Localization** — a global **Language** plus **Location** and **Timezone**, all at
  the top of Global settings. Apps carrying a 🌐 badge follow them: translated words,
  locale date order, number format, 12h/24h, and currency/holidays by country. All
  overridable per app and per playlist entry. See **[Localization](#localization)**.
- **The gateway, in one place** — the gateway's own calibration / modules /
  diagnostics UI opens right inside the companion (and, as a Home Assistant app, right
  inside the sidebar), so you never leave to configure the hardware.
- **Home Assistant** — a native **[HACS integration](#home-assistant-hacs-integration)**,
  an **[MQTT device](#home-assistant-mqtt)**, a one-click **[app for the sidebar](#home-assistant-app-add-on)**,
  a **[Vestaboard-compatible API](#vestaboard-compatible-api)**, and an
  **[MCP server](#mcp-server)** for LLM clients.
- **Packaging** — a multi-arch Docker image (healthcheck + `/data` volume), a
  one-line installer, and a Home Assistant app.

The gateway is the **source of truth** for hardware config: the grid size and MQTT
broker are read from it. The companion drives the display over **REST** — a whole
page in one request, no broker — so animations are smooth. MQTT is used only for the
Home Assistant MQTT device, never for the display.

---

## Quick start

### Home Assistant app (easiest, if you run HA)

Home Assistant calls these "apps" (formerly "add-ons"). This repository is an app
repository:

1. **Settings → Apps → App Store → ⋮ → Repositories**, and add:
   `https://github.com/avandeputte/SplitFlapGatewayCompanion`
2. Install **SplitFlap Gateway Companion**, set `gateway_url` in its Configuration tab.
3. **Start**, then **Open Web UI** — it appears in the sidebar, themed to match HA.

Details, options and the two channels are in **[Home Assistant app](#home-assistant-app-add-on)**.

### Docker

```bash
docker run -d --name splitflap-companion -p 8000:8000 \
  -e GATEWAY_URL=http://192.168.1.50 -v companion-data:/data \
  ghcr.io/avandeputte/splitflap-gateway-companion:latest
# open http://localhost:8000
```

The image is **multi-arch**, so the same tag runs on x86 (Windows/Linux) and arm64
(Raspberry Pi 64-bit, Apple Silicon) — Docker pulls the right architecture. Point
`GATEWAY_URL` at your gateway; that's usually all you need, and it's **required** (the
app refuses to start without it). Or use Compose:

```bash
docker compose pull && docker compose up -d      # set GHCR_OWNER in .env
```

### Install script

One command sets everything up on a Raspberry Pi or any x86-64 Linux box — it installs
Docker if needed, asks a few questions, writes a `docker-compose` project and starts
the companion:

```bash
curl -fsSL https://raw.githubusercontent.com/avandeputte/SplitFlapGatewayCompanion/main/install.sh | bash
```

It installs Docker if absent, optionally deploys a Mosquitto broker (only needed for
the Home Assistant MQTT device — skip it if your gateway already has a broker), asks
for the gateway URL and optional MQTT password, creates the container (auto-detecting
this host's IP so the gateway can link back), and can add a Watchtower container that
auto-updates every 6h. Every prompt has an env-var override for unattended installs:

```bash
GATEWAY_URL=http://192.168.1.50 DEPLOY_MQTT=no AUTO_UPDATE=yes \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/avandeputte/SplitFlapGatewayCompanion/main/install.sh)"
```

To update by hand: `cd <project-dir> && docker compose pull && docker compose up -d`.

### Local development

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
GATEWAY_URL=http://192.168.1.50 python -m app   # required; binds 0.0.0.0:8000
GATEWAY_URL=http://192.168.1.50 COMPANION_RELOAD=1 python -m app   # auto-reload
pytest                                          # run the tests
```

> Run it with `python -m app`, not a bare `uvicorn app.main:app`: the bare command
> binds `127.0.0.1` (localhost only), so the companion isn't reachable on your LAN and
> the URL it registers with the gateway won't work. `python -m app` binds `0.0.0.0`
> and honours `COMPANION_HOST` / `COMPANION_PORT`.

---

## Configuration

**There is no companion config file.** Configuration is derived at runtime from
`defaults ← gateway ← environment` and never written to disk, so there's nothing to
seed, migrate or back up.

- **Environment** — the things you set: **`GATEWAY_URL`** (**required**) and, if your
  broker needs auth for Home Assistant, `COMPANION_MQTT_PASSWORD`. Env always wins.
- **Gateway (source of truth)** — the grid size and the MQTT broker/port/user/prefix
  are read from the gateway's `/api/config` on startup and on **Sync**.
- **Defaults** — sensible fallbacks used until the gateway answers.

As a Home Assistant app there are no environment variables — the same settings come
from the Configuration tab (see [Home Assistant app](#home-assistant-app-add-on)).

`<data_dir>` (a Docker volume) holds your **app settings, playlists, triggers and
uploaded apps** — not any companion config, which is never persisted.

| Env var | Meaning | Default |
|---|---|---|
| `GATEWAY_URL` | Gateway base URL (REST + config sync + status). **Required** | *(none; required)* |
| `COMPANION_PUBLIC_URL` | This companion's own URL, registered with the gateway for its "Companion" tab | *(auto-detected)* |
| `COMPANION_SYNC_FROM_GATEWAY` | Pull grid + MQTT from the gateway on startup | `true` |
| `COMPANION_MQTT_PASSWORD` | MQTT password for **Home Assistant only** (the gateway never exposes it) | — |
| `COMPANION_HA` | Home Assistant MQTT device: `auto` (follow gateway) \| `true` \| `false` | `auto` |
| `COMPANION_VESTABOARD` / `_KEY` | Enable the [Vestaboard API](#vestaboard-compatible-api) and pin its key | `off` |
| `COMPANION_MCP` / `_TOKEN` | Enable the [MCP server](#mcp-server) and pin its token | `off` |
| `COMPANION_THEME` | UI skin: `default` \| `ha` (Home Assistant's design language) | `default` |
| `COMPANION_MODULE_ID_BASE` | Module id of grid index 0 | `0` |
| `COMPANION_GRID_ROWS` / `_COLS` | Manual panel-size override | *(from gateway)* |
| `COMPANION_MQTT_BROKER` / `_PORT` / `_PREFIX` / `_USER` | Manual MQTT overrides | *(from gateway)* |
| `COMPANION_SETTINGS_STORE` | Where settings live: `mirror` \| `local` \| `gateway` — see below | `mirror` |
| `COMPANION_DATA_DIR` | Where app settings, playlists, triggers + uploaded apps live | `<repo>/data` |
| `COMPANION_DEV_MODE` | Show a **⚙ Dev** menu (simulation, gateway resync, grid override, the Vestaboard/MCP switches) | `off` |
| `COMPANION_LOG_LEVEL` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` \| `CRITICAL` | `INFO` |

### Settings storage

The companion keeps its settings, playlists and triggers in
`<data_dir>/app_settings.json`, and can also mirror them onto the gateway so a fresh
container inherits them. `COMPANION_SETTINGS_STORE`:

- **`mirror`** (default) — the local file is primary, and every change is mirrored to
  the gateway (gzipped, debounced). A fresh host with no local file **restores from the
  gateway** on boot; an empty gateway is seeded from the local copy.
- **`local`** — local file only; the gateway is never touched.
- **`gateway`** — stored only on the gateway, nothing written locally (a diskless
  companion).

Settings are tiny (~1–2 KB gzipped), writes are debounced, and the companion pauses
display frames during a settings transfer so it never floods the gateway. Uploaded
custom apps live in `<data_dir>/apps/` — only the settings blob moves.

### Characters

Text is upper-cased in a **Windows-1252-aware** way, so `ß` and accented letters like
`É`, `Ü`, `ç` survive (`ß` is *not* expanded to `SS`), and sent to the gateway in
Windows-1252. The companion does **not** police characters: every glyph (accents, `€`,
punctuation) passes through, and each module blanks anything its own flap set can't
show. Emoji colour squares (🟥🟩🟦 …) map to the gateway's colour flaps.

### Grid → module mapping

The display is filled row-major: module `= module_id_base + (row × cols + col)`. Set
rows/cols/base to match how your modules are provisioned in the gateway.

### Uploading your own apps

**App Library → Upload** takes a `.zip` of an app folder — `manifest.json` plus
`app.py` (functional) or `data.json` (channel), the same format as the built-in apps.
The upload is validated (manifest + a functional app's `fetch()` must import), written
to `<data_dir>/apps/<id>/` so it survives restarts and image upgrades, enabled and
loaded immediately. **[WRITING_APPS.md](WRITING_APPS.md)** is the full guide.

> ⚠️ A functional app's `app.py` runs arbitrary Python on the companion host — only
> upload apps you trust. (The same trust model as splitflap-os plugins.)

---

## Home Assistant

Five ways to use the companion from Home Assistant, from most to least integrated.

### Home Assistant app (add-on)

Runs the full companion in the HA sidebar, themed to match, configured from the
Configuration tab — no environment variables, no command line. Install it as in
[Quick start](#home-assistant-app-easiest-if-you-run-ha).

**Channels.** The app ships on two, side by side, each its own store entry:

| Folder | Store entry |
|---|---|
| [addon/](addon/) | SplitFlap Gateway Companion |
| [addon-beta/](addon-beta/) | SplitFlap Gateway Companion **(Beta)** |

Pick the stable channel unless you want prereleases. Both run the same published image
— there is no app-specific build.

**Options** (Configuration tab): `gateway_url` (required), `mqtt_password`,
`companion_public_url`, `home_assistant`, `vestaboard` + `vestaboard_key`, `mcp` +
`mcp_token`, `theme`, `dev_mode`, `log_level`.

What makes it native:

- **Sidebar (ingress).** The UI, and the gateway's own configuration UI, both open
  inside Home Assistant — no leaving to a separate tab.
- **HA theme.** The interface is restyled in Home Assistant's design language. The
  split-flap board itself stays dark — it depicts physical flaps.
- **Finds itself.** It asks Supervisor for the host's real address and its published
  port, so the gateway can link back to it.

Ingress covers the *UI*. The Vestaboard API and the MCP server are reached by clients
that aren't the HA frontend, so the app also publishes port **8000** for them.

### Home Assistant (HACS integration)

A native integration — install via [HACS](https://hacs.xyz), point it at the
companion's URL, and it adds a **SplitFlap** device with:

| Entity | Does |
|---|---|
| **App** (select) | Run an installed app, or `Off` to stop |
| **Playlist** (select) | Run a saved playlist, or `Off` |
| **Showing** (sensor) | Which app is on the flaps right now — even mid-playlist |
| **Message** (sensor) | What the board reads, as text (with a `lines` attribute) |
| **Clear / Stop / Home all** (buttons) | — |
| `splitflap.message` (service) | Show text, with an optional style and a **timed auto-revert** |

Unlike a Vestaboard integration, this surfaces the **apps and playlists**. It needs no
MQTT broker — it talks to the companion directly. The `splitflap.message` service with
`seconds` set shows a message for that long, then returns the display to whatever was
playing:

```yaml
action:
  - service: splitflap.message
    data:
      text: "Dinner's ready"
      seconds: 30
```

The integration lives in [custom_components/splitflap/](custom_components/splitflap/).

### Home Assistant (MQTT)

With `COMPANION_HA=auto` (the default) the companion enables HA when the gateway has HA
turned on, reusing the **same MQTT broker**, and publishes one auto-discovery device,
**SplitFlap Companion**, with the controls unique to the companion (the gateway's own
HA device already covers flashing a message and reading the board):

| Entity | Type | Does |
|---|---|---|
| App | select | Run an app (or `Off` to stop); state shows the running app |
| Playlist | select | Run a playlist (or `Off`); state shows the running playlist |
| Stop | button | Stop whatever is running |

Use this if you already run MQTT and don't want the HACS integration; the HACS
integration is the richer option.

### Vestaboard-compatible API

A **[Vestaboard](https://www.vestaboard.com/)** is a commercial split-flap display with
a widely-spoken [Local API](https://docs.vestaboard.com/docs/local-api/endpoints). Turn
this on and the companion answers that API, so anything written for a Vestaboard — a
`rest_command`, the [ha-vestaboard](https://github.com/natekspencer/ha-vestaboard)
integration, a script, a client library — drives *your* wall unchanged. A Vestaboard
Note is 3 × 15, exactly the default grid.

```bash
COMPANION_VESTABOARD=1          # off by default
COMPANION_VESTABOARD_KEY=       # blank -> generated once, kept with your settings
```

It's also toggleable from the **Dev menu** (`COMPANION_DEV_MODE=1`), where you read the
generated key.

| Endpoint | Does |
|---|---|
| `POST /local-api/message` | Show a message. Takes a character-code matrix, `{"characters": [[…]], "strategy": "…"}`, or `{"text": "…"}` (an extension — the real API is matrix-only, but most HA setups send text). Returns **201**, like a real board |
| `GET /local-api/message` | The board as `{"message": [[…]]}` — the matrix wrapped as the real API returns it, so clients that read `response["message"]` work unchanged |
| `POST /local-api/enablement` | Vestaboard's key handshake — only if you set `COMPANION_VESTABOARD_ENABLEMENT_TOKEN` |

Every call needs the `X-Vestaboard-Local-Api-Key` header; that key guards these routes
only. Clients that hard-code a real board's **port 7000** are satisfied by publishing
the container on it (`-p 7000:8000`) — there's no second server. Home Assistant:

```yaml
rest_command:
  splitflap_message:
    url: "http://companion-host:8000/local-api/message"
    method: POST
    headers:
      X-Vestaboard-Local-Api-Key: !secret splitflap_api_key
    content_type: "application/json"
    payload: '{"text": "{{ message }}"}'
```

**Characters.** Codes follow Vestaboard's
[table](https://docs.vestaboard.com/docs/charactercodes/): `0` blank, `1–26` A–Z,
`27–35` 1–9, `36` 0, punctuation, and `63–69` the colour chips (`r o y g b p w`, violet
→ `p`). Two are lossy: **black (70) → blank** and **filled (71) → white**.
**Geometry:** a 3 × 15 message lands cell-for-cell; a 6 × 22 Flagship message is trimmed
of its blank padding and centred on your wall. **Animations:** Vestaboard's `strategy`
maps onto the transition styles (`column` → `columns`, `edges-to-center` → `outside_in`,
…); `step_interval_ms` / `step_size` are accepted and ignored.

### MCP server

Turn this on and the display becomes a set of
**[MCP](https://modelcontextprotocol.io) tools**, so an LLM client — Claude, an agent,
an IDE — can drive the wall in words: *"put the standup time on the board"*, *"what's
showing right now?"*, *"show the weather for Paris for 30 seconds"*.

```bash
COMPANION_MCP=1                 # off by default
COMPANION_MCP_TOKEN=            # blank -> generated once, kept with your settings
```

Point a client at `http://<host>:8000/mcp` with `Authorization: Bearer <token>`. The
token is shown in the **Dev menu** (`COMPANION_DEV_MODE=1`), which can also flip the
server on at runtime.

| Tool | Does |
|---|---|
| `get_display` | What's on the flaps, row by row; what's driving them; and, mid-playlist, which app is on screen and where in the rotation |
| `show_message` | Put text on the board, centred and word-wrapped. `seconds` shows it temporarily, then reverts to what was playing |
| `clear_display` | Blank every module |
| `list_apps` / `run_app` | The installed apps (with a `configurable` flag), and run one |
| `get_app_settings` / `configure_app` | Read and change an app's settings (location, tickers, …) |
| `list_playlists` / `run_playlist` | The saved playlists with their running order, and run one |
| `stop` | Stop the running app or playlist |
| `list_styles` | The transition styles `show_message` accepts |

The token guards `/mcp` only — it's a credential for this endpoint, not a security
boundary for the host. DNS-rebinding protection is deliberately off, because a companion
is reached by whatever name its LAN gives it (`homeassistant.local:8000`, an IP, a
reverse proxy); the bearer token is what guards it, and it isn't a cookie a hostile page
could replay.

---

## Localization

Set **Language**, **Location** and **Timezone** at the top of **Global settings**. Apps
that adapt carry a 🌐 badge (on the tile, in the library, and in the playlist picker).
The languages are those whose characters fit the modules' Windows-1252 code page:
**English (US / UK / Australia)** plus French, German, Spanish, Italian, Portuguese,
Dutch and the other Western-European languages.

**What Language controls** (for 🌐 apps):

- **Words** — labels and status text are translated (SUNRISE → LEVER, GOLD → OR,
  weather conditions, moon phases, countdown units …). Untranslated words fall back to
  English, so nothing breaks.
- **Date order** — `JULY 9` (US), `9 JULY` (UK/AU), `9 JUILLET` (fr), `9. JULI` (de),
  via CLDR.
- **Number format** — `1,234.50` (US/UK) vs `1.234,50` (de/es/it/nl/pt) vs `1 234,50`
  (fr), for prices, rates and percentages.
- **Clock** — 12-hour with AM/PM in English, 24-hour elsewhere.
- **Word clock & fortunes** — genuinely per-language: the word clock builds each
  language's grammar (`HALB ELF` = 10:30 in German), and the fortunes app ships British
  and Australian editions.

**Currency and holidays follow Location, not language** — French could be France (EUR),
Canada (CAD) or Switzerland (CHF), so these key off the configured Location
(reverse-geocoded to a country, cached): Exchange Rates default to your country's
currency, and Public Holidays show your country's calendar down to the **province /
state** (Quebec ≠ British Columbia), the common ones in your Language.

**Overrides.** Every 🌐 app has its own **Language** picker (blank = follow global), and
every location-tied app has a **Location** override — per app *and* per playlist entry,
so one playlist can show Paris in French and Tokyo in Japanese back to back.

Building an app that localizes? **[WRITING_APPS.md](WRITING_APPS.md)** documents the
injected `i18n`, `get_weather` and `get_location` helpers.
