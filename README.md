# SplitFlap Gateway Companion

A web app that sends rich content — clocks, weather, stocks, sports, quotes,
animations, composed messages — to a split-flap display driven by
**[SplitFlapGateway](https://github.com/avandeputte/SplitFlapGateway)**.

The gateway (an ESP32) owns all the *hardware*: the RS-485 bus, module discovery,
provisioning, calibration and diagnostics. The companion owns all the *content*:
apps, playlists, triggers and a live preview. The two run on separate machines but
are meant to feel like **one integrated product** — both share the same look and a
unified tab bar that cross-links between them (with a ↗ marking the jump), and the
companion registers itself so a **Companion** tab appears on the gateway.

> **Requires Gateway 3.0 or newer.** The companion uses 3.0 APIs (batch RS-485
> send, `/api/companion` registration, schedule-driven quiet time). It targets
> 3.0+ exclusively — there are no fallbacks for older firmware.

The content apps are the **plugin library from
[csader/splitflap-os](https://github.com/csader/splitflap-os)**, reused through a
**behavior-identical plugin runtime** so any splitflap-os app drops in unchanged.
See [COMPATIBILITY.md](COMPATIBILITY.md) and [ATTRIBUTION.md](ATTRIBUTION.md).

> **License:** CC BY-NC-SA 4.0 (non-commercial, share-alike, attribution) — this
> is a derivative of splitflap-os. See [LICENSE](LICENSE).

---

## Status — feature-complete

All six build phases are done. What's here:

- **Apps** — all **46 vendored splitflap-os apps** (functional + channel), loaded
  through a behavior-identical runtime and **drop-in compatible** (a conformance
  test asserts every app satisfies the contract). Tile grid, one-tap run, live
  "▶ running" state, and an **App Library** to add/remove apps or **upload your
  own** (a `.zip` of the app folder) — persisted to the data volume.
- **Manifest-driven settings** — full renderer: text/number/password/textarea/
  select/toggle, **search_chips** (live search for locations, stocks, crypto,
  timezones), stepper, inline-toggle, `visible_when`, `sync_values`, and computed
  fields. The search endpoints are served at the same paths splitflap-os uses.
- **Compose** — click-to-type grid with colour tiles; all transition styles
  (`ltr`, `rtl`, `spiral`, `sync`, `slot`, …).
- **Playlists** — sequence apps and messages with per-entry durations; save,
  load, run, loop.
- **Schedules** — time-of-day windows that run an app/playlist or turn the
  display off, per weekday, plus **quiet hours**.
- **Triggers** — apps that watch for events (ISS overhead, a game, weather) and
  briefly **interrupt** the display, with per-trigger cooldown.
- **Display tab** — the gateway's own calibration/modules/diagnostics UI
  **reverse-proxied** under one origin (`/display/*`), so it's all one app.
- **Gateway is the source of truth** — grid size + MQTT broker are read from the
  gateway's `GET /api/config` on startup and on demand.
- **Transport** — **always REST**: a whole page in one `/api/rs485/batch` request
  (no broker). MQTT is used **only** for Home Assistant, never for the display.
- **Home Assistant** — when the gateway has HA enabled, the companion publishes a
  "SplitFlap Companion" MQTT device with **App** and **Playlist** selects
  (start/stop from HA) and a **Stop** button — the companion-unique controls the
  gateway's own HA device doesn't cover.
- **Packaging** — Docker image (healthcheck + `/data` volume) and env-var config.

---

## Quick start

### Docker (recommended)

```bash
docker compose up --build
# open http://localhost:8000
```

Configure via environment (see `docker-compose.yml`): point `GATEWAY_URL` at your
gateway — that's usually all you need (and it's **required**; the app refuses to
start without it). The companion always uses REST (no broker) and reads the grid
size from the gateway.

### Run from the registry (no build)

Prebuilt **multi-arch** images are published to GitHub Container Registry, so the
same tag runs on x86 (Windows/Linux) and arm64 (Raspberry Pi 64-bit, Apple
Silicon) — Docker pulls the right architecture automatically:

```bash
docker run -d --name splitflap-companion -p 8000:8000 \
  -e GATEWAY_URL=http://192.168.1.50 -v companion-data:/data \
  ghcr.io/avandeputte/splitflap-gateway-companion:latest
```

Or with compose — set `GHCR_OWNER` in `.env`, then:

```bash
docker compose pull && docker compose up -d
```

**Publishing new images** (maintainers): push a `v*` tag (or run the *Publish
container image* GitHub Action) to build and push both architectures, or do it
from your machine with `./scripts/publish-image.sh` (see `--help`).

### Local dev

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
GATEWAY_URL=http://192.168.1.50 python -m app   # GATEWAY_URL is required; binds 0.0.0.0:8000
# open http://localhost:8000  (or http://<this-host-ip>:8000)
GATEWAY_URL=http://192.168.1.50 COMPANION_RELOAD=1 python -m app  # dev auto-reload
pytest                           # run the tests
```

> **Run it with `python -m app`, not `uvicorn app.main:app`.** The bare `uvicorn`
> command binds **127.0.0.1** (localhost only), so the companion won't be
> reachable on your LAN and the URL it registers with the gateway won't work.
> `python -m app` binds `0.0.0.0` and honors `COMPANION_HOST` / `COMPANION_PORT`.
> (If you must use uvicorn directly, add `--host 0.0.0.0 --app-dir .`.)

---

## Configuration

**There is no companion config file.** Configuration is derived at runtime from
three sources — `defaults <- gateway <- environment` — and never written to disk,
so there's nothing to seed, migrate, or back up. On restart it's all re-derived.

- **Environment** — the only things you set yourself: **`GATEWAY_URL`** (where the
  gateway is — **required**; the app refuses to start without it) and, if your
  broker needs auth for Home Assistant, `COMPANION_MQTT_PASSWORD`. Env always wins.
- **Gateway (source of truth)** — grid size and the MQTT broker/port/user/prefix
  are pulled from the gateway's `/api/config` on startup and on **Sync**.
- **Defaults** — sensible fallbacks used until the gateway answers.

The grid/MQTT env vars below still work as manual overrides (they win over the
gateway if set). Note `<data_dir>` is still a Docker volume — it holds your
**app settings, playlists, triggers and uploaded apps** (`app_settings.json` +
`apps/`), just not any companion config.

| Env var | Meaning | Default |
|---|---|---|
| `GATEWAY_URL` | Gateway base URL (REST + config sync + status + Display link). **Required** — the app won't start without it | *(none; required)* |
| `COMPANION_PUBLIC_URL` | This companion's own URL, registered with the gateway (v3.0) for its "Companion" tab | *(blank)* |
| `COMPANION_SYNC_FROM_GATEWAY` | Pull grid + MQTT from the gateway on startup | `true` |
| `COMPANION_MQTT_PASSWORD` | MQTT password for **Home Assistant only** (the gateway never exposes this) | — |
| `COMPANION_MODULE_ID_BASE` | Module id of grid index 0 (companion-owned) | `0` |
| `COMPANION_HA` | Home Assistant integration: `auto` (follow gateway) \| `true` \| `false` | `auto` |
| `COMPANION_GRID_ROWS` / `COMPANION_GRID_COLS` | Manual panel-size override | *(from gateway)* |
| `COMPANION_MQTT_BROKER` / `_PORT` / `_PREFIX` / `_USER` | Manual MQTT overrides | *(from gateway)* |
| `COMPANION_DATA_DIR` | Where app settings, playlists, triggers + uploaded apps live (no config) | `<repo>/data` |

> **Why no gateway firmware change?** The gateway (v2.1+) already exposes
> `gridRows`, `gridCols`, `mqHost`, `mqPort`, `mqUser` and `mqPfx` via
> `GET /api/config`, so the companion just reads them. The MQTT **password** is
> intentionally *not* exposed by the gateway — supply it once in the companion
> (or leave blank for an anonymous broker).

### Transport — always REST

The companion **only** drives the gateway over REST, drawing a whole page in
**one** `/api/rs485/batch` request (Gateway 3.0+). There is no transport
selector, config field, or env var — REST is the single display path, so
animations are smooth with **no broker** to run. Each incoming batch also shows
as a single **REST** row in the gateway's Monitor, above the TX frames it emits.

**MQTT is used only for the Home Assistant integration** (below), which speaks
MQTT regardless; it never carries display frames. The UI stays uncluttered while
the gateway is reachable and shows a red **Display offline** banner only if the
connection drops (with no gateway reachable at all, the preview becomes a no-op).

### Characters

Text is upper-cased in a **Windows-1252-aware** way — so `ß` and accented letters
like `É`, `Ü`, `ç` survive (`ß` is *not* expanded to `SS`) — and sent to the
gateway in the Windows-1252 encoding. The companion does **not** police
characters against a fixed set: every glyph (accents, `€`, punctuation) is passed
straight through, and each module simply blanks anything its own flap set can't
show. Emoji colour squares (🟥🟩🟦 …) still map to the gateway's colour flaps.

### Grid → module mapping

The display is filled row-major: module `= module_id_base + (row × cols + col)`.
Set rows/cols/base to match how your modules are provisioned in the gateway.

### Uploading your own apps

**App Library → Upload** takes a `.zip` of an app folder — `manifest.json` plus
`app.py` (functional) or `data.json` (channel), the same format as the built-in
apps (see [COMPATIBILITY.md](COMPATIBILITY.md) and
[the splitflap-os app guide](https://github.com/csader/splitflap-os/blob/main/APPS_README.md)).
The upload is validated (manifest + a functional app's `fetch()` must import),
written to `<data_dir>/apps/<id>/` (so it survives restarts and image upgrades,
separate from the vendored `apps/`), enabled, and loaded immediately. Uploaded
apps show a **· uploaded** tag and a 🗑 to remove them; built-ins can't be deleted.

> ⚠️ A functional app's `app.py` runs arbitrary Python on the companion host —
> only upload apps you trust. (This is the same trust model as splitflap-os
> plugins.)

### Home Assistant

With `COMPANION_HA=auto` (the default) the companion enables HA when the gateway
has HA turned on, reusing the **same MQTT broker**. It publishes one MQTT
auto-discovery device, **SplitFlap Companion**, with only the controls that are
unique to the companion — the gateway's own HA device already covers flashing a
message and reporting the display content, so those aren't duplicated:

| Entity | Type | Does |
|---|---|---|
| App | select | Run an installed app (or `Off` to stop); its state shows the running app |
| Playlist | select | Run a saved playlist (or `Off`); state shows the running playlist |
| Stop | button | Stop whatever is running |

So an HA automation can start an app or run a playlist on any trigger, and
dashboards can read which app/playlist is active from the select states. The
option lists update automatically as you install apps or save playlists.

---

## Roadmap

1. ✅ **End-to-end slice** — compose → frames → gateway (REST batch) + preview.
2. ✅ **Plugin runtime + all apps** (drop-in compatible) + Apps tab + library + settings.
3. ✅ **Playlists + schedules + triggers**.
4. ✅ **Full settings renderer + app-data helper endpoints** (search_chips).
5. ✅ **Gateway reverse-proxy "Display" tab** + live status.
6. ✅ **Packaging + docs**.

Runs against real hardware (Gateway 3.0 + physical split-flap modules) and, for
development without a gateway, in `sim` mode.
