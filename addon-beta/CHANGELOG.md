# Changelog

Home Assistant shows this when an update is available. Newest first; the version headings
have to match the add-on's `version`, or the update notice comes up blank.

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

Promoted to stable — the beta channel and the stable channel are the same build at this
release. See the stable add-on's changelog. New prereleases will appear here as
1.5.x-beta.N.

## 1.5.0-beta.8

- **The MCP server can now say which app is on screen.** While a playlist runs, an
  assistant could see *that* a playlist was playing but not *which* of its apps was up,
  so it had to guess from the flaps. `get_display` now reports the app on screen, what
  kind of thing is driving the display, and the playlist's running order and position.
  This also shows in the web UI's live view and in Home Assistant.

## 1.5.0-beta.7

**The gateway's own UI now opens inside Home Assistant, and matches its look.**

- The gateway's tabs (Modules, Calibration, Settings…) used to leave Home Assistant
  altogether — on mobile, they left the app — and its link back never returned. Home
  Assistant can only put *this add-on's* port in the sidebar, and the gateway is a
  separate device, so the companion now serves the gateway's UI itself at `/gw/`. The
  whole round trip stays in the sidebar.
- Because the gateway's page passes through the companion, it can be restyled to match
  Home Assistant. No gateway firmware update is needed.
- **Developer mode** is now a switch on the Configuration tab. It was an environment
  variable, which an add-on user has no way to set — so the Dev menu was unreachable.
- The Dev menu now shows the **address an MCP client (or a Vestaboard `rest_command`)
  must actually use**. It was showing Home Assistant's own address, which reaches
  neither.

## 1.5.0-beta.6

- **What was playing survives a restart.** Updating the add-on restarts it, and the
  display went dead: the playlist that had been running simply stopped. It now comes back
  on its own. A message you typed by hand does *not* come back — it replaced whatever was
  running, so the board is left alone.

## 1.5.0-beta.5

- The characters on the live display were hard to read at small sizes. The seam across
  each module was being drawn *over* the character, and the typeface thinned out badly.
  The glyphs are now bigger, heavier, and no longer cut in half.

## 1.5.0-beta.4

- **Fixed: the gateway could not reach the companion.** The add-on was registering itself
  with the gateway as `172.30.33.4` — its address on Home Assistant's internal network,
  which nothing on your LAN can reach, so the gateway's "Companion" tab pointed nowhere.
  It now asks Supervisor for the host's real address and the port it is published on.

## 1.5.0-beta.3

- **Fixed: the `stocks` app failed with "cannot load module more than once per process".**
  numpy 2.4 requires a CPU baseline (x86-64-v2) that many Home Assistant machines don't
  have — typically a VM with a generic CPU model, such as Proxmox's default `kvm64`. The
  add-on now ships a numpy that runs on them.
- **The menu collapses on a phone.** With the gateway's tabs added, it ran to four rows
  and pushed the display off the screen.
- **The display preview fits a phone**, and is more compact on a desktop.

## 1.5.0-beta.2

- **Fixed: the add-on would not start**, reporting `GATEWAY_URL is not set` even with the
  gateway URL filled in. It was only looking for an environment variable, and an add-on
  has none — the value lives in the Configuration tab.

## 1.5.0-beta.1

First release as a Home Assistant add-on.

- Runs in the sidebar (ingress), restyled to match Home Assistant.
- Configured from the Configuration tab; no environment variables.
- **MCP server** (off by default): an LLM client — Claude, an agent — can show a message,
  run an app, or read what's on the flaps.
- **Vestaboard-compatible API** (off by default): anything written for a Vestaboard,
  including a Home Assistant `rest_command`, drives this display unchanged.
