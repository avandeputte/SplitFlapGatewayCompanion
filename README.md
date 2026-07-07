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

## Status

Built in phases. **Phase 1 (this drop) is a working end-to-end slice:**

- ✅ Compose a message on a click-to-type grid (with colour tiles) and send it.
- ✅ Faithful render/normalization + all splitflap-os transition styles
  (`ltr`, `rtl`, `spiral`, `sync`, `slot`, …).
- ✅ Selectable transport: **sim** (no hardware), **MQTT** (raw frames via broker),
  **REST** (gateway HTTP API).
- ✅ **Gateway is the source of truth** — the companion reads its display size
  (`gridRows`/`gridCols`) and MQTT broker (`mqHost`/`mqPort`/`mqUser`/`mqPfx`)
  straight from the gateway's own `GET /api/config`, on startup and on demand.
  You configure the panel and broker once, in the gateway.
- ✅ Live split-flap preview + transport/gateway status pill.
- ✅ Settings UI (gateway URL + sync, transport, MQTT password) + env-var config.
- ✅ Docker packaging.

**Phase 2 (this drop) adds the plugin runtime + all apps:**

- ✅ All **46 vendored splitflap-os apps** load and run (functional + channel),
  drop-in compatible — a conformance test asserts every app satisfies the contract.
- ✅ **Apps tab**: tile grid, one-tap run, live "▶ running" state, and an
  **App Library** to add/remove apps.
- ✅ **Manifest-driven settings** modal (text / number / password / textarea /
  select / toggle, with `visible_when`), persisted; `search_chips` degrade to a
  text field for now (full chip search is Phase 4).
- ✅ Faithful **play-loop**: page cycling by `loop_delay`, fetch caching by
  `refresh_interval` (+ `polling_rate` override), animation apps, OFFLINE
  fallback, and compose/app **exclusivity**.

Later phases: playlists/schedules/triggers, the full settings renderer +
app-data helper endpoints, and the gateway reverse-proxy "Display" tab.
See [the roadmap](#roadmap).

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
uvicorn app.main:app --app-dir . --reload
# open http://localhost:8000
pytest            # run the render conformance tests
```

(From the repo root the app-dir is `backend`: `uvicorn app.main:app --app-dir backend --reload`.)

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
| `COMPANION_GATEWAY_URL` | Gateway base URL (config sync + REST + status + proxy) | `http://splitflap-gateway.local` |
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
3. Playlists + schedules + triggers. ← *next*
4. Full manifest-driven settings renderer + app-data helper endpoints (search_chips).
5. Gateway reverse-proxy "Display" tab + live status.
6. Packaging polish + docs.
