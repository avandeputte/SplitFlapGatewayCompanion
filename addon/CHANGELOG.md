# Changelog

Home Assistant shows this when an update is available. Newest first; the version headings
have to match the add-on's `version`, or the update notice comes up blank.

## 1.8.0

- **The ⚙ menu is now always there** (it used to appear only in developer mode, labelled
  "Dev"). It holds the Vestaboard and MCP switches with their keys, gateway resync, and
  the settings sync buttons. `dev_mode` now controls exactly one thing: whether
  **simulation mode** is offered in that menu — and the grid-size override appears
  directly under simulation, only while it's on.

## 1.7.0

- **The Home Assistant look is now the only look.** The companion (and, from their next
  firmware releases, the gateways) use Home Assistant's design language everywhere — light
  and dark following your system. The `theme` option and `COMPANION_THEME` variable are
  gone; if you still have `theme` in your configuration it is ignored. The split-flap board
  itself stays dark, as the physical flaps are.

## 1.6.0

- **New: a native Home Assistant integration**, installable through HACS. It adds a
  SplitFlap device with App and Playlist selects, sensors for what's on the flaps and
  which app is showing, Clear/Stop/Home buttons, and a `splitflap.message` service (with a
  timed auto-revert). Talks to this companion directly — no MQTT required.
- **The Vestaboard/MCP message tools can now show a message temporarily** — for a set
  number of seconds, after which the display returns to whatever was playing.
- **MCP: assistants can configure apps** (set a location, stock tickers, etc.) and read an
  app's settings, not just run it.

## 1.5.2

- **Fixed: the gateway's tabs (Modules, Calibration, Settings…) were missing from the
  companion's menu.** A bug hid them entirely; they're back, and open the gateway inside
  Home Assistant.
- **Global settings:** Language, Location and Timezone are now pinned to the top, in that
  order — the settings you set first, no longer buried under the weather options.

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
