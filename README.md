# SplitFlap Gateway Companion

A web app that sends rich content — clocks, weather, stocks, sports, quotes,
animations, composed messages — to a split-flap display driven by
**[SplitFlapGateway](https://github.com/avandeputte/SplitFlapGateway)**.

The gateway (an ESP32) owns all the *hardware*: the RS-485 bus, module discovery,
provisioning, calibration and diagnostics. The companion owns all the *content*:
apps, playlists, schedules, triggers, a compose grid and a live preview. The two
run on separate machines but are meant to feel like **one integrated product** —
the companion reverse-proxies the gateway's own UI so calibration lives one click
away without being duplicated.

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
  "▶ running" state, and an **App Library** to add/remove apps.
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
- **Transports** — **sim** (no hardware), **MQTT** (raw frames via broker),
  **REST** (gateway HTTP API); honest live status pill.
- **Packaging** — Docker image (healthcheck + `/data` volume) and env-var config.

---

## Quick start

### Docker (recommended)

```bash
docker compose up --build
# open http://localhost:8000
```

Configure via environment (see `docker-compose.yml`): set `COMPANION_TRANSPORT`
to `mqtt` or `rest`, point `COMPANION_GATEWAY_URL` at your gateway, and set
`COMPANION_GRID_ROWS` / `COMPANION_GRID_COLS` to your panel.

### Local dev

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m app                    # binds 0.0.0.0:8000 (reachable on your LAN IP)
# open http://localhost:8000  (or http://<this-host-ip>:8000)
COMPANION_RELOAD=1 python -m app # dev auto-reload
pytest                           # run the tests
```

> **Run it with `python -m app`, not `uvicorn app.main:app`.** The bare `uvicorn`
> command binds **127.0.0.1** (localhost only), so the companion won't be
> reachable on your LAN and the URL it registers with the gateway won't work.
> `python -m app` binds `0.0.0.0` and honors `COMPANION_HOST` / `COMPANION_PORT`.
> (If you must use uvicorn directly, add `--host 0.0.0.0 --app-dir .`.)

---

## Configuration

Config is stored in `<data_dir>/config.json` (a Docker volume). **Environment
variables always win** over the saved file, so a container can be configured with
env alone.

Because the **gateway is the source of truth**, the only thing you normally set is
`COMPANION_GATEWAY_URL` (and `COMPANION_MQTT_PASSWORD` if your broker needs auth).
Grid size and the MQTT broker/port/user/prefix are pulled from the gateway's
`/api/config` on startup and whenever you hit **Sync**. The grid/MQTT env vars
below act as manual overrides (they win over the gateway if set).

| Env var | Meaning | Default |
|---|---|---|
| `COMPANION_GATEWAY_URL` | Gateway base URL (config sync + REST + status + Display link) | `http://splitflap-gateway.local` |
| `COMPANION_PUBLIC_URL` | This companion's own URL, registered with the gateway (v3.0) for its "Companion" tab | *(blank)* |
| `COMPANION_SYNC_FROM_GATEWAY` | Pull grid + MQTT from the gateway on startup | `true` |
| `COMPANION_TRANSPORT` | `sim` \| `mqtt` \| `rest` | `sim` |
| `COMPANION_MQTT_PASSWORD` | MQTT password (the gateway never exposes this) | — |
| `COMPANION_MODULE_ID_BASE` | Module id of grid index 0 (companion-owned) | `0` |
| `COMPANION_GRID_ROWS` / `COMPANION_GRID_COLS` | Manual panel-size override | *(from gateway)* |
| `COMPANION_MQTT_BROKER` / `_PORT` / `_PREFIX` / `_USER` | Manual MQTT overrides | *(from gateway)* |
| `COMPANION_DATA_DIR` | Where config/state live | `<repo>/data` |

> **Why no gateway firmware change?** The gateway (v2.1+) already exposes
> `gridRows`, `gridCols`, `mqHost`, `mqPort`, `mqUser` and `mqPfx` via
> `GET /api/config`, so the companion just reads them. The MQTT **password** is
> intentionally *not* exposed by the gateway — supply it once in the companion
> (or leave blank for an anonymous broker).

### Transports

- **MQTT** — publishes raw frames (`m05-A\n`) to `<prefix>/send`, reads
  `<prefix>/rx`. Smoothest for animations. Needs a broker both the gateway and
  companion can reach. (This mirrors splitflap-os's proven gateway transport.)
- **REST** — POSTs each frame to the gateway's `/api/rs485/send`. No broker
  needed; animated styles cost one HTTP request per module.
- **sim** — logs frames, drives the preview, needs no hardware.

### Grid → module mapping

The display is filled row-major: module `= module_id_base + (row × cols + col)`.
Set rows/cols/base to match how your modules are provisioned in the gateway.

---

## Roadmap

1. ✅ **End-to-end slice** — compose → frames → gateway (both transports) + preview.
2. ✅ **Plugin runtime + all apps** (drop-in compatible) + Apps tab + library + settings.
3. ✅ **Playlists + schedules + triggers**.
4. ✅ **Full settings renderer + app-data helper endpoints** (search_chips).
5. ✅ **Gateway reverse-proxy "Display" tab** + live status.
6. ✅ **Packaging + docs**.

Not yet exercised on physical hardware — the whole stack is verified in `sim`
mode; point it at your gateway to drive the real display.
