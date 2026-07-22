# Changelog

Home Assistant shows this when an update is available. Newest first; the version headings
have to match the add-on's `version`, or the update notice comes up blank.

## 2.10.0-beta.2

- **Fast Matrix-panel apps now draw over the v3.2 stream.** On a wall running firmware 3.2, a
  frequently-redrawing frame-push app (a sweeping clock, Weather Sky, Countdown, Stock Graph in
  rotation …) runs over one persistent draw connection instead of a HTTP request per frame — much
  higher frame rates and smoother animation. Delta frames go as rect records, keyframes as full
  frames; the stream is always closed on hand-back, and any hiccup falls back to the per-frame path.
  Walls below firmware 3.2, and slow/ops apps, are unaffected.

## 2.10.0-beta.1

- **Groundwork for the Matrix-panel *canvas stream* (firmware 3.2).** The companion now detects the
  gateway's `canvas.stream` capability and ships the persistent draw-channel transport
  (`PUT /api/canvas/stream`) — one long-lived connection that carries frame/rect/ops records with no
  per-frame HTTP round trip. It is **not driving apps over it yet** (that's the next beta), so there
  is no behaviour change; walls below firmware 3.2 are unaffected.

## 2.9.2

- **Fortune-cookie panel icon** now uses an openly-licensed emoji (Noto Emoji, Apache-2.0) rather
  than a platform emoji, so it can ship freely in the add-on image.

## 2.9.1

**Channel apps come to the Matrix LED panel, a new quiz app type, two new apps, and faster, lighter
panel updates.** A physical split-flap wall is unaffected — the drive path is unchanged.

- **Channels on the Matrix panel.** Jokes, quotes, fortunes and the other channel apps can now show
  on an LED panel as large text beside a themed icon, not only on the flaps. It is on by default on a
  panel and can be turned off per app in that app's settings.
- **New app type: Quiz.** A question, then — after a short pause — its answer: a two-screen reveal.
  **Dad Jokes** is now a quiz, with a larger, tidied-up set.
- **New apps.** **Movie Quotes** shows iconic lines with the film they are from. **Stock Graph** puts
  a live quote in big type over its own price chart, for a single symbol or a rotating watchlist of
  indices and tickers.
- **Faster, lighter panel updates.** Working with Matrix Portal Gateway firmware 3.1, the gateway now
  sends only the parts of the screen that changed and reuses sprite sheets across draws, so the panel
  updates more smoothly and over far less WiFi, and on-panel text covers the full character set.
- **A quiet panel when nothing is moving.** Panel apps that change only occasionally — the date card,
  world clock and others — now redraw when something actually changes rather than on a fixed timer.
- **Reworked panel apps.** Weather, Overview, the clocks and the scoreboard were redesigned for LED —
  rich colour on a black background, clearer icons and degree signs, and a shared team and league
  picker shared by Sports and Scoreboard.

## 2.9.0

**Aligned with Matrix Portal Gateway firmware 3.0, plus new Home Assistant dashboard apps.**
A physical split-flap wall is unaffected — the drive path is unchanged.

- **Firmware 3.0.** Live preview streams over SSE (with a polling fallback), and the Matrix
  gateway no longer supplies an MQTT broker. **If you use the Home Assistant integration, set the
  broker in the add-on** (the `MQTT broker` option, e.g. `core-mosquitto`) — it is no longer read
  from the gateway.
- **Home Assistant dashboards.** Two new apps show your entity states: **HA Dashboard** (a card
  grid on the Matrix panel) and **Home Assistant** (rows on a split-flap wall). Pick entities with
  a search box, rename them, reorder them, and set numeric thresholds that colour the value
  (green / amber / red). The add-on reads states through the Supervisor proxy automatically.
- **Each on-device effect is its own app** (Plasma, Fire, Matrix rain, …) instead of one effect
  app with a picker.
- **Richer canvas apps on black.** Weather Sky, Weather Panel and the Scoreboard draw bright,
  colourful content on an unlit black background (which reads best on an LED panel); the Scoreboard
  gained real team logos and the same team/league picker as the Sports app.
- **UI:** an editable entity table (search / reorder / rename / thresholds), a custom amber
  dot-matrix marker for Matrix-panel apps, a "Matrix" filter in the app library, and richer app
  pickers in the playlist and trigger editors.

## 2.8.0

**The companion uses the Matrix panel's new canvas features (firmware 1.18+).** It reads them
from the wall's capabilities and lights up where present, falling back cleanly on an older
panel — so nothing changes for a physical split-flap or a pre-1.18 Matrix.

- **Frames cross far less WiFi.** Every canvas app (the drawn clocks, Weather Sky, Overview,
  Date Card, the image app, …) now sends its frames **QOI-compressed** wherever the wall
  accepts it — the same picture in 2–4× fewer bytes (a 256×64 frame ≈16 KB instead of 49 KB).
  That matters because the panel and the radio share one bus. It is fully transparent: no app
  changed, and a frame that will not compress falls back to raw.
- **Ticker** — a NEW app: one line scrolling across the panel, rendered **on-device** — the
  companion sends it once and the panel scrolls it smoothly itself, so it stays smooth where a
  pushed-frame crawl janked. A custom message or a live RSS feed's headlines.
- **Animation** — a NEW app: play a looping **GIF on-device**. Its frames upload once and the
  panel plays the loop itself from spare memory, so it is smooth and costs no ongoing WiFi
  (longer GIFs are sub-sampled to fit).
- **Effect parameters** — the Effects app gains **Hue** and **Density** knobs (recolour the
  matrix rain, tint plasma / Life, set the Life seed or flip-o-rama churn) where the panel
  supports them. The newer on-device effects — flip-o-rama, clock, Game of Life — appear in
  the picker automatically.

## 2.7.1

- **Image** — fixed the **Fit** mode (letterbox the whole picture into the panel).
  It was crashing internally and falling back to the demo gradient, so only **Fill**
  worked. Both fit modes work now. (This bug was present since the app shipped.)
- **Moon Phase** — no longer abbreviates "5 Days" to "5D" on a wide wall; it spells
  the day unit out wherever there is room, abbreviating only where there isn't.

## 2.7.0

The 2.7.0 line, gathered into a stable release. Everything below shipped and
soaked across the 2.7.0 betas.

**Apps that use the whole panel.** A big Matrix panel (say 256×64) has far more
room than a physical reel, and this release spends it — canvas apps that fill the
panel, and text apps that spread into a wide character grid instead of clustering
in a corner or stranding a label at one edge and its value at the other.

- **Overview** — a NEW canvas dashboard that fills a big panel: a large clock and
  the date on the left, a weather column on the right (temperature, condition,
  high/low, feels-like, humidity, wind) with a day/night sun or moon and a seconds
  sweep. It shrinks gracefully to a clock and a line of weather on a small panel.
- **Weather Sky** and **Date Card** — open into full big-panel layouts on a large
  Matrix (a rich info panel and a forecast strip; a facts column) instead of
  clustering in one corner.
- **Weather forecast** — on a wide wall it spells the forecast out: the condition
  in full ("Light rain", "Partly cloudy"), full weekdays, degree signs — laid out
  as an aligned block instead of abbreviations flung to opposite edges. A 15-wide
  wall keeps the compact form.
- **Stocks** and **Crypto** — on an ultra-wide panel each ticker/coin is one line
  (name, price and the day's change together), the whole watchlist on one page,
  instead of paging or stacking.
- **Sun Times, Tides, Metals, Exchange Rates** — centre their columns (or lay
  several across the width) instead of stranding a label and its value at opposite
  edges.
- **BTC Fear & Greed** and **Aurora** — draw a full-width gauge bar, filled to the
  value and coloured by the zone, so it reads from across the room.
- **Metro** — shows where each direction actually goes ("Forest Hills") instead of
  the cryptic "Dir0 / Dir1".
- **BirdNET** — spells species names out in full ("Northern Cardinal") when there
  is room.
- **Planes Overhead** — a one-aircraft-per-line table on a wide wall (dropping
  fields to fit, or wrapping while still packing several aircraft to a page); the
  route (from → to) from the keyed providers; on/off switches for each field; and
  it now uses your global location.
- **Dashboard** and other flap apps — pack a dense, full-width page on a tall wall.

**Clocks and settings.**

- **Art Clock** — a Clock Format setting (Auto / 12-hour / 24-hour). On Auto it
  shows AM/PM — drawn in colour flaps like the digits — on an English wall, and
  24-hour elsewhere.
- **Stocks** — a Refresh Frequency setting, plus an option to pause polling when a
  market is closed (judged per the exchange's own timezone).
- **Settings** — a "Use my location" button fills the precise-location field from
  your phone's GPS in one tap, storing the exact coordinates the location apps need.

**Fixes.** The Planes table columns no longer drift when a row's last field is
shorter; the Time app no longer drops the whole hour (showing ":30") during the
midnight hour on a 24-hour wall.

## 2.6.0

The 2.6.0 line, gathered into a stable release. Everything below shipped and
soaked across the 2.6.0 betas.

**A Matrix wall is now a canvas.** A Matrix Gateway — the split-flap firmware
ported to an LED panel — advertises a *canvas* (a real framebuffer) and on-device
effects, and the companion now uses both. A new kind of app, a **canvas app**,
draws straight onto the panel instead of returning flap pages, free of the module
grid. Canvas apps appear only on a Matrix wall; a physical split-flap has no
framebuffer, so they simply don't show there.

- **Lumina Clock** — the time as luminous colour: big anti-aliased digits with a
  glow, gradient, aurora or minimal fill, in curated palettes.
- **Weather Sky** — the weather as a scene: a sky coloured by the hour and the
  conditions, a glowing sun or moon, drifting cloud, falling rain or snow, with
  the temperature, the condition and today's high/low.
- **Countdown Bars** — a countdown as full-width colour bars, the numbers inside
  each, draining like the flap Countdown.
- **World Time** — several cities' local times at once, each on its own
  day/night-tinted row.
- **Date Card** — a big typographic date with a year-progress bar.
- **Image** — mirror any picture onto the panel in full colour.
- **Effects** — on-device plasma, fire and Matrix rain, rendered by the panel
  itself at full frame rate; the list of effects is read from what the wall
  actually advertises.

**Rich, smooth rendering.** Canvas apps draw with a real anti-aliased font and
push whole frames to the panel, so the type is crisp and the colour is the
panel's own, not the blocky flap font. App authors get a `canvas` drawing surface
— pixels, lines, rectangles, text, gradients, a bundled font, on-device effects
and whole images — documented in the wiki.

**The panel, mirrored.** The web live preview and the Home Assistant board image
now show what a canvas app is drawing, instead of the flap grid it bypasses.

**Playlists.** Drag to reorder the entries in the editor. And a canvas app — an
on-device effect especially — placed in a playlist now hands the panel back when
its turn ends, instead of staying lit forever.

**Compose from a phone or tablet.** Tapping a cell in Compose now opens the
on-screen keyboard on iOS and iPadOS, so you can type onto the wall from a
touch device.

Plus a long round of readability and layout polish across the new canvas apps.

## 2.5.0

The 2.5.0 line, gathered into a stable release. Everything below shipped and
soaked across the 2.5.0 betas.

**The whole app catalog, audited and improved.** Every built-in app was reviewed
against what the gateways can actually do, then fixed: apps stopped deleting
accents the display could show, several stopped truncating their own content on
narrow walls, colour tiles now mark severity (aurora, earthquakes, the Fear &
Greed index, the moon's illumination), and a batch of small bugs went with them.
Four channels (Magic 8 Ball, Fortune Cookie, Stoic Quotes, Shower Thoughts) gained
ten languages each, and single-page channels can now shuffle while jokes keep
their setup-then-punchline order.

**Public Holidays, rebuilt.** It runs entirely offline now from a ten-year
dataset bundled with the add-on (no API, no key) and shows four switchable
layers: public holidays for your country and province/state, religious
observances filtered by tradition, curated cultural traditions per
language-region, and an optional fun-day-a-day novelty calendar. The old
National Today app is folded into it; walls that had it installed migrate
automatically.

**Weather, sharper.** Colour swatches are balanced around a label instead of
one lonely tile, the current condition carries its own sky colour, humidity
shows on tall walls, and a five-row display fits five days of forecast on one
page. All provider quirks live in one shared weather brain.

**Countdown, Binary Clock, Exchange Rates, and more.** The countdown target is a
calendar picker, far-off dates lead with years, and multiple countdowns rotate on
a timer you set while the seconds keep ticking. The binary clock shows the plain
time on its bottom row. Exchange rates line their decimals up into a column.

**Under the hood.** A full companion-side audit hardened the app-upload path
(escaping, zip-bomb and secret-leak fixes), added continuous integration that
runs the full test suite plus Home Assistant's own validators on every change,
fixed a class of multi-display bugs, and made the engine repaint reliably after
an interruption. The Home Assistant integration (1.3.0) gained request timeouts,
live grid refresh, and stable entity IDs. Motion capability, a board-image
entity, and gateway auto-discovery from earlier in the line are all here too.

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
