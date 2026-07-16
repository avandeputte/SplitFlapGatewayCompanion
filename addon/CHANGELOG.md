# Changelog

Home Assistant shows this when an update is available. Newest first; the version headings
have to match the add-on's `version`, or the update notice comes up blank.

## 2.4.0

**One weather brain.** Every provider quirk — four providers' condition-code
dialects, forecast bucketing (OpenWeather's worst-sky-of-the-day), the air
quality / UV / pollen scales, location fallbacks — now lives in the shared
weather helper; the weather app is pure presentation, half its former size.
`get_weather` grows optional arguments: `days=N` adds a normalized forecast
(a canonical sky token per day) and an hourly temperature series; `air=True`
adds AQI/UV/pollen with labels and canonical bands, so one colour map fits
every provider's scale. Forecast Ribbon rides the same helper — one cached
fetch shared across the weather apps.

## 2.3.0

**The wall states how it moves, and the companion believes it.** `/api/capabilities`
gained a `motion` key — `{"kind": "drawn" | "mechanical", "settleMs": …}` (Gateway
3.10+, Matrix Portal 1.12+). "Show seconds" and every future update-rate decision
now reads that statement instead of inferring the wall's nature from which
endpoints it happens to expose; a gateway too old to have said falls back to the
old inference. Nothing to configure — and the day a physical gateway advertises
its bulk `cells` endpoint, nothing will start ticking seconds at mechanical flaps.

## 2.2.1

**The "Show seconds" switch can now actually be switched.** 2.2.0 shipped it as a
toggle with no options — and the settings dialog draws a toggle *from* its options,
so it rendered as an empty, unclickable control and the seconds never came on. It
looked like the companion wasn't recognizing Matrix Portal walls; detection was
fine, the switch was dead. All three fields (Time, Countdown, Binary Clock) are
proper Yes/No toggles now, and a regression test fails any future manifest toggle
that declares no options.

## 2.2.0

**A binary clock.** New app: the time as a classic BCD binary clock — six columns of
colour-flap bits (tens and ones of hours, minutes and seconds) read top to bottom as
8-4-2-1. Pick the colours for 1 and 0 (0 can be a blank flap). Needs a wall at least
4 rows tall and 8 wide; a fifth row gets H/M/S labels under the columns. In the App
Library under Time.

**Countdown grows an instrument panel, and stops fidgeting.** On a wall five rows
or taller, each unit gets its own row — the value beside a colour bar of how much
of that unit's cycle remains (days of the year 🟦, hours of the day 🟩, minutes of
the hour 🟨, seconds of the minute 🟥). And in the one-line layout, the seconds
field holds a fixed width, so a 10S → 9S rollover no longer shifts everything to
its left by a flap.

**Seconds, where the wall can actually do them.** Time, Countdown and the binary
clock gain a "Show seconds" option that is honored only on a drawn wall (a Matrix
Portal): a physical module takes seconds per flip, so a ticking seconds field would
keep the wall permanently mid-clatter. Countdown used to append seconds whenever
they fit — on physical walls too; that now requires the option, so a physical
countdown ticks by the minute as it always should have.

## 2.1.0

**The Displays dialog now finds gateways for you.** Open the Displays dialog and the
companion scans the network: every SplitFlap-family gateway answers `GET /api/config` with
its grid, so the companion probes the subnets it can honestly claim to be near — where its
registered gateways live, and (as an add-on, by asking Supervisor) the host's real LAN.
One tap adds what it finds. mDNS is used as an accelerator where multicast reaches us at
all — on bare metal it does, inside a bridged container it cannot, which is exactly why
the scan is an HTTP sweep first. Scans run only while that dialog is open, never in the
background.

**Animations now default to a speed a split-flap can physically do.** Frames used to
advance every 0.25–0.6 s — but a frame can send any flap anywhere, and a module's full
revolution takes up to ~4 s, so the wall was still clattering toward one frame when the
next arrived. The built-in animations now default to 4 s per frame (and the Frame Speed
slider goes up to 10 s); an animation that doesn't declare a speed gets 4 s instead of
0.4 s. A Frame Speed you saved yourself is untouched.

## 2.0.1

**Fixes a physical Split-Flap Gateway going dark on 2.0.0.** Every page write returned 404 and
the UI reported the display offline, while the gateway itself sat there answering everything
else perfectly.

2.0.0 started asking the gateway what it can do (`GET /api/capabilities`). A physical gateway
answers with a feature list that includes **`index`** — which is `POST /api/flap/index`, "turn
ONE module to a flap by number", something every gateway has. The companion read that as the
Matrix Portal's bulk **`cells`** API (`POST /api/display/cells`) and posted every page to an
endpoint that does not exist there.

Two different endpoints, one wrong assumption. Only `cells` means the bulk page API, and that
is now the only thing the companion looks for.

It also no longer takes a wall down over it: if the cells endpoint returns 404, the gateway is
telling us plainly that it does not have it — whatever the capability list said — so the page
goes out on the legacy wire instead, with one warning in the log. A 500 is left alone, because
that means the endpoint exists and something behind it is genuinely broken.

Matrix Portal walls are unaffected.

## 2.0.0

Everything from the 1.9.0 beta series, consolidated. The headline is that the companion stopped
guessing about your wall and started asking it — and that one change is what makes the rest of
this release possible.

### The wall decides what it can show

Gateways now answer **`GET /api/capabilities`**, and the companion asks — on boot, and again on
every resync. It gets the feature list *and the actual character set of your reels*.

This matters because of how a split-flap fails. Ask a module for a character that is not printed
on its reel and it does not complain and does not substitute: it **homes**. A blank hole in the
middle of a word, reported by nothing. The companion used to send the character and hope, which
is why app text had to be written in stripped-down ASCII.

Now what your reel cannot show becomes the nearest thing it can — `Åre` → `ARE`, an em dash →
`-`, `15:30` → `15.30` on a reel with no colon, `Straße` → `STRASSE` on a reel with no ß. And
what your reel **does** carry, it keeps: on a French reel, `Prévu` finally shows as `PRÉVU`.
Those thirteen accent flaps were always there.

On a **mixed wall** (modules with different reels) it uses the intersection — a character only
half your modules carry is a character that punches holes in the other half.

### The apps stopped shouting

Apps used to write in capitals, because a split-flap has no lowercase flaps. But that is the
*wall's* business, not the app's: the companion folds the case on the way out, for the walls
that need it. So the apps now write the way people write, and a **Matrix Portal** shows them as
written — *It's five past three*. Nothing changes on a physical wall, where the output is
byte-for-byte what it always was. If you prefer capitals anyway, there is a new **Always
uppercase** setting, per display.

A Matrix Portal (firmware 1.6+) also gets its **full alphabet**: every Windows-1252 glyph, the
60 lowercase flaps, and fourteen **pictographs** (`♥ ♦ ♣ ♠ ☀ ☺ ♪ ● ■ ⌂` and four arrows). Apps
can ask what the wall can do and use them when they are there.

### Several displays, one companion

Drive **more than one gateway at once** — a split-flap in the living room and a Matrix Portal in
the office. Each has its own geometry, apps, playlists, triggers and settings. A switcher
appears in the header as soon as there is a second one.

`GATEWAY_URL` takes a comma-separated list. Everything that addresses a display can name one:
`?display=` on the API, `/gw/<id>/` for the gateway's own UI, `/local-api/<id>/message` for the
Vestaboard API, a `display` argument on every MCP tool (plus a new `list_displays`), and **one
Home Assistant device per wall** — the default keeps its historic entity ids, so existing
automations do not break.

The list of displays is backed up to your gateways along with everything else, so a rebuilt
companion comes back knowing about all of them.

### Home Assistant

The gateway's own UI now **opens inside Home Assistant**, in the sidebar, and matches its look.
The add-on follows your **Home Assistant profile language**, not your browser's.

### Tall walls

A 5×15 wall is no longer a 3×15 wall with dead rows under it. Content is **centred vertically**,
and Weather, Wikipedia, Next Holiday, World Clock, Stocks and YouTube Comments were re-laid-out
to use the space instead of paging through near-empty screens.

### Weather gets a forecast

A page of the coming days — one line each, with the day's sky as a **word** (not just a colour):
`Sunny`, `Rain-`, `Storm`. The day name shrinks before the condition does.

### New apps

- **Calendar** — the next thing you have to be at, and the one after it if the wall has the rows.
  Point it at one or more iCal feeds (comma-separated) and their events merge into one timeline.
  Recurring events are expanded, so the weekly standup actually shows up; a feed being down costs
  you its events, not the whole app.
- **Dog Facts** — the sibling of Cat Facts.
- **Forecast Ribbon** — the shape of the day painted in flap colours.

### A stopped display goes blank

It used to keep showing the last page the app happened to draw, which is worse than blank: a
clock frozen at 11:34 is not obviously *off*, it is obviously *wrong*. Stopping an app or
playlist — or a playlist simply running out — now homes every module.

### Fixes worth naming

- **Three apps were shipping shredded text.** Trivia, Chuck Norris and News Headlines filter
  their text through the flap character set, and the filter was case-sensitive — so it was
  quietly blanking every lowercase letter. Trivia had been rendering *"What is the largest
  planet?"* as `W                         ?`.
- **The French clock had a hole in it.** The `fr-FR` reel spends its flaps on the thirteen
  accents French needs and has no colon, so `15:30` reached every French wall as `15 30` — in all
  fourteen apps that show a time. French writes `15h30` anyway.
- **Translations, all nine languages, reviewed by native speakers.** The Dutch label for tree
  pollen was `Bom` — *bomb*. Norwegian's was `Tre`, which is also the numeral *three*. Portuguese
  had sleet and hail swapped. Ten strings were wider than the wall and were being silently cut.
- **Triggers were painting colour flaps through their words** — a trigger's page was treated as a
  raw colour frame, so any `r`, `o` or `y` in the text became a coloured square.
- The gateway's **logo** not loading through the companion's proxy; the gateway **tabs**
  disappearing from the top bar; **editing a playlist** no longer means retyping its name.
- **Standalone Docker**: set `COMPANION_PUBLIC_URL` to this host's LAN address. Inside a
  bridge-networked container the companion could only see its own `172.17.x.x` address, which
  your gateway cannot reach, so the gateway's *Companion* link pointed nowhere. It now says so in
  the log, and the README and compose file set it.

### Upgrading

Nothing to do. Settings, playlists and triggers are carried over, and a single-display setup
behaves exactly as it did — the switcher only appears once there is a second wall.

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
