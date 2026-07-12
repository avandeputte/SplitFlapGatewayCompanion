# Changelog

Home Assistant shows this when an update is available. Newest first; the version headings
have to match the add-on's `version`, or the update notice comes up blank.

## 1.5.1

- **Fixed: the Vestaboard-compatible API now works with the popular
  [ha-vestaboard](https://github.com/natekspencer/ha-vestaboard) integration** (and other
  real Vestaboard clients). Two responses didn't match a real board — the read wasn't
  wrapped in `{"message": …}`, and a successful write returned `200` instead of `201` — so
  the integration failed to set up and every message it sent reported failure. Verified by
  driving that integration's own client against the companion.

## 1.5.0

First stable release as a Home Assistant add-on.

Runs in the sidebar, restyled to match Home Assistant, configured entirely from the
Configuration tab — no environment variables, no command line.

**Drive the wall from Home Assistant**
- The full companion: apps (weather, clock, stocks, transit…), playlists, schedules and
  triggers, and a click-to-type Compose grid.
- Publishes a *SplitFlap Companion* MQTT device (App / Playlist selects, a Stop button)
  when Home Assistant integration is on.

**Drive it from an automation or an assistant**
- **Vestaboard-compatible API** (off by default): anything written for a Vestaboard —
  a `rest_command`, a script, the HACS Vestaboard integration — drives this wall
  unchanged.
- **MCP server** (off by default): an LLM client can show a message, run an app or a
  playlist, and read what's on the flaps — including which app is currently on screen.

**Seamless inside Home Assistant**
- The gateway's own configuration UI opens in the sidebar too, restyled to match — no
  leaving Home Assistant, no separate browser tab.
- Detects the host's real address so the gateway can link back to the companion.

Everything above was shaped over the 1.5.0 beta series; this is that work, stabilised.
