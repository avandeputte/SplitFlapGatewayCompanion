# SplitFlap Gateway Companion

Drives a physical **split-flap wall** from Home Assistant: run apps (weather, clock,
stocks, transit…), build playlists and schedules, type a message straight onto the
board — and let Home Assistant, or an LLM, do the same.

The add-on is the same application the project ships as a container; it just arrives
pre-wired for HA, with the UI restyled to match and served in the sidebar.

## Before you start

You need a **SplitFlapGateway** (the ESP32 board that talks RS-485 to the modules)
reachable on your network. The gateway is the source of truth: grid size and MQTT
settings are read from it, so its URL is normally the only thing you have to enter.

## Installation

1. **Settings → Apps → App Store → ⋮ → Repositories**, and add:
   `https://github.com/avandeputte/SplitFlapGatewayCompanion`
2. Install **SplitFlap Gateway Companion**.
3. Open **Configuration** and set `gateway_url` to your gateway, e.g.
   `http://192.168.1.50`.
4. **Start**, then **Open Web UI** — it appears in the sidebar.

## Configuration

| Option | Default | What it does |
|---|---|---|
| `gateway_url` | — | **Required.** Your SplitFlapGateway's URL. Takes a comma-separated list to drive several displays: `http://192.168.1.218,http://192.168.1.50`. The first is the default display. The add-on refuses to start without at least one. |
| `mqtt_password` | *(unset)* | Only if your MQTT broker needs auth. The gateway publishes its broker/user/prefix but never the password, so it is set here. |
| `companion_public_url` | *(auto)* | This add-on's own URL, registered with the gateway so the gateway shows a "Companion" tab linking back here. Leave blank: the add-on asks Supervisor for the **host's** address and the port it is published on. (It cannot work this out by itself — from inside the container the only address it can see is its own `172.30.x.x` on Home Assistant's internal bridge, which nothing on your LAN can reach.) Set it only if you front the add-on with a reverse proxy. |
| `home_assistant` | `auto` | MQTT integration. `auto` follows the gateway's own HA setting. Publishes a *SplitFlap Companion* device with App/Playlist selects and a Stop button. |
| `vestaboard` | `false` | Answer the [Vestaboard Local API](https://docs.vestaboard.com/docs/local-api/endpoints), so anything written for a Vestaboard drives this wall instead. See below. |
| `vestaboard_key` | *(generated)* | The key clients send as `X-Vestaboard-Local-Api-Key`. Leave blank and one is generated and kept with your settings. |
| `mcp` | `false` | Expose an **MCP server**, so an LLM client can drive the display as tools. See below. |
| `mcp_token` | *(generated)* | The bearer token MCP clients send. Leave blank and one is generated. |
| `log_level` | `INFO` | `DEBUG` adds the companion's own detail (gateway sync, settings mirror, app fetches). |

## Driving the board from an automation

With `vestaboard: true`, the display answers the Vestaboard Local API — the widest
"put text on a split-flap" API there is, so HA needs nothing custom:

```yaml
rest_command:
  splitflap_message:
    url: "http://homeassistant.local:8000/local-api/message"
    method: POST
    headers:
      X-Vestaboard-Local-Api-Key: !secret splitflap_api_key
    content_type: "application/json"
    payload: '{"text": "{{ message }}"}'
```

```yaml
action:
  - service: rest_command.splitflap_message
    data:
      message: "Bin day tomorrow"
```

Read the key from the add-on's Configuration tab (or the UI's ⚙ menu). Note this
uses **port 8000 directly**, not the sidebar URL: ingress serves the *UI*, while a
`rest_command` is a separate client and needs the published port.

## Driving the board from an LLM

With `mcp: true` the add-on serves an MCP endpoint at `http://<ha-host>:8000/mcp`,
authenticated with `Authorization: Bearer <mcp_token>`. Point any MCP client at it
and the display becomes a set of tools:

| Tool | Does |
|---|---|
| `get_display` | What's on the flaps, and (mid-playlist) which app is showing and where in the rotation |
| `show_message` | Put text on the board; `seconds` shows it temporarily, then reverts to what was playing |
| `clear_display` | Blank every module |
| `list_apps` / `run_app` | The installed apps, and run one |
| `get_app_settings` / `configure_app` | Read and change an app's settings (location, tickers, …) |
| `list_playlists` / `run_playlist` | The saved playlists, and run one |
| `stop` | Stop the running app or playlist |
| `list_styles` | The flap transition styles `show_message` accepts |

There is also a native **HACS integration** (App/Playlist selects, sensors, buttons, a
`splitflap.message` service) — see the project README. It needs no MQTT and surfaces the
apps and playlists as entities.

## Notes and limits

- **The web UI is unauthenticated** — ingress means HA is the
  front door, and the `vestaboard_key` / `mcp_token` guard only their own routes.
  They are compatibility credentials, not a security boundary for the host.
- The add-on stores its settings in `/data`, and mirrors them to
  the gateway, so a reinstall restores what you had.
- Dark mode follows your browser/OS, which can disagree with Home Assistant's own
  theme setting — that choice isn't exposed to an ingress page.
