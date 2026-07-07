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
- ✅ Live split-flap preview + transport/gateway status pill.
- ✅ Settings UI (grid layout, transport, gateway URL, MQTT) + env-var config.
- ✅ Docker packaging.

Later phases: the plugin runtime + all 47 apps, playlists/schedules/triggers, the
app library, and the gateway reverse-proxy "Display" tab. See
[the plan](#roadmap).

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

| Env var | Meaning | Default |
|---|---|---|
| `COMPANION_TRANSPORT` | `sim` \| `mqtt` \| `rest` | `sim` |
| `COMPANION_GATEWAY_URL` | Gateway base URL (REST + status + proxy) | `http://splitflap-gateway.local` |
| `COMPANION_GRID_ROWS` / `COMPANION_GRID_COLS` | Panel size | `3` / `15` |
| `COMPANION_MODULE_ID_BASE` | Module id of grid index 0 | `0` |
| `COMPANION_MQTT_BROKER` / `_PORT` / `_PREFIX` / `_USER` / `_PASSWORD` | MQTT settings | — |
| `COMPANION_DATA_DIR` | Where config/state live | `<repo>/data` |

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

1. **End-to-end slice** — compose → frames → gateway (both transports) + preview. ← *you are here*
2. Plugin runtime + all 47 apps (drop-in compatible).
3. Playlists + schedules + triggers.
4. App library + app-data helper endpoints + full manifest-driven settings forms.
5. Gateway reverse-proxy "Display" tab + live status.
6. Packaging polish + docs.
