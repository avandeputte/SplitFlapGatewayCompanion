# Changelog

Home Assistant shows this when an update is available. Newest first; the version headings
have to match the add-on's `version`, or the update notice comes up blank.

## 2.9.0-beta.5

- **The live-preview stream is attempted behind Home Assistant ingress too.** beta.4 played it
  safe and only polled under ingress; now that the earlier blank preview is understood to have
  been a gateway-side issue, the stream runs everywhere. It's safe because the preview polls as
  its baseline and only switches to the stream once the stream actually delivers a frame — so
  inside HA you get the near-real-time preview when ingress forwards the stream, and the reliable
  poll when it doesn't. No configuration either way.

## 2.9.0-beta.4

Fixes the live preview under Home Assistant (a regression in beta.3).

- **The live preview is reliable under HA ingress again.** Ingress does not carry a long-lived
  event stream well — it stalls and can starve the ordinary requests the preview needs — which
  left the preview blank. The preview now **always polls as its baseline** and only promotes to
  the SSE stream once the stream has proven itself by delivering an event; **under ingress it
  doesn't open a stream at all** and simply polls, which is reliable there. Direct and
  reverse-proxy access still get the near-real-time stream.
- **A canvas that can't be read back no longer blanks the preview.** When the panel image
  (`/api/current_state/canvas.png`) can't be produced, the preview keeps showing the flap grid
  instead of swapping in a broken/empty image.

## 2.9.0-beta.3

Aligns the companion with **Matrix Portal Gateway firmware 3.0**, which pushes the live
display over SSE and drops MQTT from the gateway.

- **Live preview over Server-Sent Events.** The browser polled the display state a few times
  a second; it now rides a push stream (`GET /api/events`) — the preview follows the wall the
  instant it changes, and falls back to polling automatically if the stream drops. A canvas
  panel's frame still refreshes on a timer while it's up (a running effect draws its own
  frames on-device, so there is no state change to announce each one).
- **MQTT is gone from the gateway path.** Firmware 3.0 removed MQTT from the gateway, so the
  companion no longer pulls a broker or a Home Assistant switch from it — only the grid
  geometry is still synced. The companion's own Home Assistant integration stays, but its
  broker is now local: set `mqtt_broker` (add-on) or `COMPANION_MQTT_BROKER` (Docker), with
  optional port/username/password. `home_assistant: auto` brings the integration up when a
  broker is configured, off when none is. New add-on options: `mqtt_broker`, `mqtt_port`,
  `mqtt_username`.

## 2.9.0-beta.2

Two fixes from real-hardware testing of the 2.9.0 line.

- **Fix — the live preview during an on-device effect.** It showed the *previous* frame-push app's
  last frame (a clock, weather) instead of the effect that was actually playing. An effect draws
  on-device, so the companion now drops that cached frame and reads the panel back — the preview
  shows what is really lit.
- **Fix — a stale flap that lingered until its value next changed.** The companion skips re-sending
  a flap it believes is already correct, so a flap that drifted from that belief (another client on
  the same gateway, the gateway's own Compose page, a transient) was never re-asserted while the
  page held. The whole-page repaint is now on a **wall-clock bound (~15 s)** and the app loop
  re-emits a held page on the same beat, so drift heals even when nothing on screen is changing. It
  stays invisible where the cache is right — a flap already showing its value does not re-flip.

## 2.9.0-beta.1

Matrix Portal firmware **2.1** support — the LED panel learned a lot of new tricks, and the
companion learned to drive them.

- **The live preview reads the panel back.** On a firmware 1.19+ Matrix wall the live view now
  shows what is actually lit — including on-device effects, tickers and animations the companion
  never rendered a frame for (they used to preview blank).
- **A new Panel tab** (Matrix walls only) for the LED panel's own controls:
  - **Overlay ticker** — a scrolling band of text over whatever else is running, kept up until
    you clear it.
  - **Transitions** — crossfade / wipe / slide between full-panel frames.
  - **Animation library** — animations saved on the panel, replayed by name and surviving a
    reboot; upload a GIF and the panel decodes it on-device; set one as a **boot splash**.
  - **Fonts** — install custom faces the ticker and text can use.
- **Richer canvas apps.** The full draw-op set — lines, shapes, gradients, sprites, a marquee
  scroll, aligned text with custom fonts — is now available to apps, and the **Animation** app
  hands a GIF straight to the panel to decode where the firmware supports it.
- **Fix — no more stale flaps flashing on a canvas → split-flap switch.** The companion no longer
  hands the panel back to the reel wall before the replacement page is ready; the incoming page
  takes it over directly, so the pre-canvas flaps never flash.
- **Fix — a self-healing display cache.** A periodic full repaint corrects any drift between the
  companion's idea of the wall and what is really on it (another client, the gateway's own
  compose page, a reboot) — a stale flap no longer lingers until it happens to change. It is
  invisible where the cache is right: a flap already showing a value does not re-flip.

## 2.8.0

The 2.8.0 line promoted to a stable release — see the stable
add-on changelog for the full summary. No code change from beta.1.

## 2.8.0-beta.1

**Uses the Matrix panel's new canvas features (firmware 1.18+).** The companion reads them
from the wall's capabilities and lights up where present, falling back cleanly on an older
panel:

- **QOI compression** — every canvas app now sends its frames QOI-compressed where the wall
  accepts it: the same picture over 2–4× less WiFi (a 256×64 frame ≈16 KB instead of 49 KB),
  which matters because the panel and the radio share one bus. Fully transparent — no app
  changed, and a frame that won't compress falls back to raw.
- **Ticker** — a NEW app: one line scrolling across the panel, rendered on-device (sent
  once, the panel scrolls it smoothly itself). A custom message or a live RSS feed's
  headlines.
- **Animation** — a NEW app: play a looping GIF on-device. Its frames upload once and the
  panel plays the loop itself from PSRAM, so it's smooth and costs no ongoing WiFi (longer
  GIFs are sub-sampled to fit).
- **Effect parameters** — the Effects app gains **Hue** and **Density** knobs (recolour the
  matrix rain, tint plasma / Life, set the Life seed or flip-o-rama churn) where the panel
  supports them. The newer on-device effects (flip-o-rama, clock, Game of Life) appear
  automatically.
- Under the hood, `canvas` helpers for on-device animation, a scrolling ticker,
  single-rectangle updates and effect parameters, so future apps can reach them too.

## 2.7.1

- **Image** — fixed the **Fit** mode (letterbox the whole picture into the panel):
  it was crashing internally and falling back to the demo gradient, so only **Fill**
  worked. Both fit modes work now. (Present since the app shipped.)
- **Moon Phase** — no longer abbreviates "5 Days" to "5D" on a wide wall; it spells
  the day unit out wherever there is room.

## 2.7.0

The 2.7.0 line promoted to a stable release — see the stable
add-on changelog for the full summary. Only the (over-long) Matrix
app descriptions were trimmed since beta.12.

## 2.7.0-beta.12

- **Time** — fixed the clock showing `:30` instead of `0:30` during the midnight
  hour on a 24-hour wall. Trimming the leading zero for a cleaner `9:30` was eating
  the whole hour when it was `00`.

## 2.7.0-beta.11

- **Planes Overhead** — fixed the column alignment in the table. Rows with a shorter
  last field (e.g. altitude `A38K` vs `A4050`) were being re-centred a column over, so
  the columns drifted down the page. Every row is now the same width, and the distance
  is aligned inside its column too — the number flush right so the decimals line up,
  the compass direction flush left.

## 2.7.0-beta.10

**Planes Overhead** got route info and a much more adaptive display:

- **Route (from → to)** — each aircraft can now show where it's coming from and
  going to, e.g. `PIT→SFO`. This comes from the route in the keyed providers
  (FlightAware / FlightRadar24 / AirLabs / AviationStack); OpenSky's free feed has
  no route, so it's blank there.
- **Pick your fields** — new on/off switches for Route, Distance, Altitude and
  Speed (the callsign is always shown), so you choose what appears.
- **Smarter fit** — the display now prefers to DROP a field rather than wrap: it
  shows as many of your chosen fields as fit on one line per aircraft, packing
  several aircraft to a page. If your selection genuinely can't fit one line, it
  wraps to two lines per aircraft but STILL packs several per page, instead of
  falling back to one aircraft on a near-empty page.

## 2.7.0-beta.9

- **Planes Overhead** — now uses your **global location** (the Location in the main
  settings, shared with weather / tides), instead of its own separate lat/lon box.
  That box is still there as an optional per-app override — leave it blank to follow
  the global location.
- **Planes Overhead** — the wide-screen table now shows **one plane per line** on
  more panel widths: it always shows the callsign and distance, then adds altitude
  and speed as the width allows, instead of only switching to a table when all four
  columns fit.
- **Settings** — the precise-location field has a **"📍 Use my location"** button.
  On a phone (or any device with location services) it fills in your exact
  coordinates in one tap, via the browser's geolocation.

## 2.7.0-beta.8

- **Art Clock** — the AM/PM on a wide wall is now drawn in colour flaps like the
  digits (a slightly smaller 3×3 letter), instead of as plain text.

## 2.7.0-beta.7

- **Stocks** — a new "Pause When Markets Closed" option (on by default). yfinance's
  quick feed carries no open/closed flag, but it does carry each stock's exchange
  timezone, so the app now knows when a market is shut and stops polling it overnight
  and on weekends — showing the last prices meanwhile — instead of hammering the
  feed around the clock. A watchlist spanning several exchanges keeps refreshing as
  long as any one of them is trading. Turn it off to always refresh on your schedule.

## 2.7.0-beta.6

A sweep of apps that were wasting a wide Matrix panel, plus two clock tweaks:

- **Sun Times, Tides, Metals, Exchange Rates** — stopped stranding a label at one
  edge and its value at the other with a lake of empty space between. Sun Times and
  Tides now centre the label/value block; Metals puts both metals on one line;
  Exchange Rates lays several currencies out in columns across the width.
- **BTC Fear & Greed** and **Aurora** — on a wide wall each draws a full-width gauge
  bar, filled to the value and coloured by the zone (green→red), so it reads from
  across the room instead of a few characters in the middle.
- **Metro** — shows where each direction actually GOES ("Forest Hills", "Oak Grove")
  instead of the cryptic "Dir0 / Dir1".
- **BirdNET** — spells the species out in full ("Northern Cardinal") when the wall
  has room, abbreviating only when it must.
- **Planes Overhead** and **Sports** — on a wide wall each becomes a table: one
  aircraft (callsign / distance / altitude / speed) or one game (league / score /
  status) per row, several to a page, instead of one item on a near-empty page.
- **Art Clock** — a new Clock Format setting (Auto / 12-hour / 24-hour). On Auto it
  shows AM/PM on an English wall (where there's width for it) and 24-hour otherwise.
- **Stocks** — a Refresh Frequency setting, so you can change how often prices
  update (default 60s).

## 2.7.0-beta.5

Two apps that were wasting a wide Matrix panel:

- **Weather** — the forecast page stops abbreviating on a wide wall. Instead of
  "Wed Rain- ..... 78/61" (day and temps flung to opposite edges), it spells the
  condition out ("Light rain", "Partly cloudy", "Heavy snow"), gives the temps
  degree signs, uses full weekday names where there's room, and lays the days,
  conditions and highs/lows out as an aligned block centred on the wall. A 15-wide
  wall keeps the compact form it has always used.
- **Crypto** — on an ultra-wide panel each coin is now one line (ticker + price +
  the day's change together), so the watchlist is a page of one-liners instead of
  the name/price/change stacked over three rows — the same treatment stocks got.
  A narrow wall keeps the stack.

## 2.7.0-beta.4

- **Stocks** — on an ultra-wide Matrix panel the ticker, its price AND the day's
  change now sit together on one line, so the whole watchlist is a single page
  instead of flipping between a price page and a change page. The prices line up
  in a column and the changes line up in a column, to read straight down. A
  narrower panel (or a split-flap), where all three won't fit, keeps the two-page
  price-then-change split.

## 2.7.0-beta.3

- **Overview** — on a big panel the bottom humidity/wind line was clipped off the
  edge: the taller font from beta.2 pushed the five-line weather column past the
  bottom of the panel. The column now fits itself to the panel — if the day's
  readings make it tall (a long condition word, three-digit values), the whole
  column shrinks together so the last line always stays fully on screen.

## 2.7.0-beta.2

Readability fixes on the apps from beta.1:

- **Overview** — the humidity/wind line at the bottom of the weather column was
  too small to read. It now drops the word labels (the `%` and the mph/km-h unit
  already say which is which) and uses that room for a taller font.
- **Stocks** and **World Clock** — on a wide panel the two columns were flung to
  opposite edges with a lake of space between them. They now stay together as a
  block in the middle, so you can read a ticker across to its price, or a city
  across to its time, at a glance. The value column still lines up down the page.

## 2.7.0-beta.1

**Apps that use a big Matrix panel.** On a large panel (say 256×64) most apps used
to leave the space empty. Now:

- **Overview** — a NEW canvas app: a drawn dashboard that fills a big panel — a
  large clock and the date on the left, a weather column on the right
  (temperature, condition, high/low, feels-like, humidity, wind) with a day/night
  sun or moon and a seconds sweep. It shrinks gracefully to a clock + a line of
  weather on a small panel.
- **Weather Sky** — on a big panel it opens into a full info panel: feels-like,
  humidity and wind beside the temperature, and a three-day forecast strip across
  the bottom (instead of clustering in the left third).
- **Date Card** — on a big panel it adds a facts column: the ISO week, the day of
  the year, and how many days of the year remain.
- **Dashboard** (the flap app) — on a tall wall (5+ rows) it now drops the time
  and weather onto one dense page that spreads to the edges, instead of two sparse
  three-line pages floating in a big grid.

## 2.6.0

The 2.6.0 line promoted to a stable release — see the stable
add-on changelog for the full summary. No code change from beta.10.

## 2.6.0-beta.10

**Compose now works on iPhone and iPad.** Tapping a cell in the Compose tab
opened no keyboard on iOS — a focused grid cell (a `<div>`) never triggers the
on-screen keyboard. Compose now routes typing through a real (hidden) text input,
so the keyboard appears and you can type onto the wall from a phone or tablet.

## 2.6.0-beta.9

**Canvas app readability fixes.**

- **Weather Sky** — all the text now sits in a left column over a dark scrim, so
  it reads clearly even on a bright day sky (the light-on-light contrast is gone),
  and it's a clean place / temperature / condition·high·low stack with no
  overlap. The sky, sun or moon still shine on the right.
- **Date Card** — dropped the tinted background; it's solid black now.
- **Countdown Bars** — removed the lines between the bars.

## 2.6.0-beta.8

**The live preview and the Home Assistant board image now show canvas apps.**
While a Matrix-panel app (the clock, weather, and the rest) is drawing, both used
to show the stale flap grid it bypasses — now they show the panel's actual frame.
(An on-device effect has no frame the companion can see, so it still shows the
flaps.)

- **Weather Sky** — the high/low are right-aligned to the edge, so a 3-digit
  temperature no longer runs off the screen.
- **Countdown Bars** — the event title no longer clips (top or bottom) and is a
  touch smaller; the *elapsed* part of each bar is now solid black, like the
  flap Countdown, instead of a dim colour.

## 2.6.0-beta.7

**Canvas apps: more polish, and a switch-back fix.**

- **Fixed:** switching from a Matrix-panel app back to an ordinary app could leave
  the wall stuck — the flap display's "unchanged cell" cache went stale while the
  canvas app drew straight to the panel, so cells that matched it were skipped and
  never repainted. Leaving canvas mode now forces a full repaint.
- **Canvas apps stand out in the library** — the whole tile takes a tinted shade
  and border, not just a small marker, so you can tell Matrix-panel apps at a glance.
- **Countdown Bars** — the event-title font is smaller and no longer clips at the
  bottom; the bar numbers have more breathing room too.
- **Weather Sky** — the place name gets its own line, so a long city (e.g.
  "Mt Lebanon") no longer collides with the condition; high/low sit on one line
  below it.
- **Removed the News Ticker** — smooth horizontal scrolling isn't achievable over
  the panel's frame-push path, so it never looked good enough to keep.

## 2.6.0-beta.6

**Effects picker now follows the wall.** The Effects app's list of effects is no
longer a hard-coded plasma / fire / matrix — it's read from what the Matrix panel
actually advertises (GET /api/capabilities), so if the firmware gains or renames
an effect, the picker shows exactly what that panel can do (with a sensible
fallback where a wall advertises none).

## 2.6.0-beta.5

**Canvas apps: crisper type and a round of polish from real-panel feedback.**

- **Crisp text everywhere.** The Matrix-panel apps now render their type without
  anti-aliasing — hard-edged pixels that stay sharp on the LEDs instead of the
  soft grey fuzz the smoothed font left at these sizes.
- **World Time** — dark, high-contrast rows; city names shown in full (no more
  "New Y…"); the sun/moon icons are gone (the day/night cue is the tint and the
  coloured left stripe).
- **News Ticker** — scrolls faster, the text is smaller (more of the line on
  screen), and it's pure white on black for maximum contrast.
- **Countdown Bars** — the bar tracks are much darker so the fill and numbers
  stand out, the numbers are outlined for legibility over any colour, and the
  event name now sits on plain black with no bar behind it.

## 2.6.0-beta.4

**A whole shelf of new Matrix-panel apps, and the clock redesigned.** Canvas apps
now render with a real anti-aliased font, so the panel's full pixel definition is
put to work — smooth type, gradients and glow instead of a blocky grid.

- **Lumina Clock** (replaces the old canvas Aurora Clock) — big smooth digits in
  four treatments (Glow / Aurora / Neon / Minimal) over curated palettes that
  never go pink. 12h/24h, a smooth seconds bar.
- **Weather Sky** is now a colourful scene: the sky's colour is the hour *and*
  the conditions (deep-blue nights with a glowing moon and coloured stars, warm
  dawn and dusk, greying over for cloud and rain), with the temperature, the
  condition, and today's high/low — not just the temperature.
- **News Ticker** — a smooth scrolling news crawl from any RSS feed.
- **Date Card** — a big typographic date with a year-progress bar.
- **World Time** — several cities at once, each badged with a day/night sun or moon.
- **Countdown Bars** — a countdown as full-width colour bars with the numbers
  inside each bar.

**Playlists**
- **Drag to reorder** items in the playlist editor — grab a row's handle and drop
  it where you want it.
- **Fixed:** an on-device effect placed in a playlist stayed lit forever — it was
  never handed back when its slot ended. Canvas apps in a playlist now take the
  panel over and release it properly between items.

## 2.6.0-beta.3

**A new canvas app: Aurora Clock** — a much richer take on the flap *Art Clock*.
On a Matrix wall it paints time as flowing colour: a living aurora of rippling,
drifting bands behind big two-tone digits (hours one colour, minutes another),
a blinking colon, and a smooth seconds bar sweeping along the bottom. On the
default **Daylight** theme the whole palette rotates through the spectrum over
the course of a day, so the colour alone hints at the hour; **Spectrum**,
**Ocean** and **Ember** round out the themes. 12h/24h and a timezone override.

The old **Analog Clock** canvas app has been removed.

## 2.6.0-beta.2

**A new canvas app: Weather Sky.** The weather, drawn instead of spelled. On a
Matrix wall it paints an animated sky for the current conditions — a sun whose
rays slowly turn, clouds that drift, rain that falls and snow that wobbles down,
a lightning flash in a storm, a moon and stars at night — with the temperature
in a big colour that runs from icy blue to hot orange. It reads the same live
weather and location as the ordinary Weather app. Like every canvas app it
appears only on a wall that has a panel.

Animated canvas apps also got smoother: the redraw floor dropped so an app can
pick its own frame rate (up to the panel's ~8 fps ops ceiling) instead of being
capped at five.

## 2.6.0-beta.1

**Draw anything on a Matrix wall.** A Matrix Gateway can now do far more than
imitate flaps — it advertises a *canvas* (a real framebuffer) and on-device
visual effects, and the companion uses both. Three new apps, shown only on a
Matrix wall that has a panel:

- **Effects** — plasma, fire, and Matrix rain, rendered by the panel itself at
  full frame rate.
- **Analog Clock** — a real clock face with sweeping hands, drawn pixel by
  pixel (something a flap grid can never show).
- **Image** — mirror a picture onto the panel in full colour.

For app authors, a new `canvas` drawing surface lets an app paint pixels,
lines, rectangles, text, an on-device effect, or a whole image straight to the
panel, free of the flap grid. On a physical split-flap wall none of this
applies — those apps simply don't appear.

## 2.5.1-beta.2

**A shared text layout, and a tidier binary clock.** The advice, quote, cat/dog
fact, and random-fact apps each carried their own copy of the same
"balance the words evenly across the lines" logic — it now lives in one place
and the apps just ask the engine to lay their text out. No visible change,
just less to go wrong. The Binary Clock's plain-time row now lines its digits
up directly under the binary columns.

## 2.5.1-beta.1

**Channel apps write text, not line breaks.** The quote / joke / fortune
channels are restructured: each entry is now the full text of a page, and the
display wraps it to your wall — so the same joke reads correctly on a narrow
sign and a wide one without anyone pre-splitting it. Multi-page items (a joke's
setup and punchline, a two-page quote) are grouped in the data, so shuffling a
channel can never separate a punchline from its setup. With that guarantee, the
quote channels shuffle by default while jokes keep their order.

## 2.5.0

The 2.5.0 line promoted to a stable release — see the stable
add-on changelog for the full summary. No code change from beta.5.

## 2.5.0-beta.5

**A round of app polish across the catalog.**

- **Countdown** now rotates between your countdowns on a timer you set
  (Seconds each countdown is shown), and the seconds keep ticking while it
  does — the two were tangled before. It also opens a calendar picker for the
  target date and leads with years for far-off dates ("8Y 267D 14H").
- **Binary Clock** shows the plain time on the bottom row — the answer key
  under the puzzle.
- **Exchange Rates** line their decimal points up into a readable column.
- **Public Holidays** folds its cultural traditions into the same per-locale
  data files as the official holidays — one file per locale, still switchable
  by category (public / religious-by-tradition / cultural / fun-day).
- **Weather**: the colour swatches are balanced on both sides of a label
  instead of one lonely tile, the current condition carries its own sky
  colour, humidity shows on tall walls, and a five-row display fits five days
  of forecast on one page.
- **Channel apps** can shuffle: single-page channels (quotes, fortunes, 8-ball
  answers, morning/night greetings) now play in random order, while jokes keep
  their setup-then-punchline order. Authors control it with one manifest field.

## 2.5.0-beta.4

**One calendar app, honest categories.** National Today is gone — folded into
Public Holidays, which now works entirely offline from a ten-year dataset
bundled with the app (185 language-region locales, built from python-holidays)
and shows four layers, each with its own switch:

  * **Public holidays** — always, for your country and province/state;
  * **Religious observances** — off by default, and when on, filtered by
    tradition (Christian, Islamic, Jewish, Hindu, Buddhist, Sikh);
  * **Cultural traditions** — on by default: April Fools', Burns Night,
    Nikolaus, la Befana, Dia de los Muertos... curated per language-region, so
    a French wall in Montreal reads Fete du Canada and a Flemish wall in
    Antwerp gets its own Dutch names;
  * **A fun day, every day** — off by default: the "National Donut Day"
    novelty calendar, there when you want it, out of the way when you don't.

The layers know the difference between a day off, a feast, a folk custom and a
novelty — a tradition that merely shares a date with a holiday (Fete du Muguet
on Labour Day, la Befana on Epiphany) is kept; one the holiday layer already
carries is not shown twice. A wall that had National Today installed — or in a
playlist — is migrated to Public Holidays automatically.

**Countdown rotation speed** is now a setting (it was pinned at one second with
no way to change it), and the **Countdown target field opens a real calendar
picker** instead of demanding a hand-typed ISO date — every app can now declare
`datetime-local` / `date` / `time` settings. A far-off countdown leads with
years ("8Y 267D 14H") instead of a four-digit day count.

## 2.5.0-beta.3

**A countdown you can set from a calendar, to a date years away.** The
Countdown target field now opens the browser's native date & time picker —
it was always meant to (the manifest said so); the form just fell back to a
bare text box that gave no hint it wanted an ISO date. Dates saved before
the picker existed still load.

And a target thousands of days out finally reads like one: past a year,
years lead — "8Y 267D 14H" instead of "3187D 14H 22M" — with the day total
kept on signs too narrow for both. The tall-wall instrument panel gains a
years row (of a decade), its day bar moves daily again (days within the
year), and the panel can no longer show "999D" for six straight years —
the clamp that produced that lie is gone by construction. On a five-row
wall the ticking seconds yield to the years row; under a year, everything
is exactly as before.

Any app can now declare `datetime-local` / `date` / `time` settings and get
the native picker — documented in Writing-Apps.

## 2.5.0-beta.2

**The companion itself, audited the way the apps were.** Backend, web UI,
engine, integration, packaging and tests — every finding executed
(docs/BACKEND_AUDIT_2026-07.md).

*Closed before anyone hit them.* An uploaded app's name could run script in
the companion's own page (manifest text now escaped everywhere it is shown);
a crafted zip could balloon into RAM on upload (now capped by UNCOMPRESSED
size); /api/config no longer returns the Vestaboard key, enablement token or
MCP bearer token in the clear.

*The wall stops going stale.* The engine repaints after a failed send or a
trigger interruption instead of believing the page is still up — a rebooted
gateway or an ended trigger used to leave the wall frozen until the content
happened to change. Manual messages survive a trigger ending; back-to-back
temporary messages replace instead of queueing; the slot animation's spin
now survives on both gateway protocols.

*Two walls, actually separate.* Six places quietly acted on the DEFAULT
display no matter which wall you had selected — worst of all, the dev-menu
settings pull, which could overwrite one wall's settings with another's.
All scoped correctly; each gateway's status page now shows what ITS wall is
running; one wall's settings push no longer pauses the other's; failing
triggers back off instead of polling harder.

*Faster and politer.* One pooled connection per gateway (an ESP32 has about
four sockets — the companion used to open a fresh one per request, per
heartbeat); geocoding failures are remembered briefly instead of hammering
Nominatim on every refresh; the legacy protocol only resends modules that
changed; the UI stops rebuilding what didn't change and is keyboard-usable
(tiles, pickers and the dialog).

*Home Assistant integration (1.3.0).* Timeouts, so a black-holed companion
can't hang setup for minutes; a resized wall recovers without restarting HA;
entity IDs migrate to stable ones so re-adding the integration keeps your
history and customisations.

*And the guardrails.* The repo now runs its full test suite — 3,814, with a
guard that fails any test touching the live network — plus Home Assistant's
own validators, on every push. main.py shrank from 1,800 lines to 900 with
routes split into focused modules.

## 2.5.0-beta.1

**The whole app catalog, audited and brought up to what the platform can do.**
Every app — all 64 — was audited against the gateways' capabilities; this release
executes the findings.

*Platform.* `get_location()` now carries `lat`/`lon`/`city` (the one cached
geocode, shared with weather), so no app needs its own Nominatim ladder.
Triggers opt into the same injected helpers as `fetch()` — `caps`, `i18n`,
`get_weather`, `get_location` — by parameter name; the two-argument form stays
the splitflap-os contract. And `i18n.tz()` plus one blessed guarded snippet
replace eighteen hand-rolled timezone parses (fallback standardized on UTC).

*Bugs out of the wall.* Three apps stopped deleting accents the renderer could
have shown (News, On This Day, Trivia). Comments renders the comment as typed,
not its HTML (`&#39;` on flaps, no more). Time Since ticks seconds only on a
wall that repaints, fits its line to the wall, and gains a start-date row on
tall walls. Crypto shows tickers (BTC, ETH) instead of mangling id slugs into
"BITCOI". ISS coordinates now use hemisphere letters ("41.00S 123.45W") instead
of a 25-character line no small wall could show. Fear & Greed no longer drops
its own index on short walls. Sarcastic Fortune Cookies re-wraps when the wall
changes shape. BirdNET stops polling its Pi every second and no longer ships a
private LAN IP as a product default. Metro's code default matches its dialog.
Stock/crypto triggers: every direction option now works with every condition
type. Moon Phase drops timezone code that cancelled itself out. YouTube says
what it actually shows — real subscribers with an API key, latest uploads
without.

*Honest minimums.* All twelve channels declared `min_cols: 10` over 15-wide
data — every page truncated on a 10-column wall. Now 15, and a conformance test
keeps any channel's declared minimum at least as wide as its data. Stocks,
Crypto, Metals, ISS, Art Clock, Fear & Greed corrected too; Sun Times,
Earthquakes and Rocket Launch advertise the 1-row layouts they already had.

*More from the wall you own.* Severity colours (coloured pixels on a matrix
wall, colour flaps on a physical one): aurora Kp, earthquake magnitude, Fear &
Greed sentiment, and the moon's illumination bar. Tall walls: On This Day shows
up to three events, Formula 1 shows the standings column, Time Since shows the
start date. Countdown asks `caps.can_show()` instead of reaching into host
internals.

*Speak your language.* Magic 8 Ball, Fortune Cookie, Stoic Quotes and Shower
Thoughts join the localized channels — ten languages each, forty new data
files, every line checked against that language's actual reel (a new test; it
also caught and fixed five off-reel characters in existing data). Random Fact
follows your Language to the facts API (English/German). Livestream, Metro,
YouTube, Earthquakes and National Today localize their chrome; National Today
also uses the catalog's holiday-name translations.

The Writing-Apps wiki gained the distilled **house rules** — one truthy parser,
one timezone snippet, errors that raise, minimums as a contract, the i18n badge
as a promise, and never filtering characters the renderer handles better.

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

## 1.9.0-beta.24

**Standalone Docker: set `COMPANION_PUBLIC_URL`.** Outside Home Assistant there is no
Supervisor to ask where we live, so the companion works its own address out by opening a socket
toward the gateway — and inside a bridge-networked container that address is `172.17.0.x`,
which is the container's own address on the Docker bridge. Your gateway is a device on the LAN
and cannot reach it, so the "Companion" link on the gateway pointed nowhere.

The reachability check could never have caught this, and it is worth saying why: it probed the
URL *from inside the container*, where `172.17.0.x` is reachable because it **is** us. The
check passed and the URL was still useless. It now looks at the address instead, and warns in
the log, naming the fix.

The README and `docker-compose.yml` now set `COMPANION_PUBLIC_URL`; the install script already
did. **Nothing else was affected** — driving the display, the apps, settings sync, the gateway
proxy, multiple displays and the UI all worked either way.

## 1.9.0-beta.23

**The wall now says what it can show, and the companion listens.** Gateways answer a new
`/api/capabilities`, so the companion no longer guesses a display's alphabet from its product
name. It asks — on boot and on every resync — and gets the real answer, including a physical
wall's actual reel.

That matters because of how a split-flap fails: ask a module for a character that is not
printed on its reel and it does not complain and does not substitute — it **homes**. A blank
hole in the middle of a word, reported by nothing. So now anything your reel cannot show is
turned into the nearest thing it can: `Åre` → `ARE`, an em dash → `-`, `15:30` → `15.30` on a
reel with no colon, `Straße` → `STRASSE` on a reel with no ß. And what your reel *does* carry,
it keeps — on a French reel, `Prévu` finally shows as **`PRÉVU`**. Those thirteen accent flaps
were always there.

**Two new apps.**

* **Calendar** — the next thing you have to be at, and the one after it if the wall has the
  rows. Point it at one or more iCal feeds (comma-separated); their events merge into one
  timeline, and a feed being down costs you its events, not the whole app. Recurring events
  are expanded properly, so the weekly standup shows up.
* **Dog Facts** — the sibling of Cat Facts.

**The apps stopped shouting.** They wrote in capitals because a split-flap has no lowercase
flaps — but that is the wall's business, not theirs, and the companion already folds the case
for the walls that need it. On a Matrix Portal they now read as they were written: *It's five
past three*. Nothing changes on a physical wall.

Fixing that turned up **three apps that were shipping shredded text**: Trivia, Chuck Norris and
News Headlines filter their text through the flap character set, and a case-sensitive filter
was quietly blanking every lowercase letter. Trivia has been rendering "What is the largest
planet?" as "W                         ?". They are readable again.

**Translations.** A native-speaker pass over all nine languages. The Dutch label for tree
pollen was `Bom` — *bomb*. Norwegian's was `Tre`, which is also the numeral *three*. Portuguese
had sleet and hail swapped. Ten strings were wider than the wall and being silently cut
("Naechster Feier"). Accents are spelled properly now, because the reels carry them.

The **French clock** was broken everywhere: the fr-FR reel has no colon, so `15:30` reached
every French wall as `15 30`, with a hole in it, in all fourteen apps that show a time. French
writes `15h30` anyway.

**Also:** the display switcher was an unreadable dark box in the Home Assistant theme, and
"Remove a display" — the one destructive control in the app — was styled as the primary blue
button. Both fixed. The German UI was half formal and half informal, and a review of it found a
setting whose description said the opposite of what it does.

## 1.9.0-beta.22

**A stopped display goes blank.** It used to keep showing the last page the app happened to
draw — which is worse than blank: a clock frozen at 11:34 is not obviously *off*, it is
obviously *wrong*, and the longer it sits there the more it looks like the thing is still
working.

Blanking **is** homing: flap 0 is the blank flap, so every module returns home. (The Home
button is still there for a physical re-home.)

Both ways of ending up with nothing running now blank the wall: you stopped it (the Stop
button, Home Assistant, an MCP call), **or** a playlist that does not loop simply ran out.
Switching from one app to another does not flash a blank in between.

## 1.9.0-beta.21

**Apps can now ask what the display can show**, and the first pictographs are in use.

- **Stocks and Crypto** show **↑ / ↓** for the day's direction. It used to be a colour only —
  which is nothing at all if you have colours turned off. The arrow carries the meaning and
  the colour reinforces it. On a real split-flap the arrow becomes `^` / `v`, which still reads.
- **Tides** shows **↑ / ↓** instead of HIGH / LOW on a Matrix Portal, which frees the room the
  time and the height wanted. A real reel keeps the words, because there a ↑ would come out
  as `^` — and that is not what a tide table should say.

An app declares `caps` and gets told: `lowercase`, `pictographs`, `named_colours`. It is
optional, so an app that never heard of it is called exactly as before.

## 1.9.0-beta.20

**New setting: "Always uppercase"** (Global settings). Show everything in capitals even on a
display that *can* render lowercase — for when you prefer the classic split-flap look.

It is **per display**, so one wall can shout while another does not, and it is stored with
that display's settings, which means it is backed up to that display's gateway like
everything else.

It costs nothing else: a Matrix Portal told to shout is still driven by the index-addressed
API, still shows its pictographs, and still gets its colours by name. It is simply in
capitals — `Hi ♥ 🟥` becomes `HI ♥ [red]`, and `café` becomes `CAFÉ` with its accent intact.

## 1.9.0-beta.19

**The forecast now says what the weather will be**, not just what colour it is.

    |    FORECAST   |          |       FORECAST       |
    |Tue Sunny 89/71|          |Y Tue Sunny      89/71|
    |Wed Rain- 86/70|          |B Wed Rain-      86/70|
    |Thu Storm 79/66|          |R Thu Storm      79/66|
      a 15-wide wall             a 22-wide wall: the colour comes back too

A colour tells you "wet"; it does not tell you drizzle from a downpour. Light and heavy are
a `-` or `+` suffix rather than separate words, so every language keeps its own noun and the
sign means the same thing everywhere — `Pluie-`, `Regen-`, `Nieve+`.

The whole page picks ONE format, from its longest condition, so the columns line up. The day
gives up a letter before the condition does (`We` is still Wednesday; a truncated condition
is not a condition), and the colour flap is spent only when it costs nobody a letter.

Translated into all nine languages.

## 1.9.0-beta.18

**Fixes triggers painting colour flaps through the words.** A trigger's page was treated as
an *animation* — where a lowercase r, o, y, g, b, p or w means a COLOUR FLAP. That was
harmless while every app SHOUTED its own output, so no lowercase could reach it. The apps
stopped doing that in beta.13, and since then "Partly cloudy" has been arriving with a red,
an orange and a yellow flap in the middle of it. Triggers now show words; only a real
animation paints.

**Also fixes the Compose editor shouting at you** on a Matrix Portal: it uppercased the
preview whatever wall you were on, so it lied about what the wall was going to show.

Under the hood, the two device types are now one idea in one file, which is what surfaced
both bugs.

## 1.9.0-beta.17

**Weather gets a forecast.** A page of the coming days — one line each, the day's sky as a
colour flap and its high/low lined up in a column you can read down.

    |    FORECAST   |
    |# Wed     89/71|      # = a colour flap: yellow sun, white cloud,
    |# Thu     86/70|          blue rain, purple snow, red storm
    |# Fri     79/66|

The sky is a **colour** rather than a picture because a colour is the only weather icon
every wall can show: the flap reel has no cloud and no raindrop, but it has had seven
colours since the beginning. Set **Forecast days** in the weather app's settings (off, 3, 4
or 5 — three by default). Works with all four providers.

## 1.9.0-beta.16

**The list of displays is now backed up to your gateways**, like everything else.

It was the one thing a rebuilt companion could not recover. Each wall's settings come back
from its own gateway — but the LIST of walls, their names, and which one you chose as the
default lived only on the companion's disk. `gateway_url` reseeds what is in the add-on
options, so a display you added in the **UI** would simply vanish.

Now every gateway carries a copy of the whole set, and any one of them can rebuild it:
wipe the companion completely, give it back a single gateway URL, and your other walls
come back with their names and your chosen default.

## 1.9.0-beta.15

**Fixes the gateway's logo not loading** when you open a gateway tab through the companion.

The proxy rewrites the gateway's absolute paths so the browser asks the GATEWAY for them
rather than the companion — but it only recognised double quotes, and the gateway writes its
brand image with single ones (`<img src='/logo.svg'>`). So the logo was the one asset on the
page still pointing at the companion's root, where there is no /logo.svg. Both quote styles
now, with the original quote preserved.

## 1.9.0-beta.14

**Nothing shouts any more.** Every joke, quote, fortune and holiday name in the apps' data
was stored in CAPITALS, because the old hardware had no lowercase flaps. All 21,561 strings
across 12 languages are now written the way the words actually are — and a physical
split-flap still renders exactly what it always did, because the companion folds the case
for the walls that need it.

    PHYSICAL WALL          MATRIX PORTAL
    |  WHY DID THE  |      |  Why did the  |
    | SCARECROW WIN?|      | scarecrow win?|

German capitalises its nouns (*Was macht ein Pirat am Computer?*), French and Dutch do not,
and a line that continues the sentence above it stays lowercase — the data is hand-wrapped
to fit a 15-column wall, so a joke's second line is usually mid-sentence.

**Fixes the live display showing mixed case wrongly.** A composed message's page was sent
"raw", which also meant "a lowercase letter is a colour flap" — so the o, r and w of
"Hello world" were being turned into orange, red and white flaps. Reading the board back
through the Vestaboard API had the mirror-image bug.

**New app: Forecast Ribbon.** The day's temperature as a colour bar chart — each column an
hour, the bar's height how warm it gets, its colour the actual temperature. A cold morning
is a low blue foothill; a warm afternoon a tall orange ridge. A sibling of Art Clock.

## 1.9.0-beta.13

**The apps stopped shouting.** They no longer uppercase their own text — the companion
folds it, and only for a wall that needs it. A physical split-flap renders exactly what it
always did; a Matrix Portal shows the words as they were written.

    PHYSICAL WALL          MATRIX PORTAL
    | WIKI FEATURED |      | WIKI FEATURED |
    | MANUFACTURERS |      | Manufacturers |
    | TRUST COMPANY |      | Trust Company |
    |    BUILDING   |      |    Building   |

Article titles, holiday names, quotes, headlines, city names, weekdays and months all keep
their case now. The apps' own labels (NEXT HOLIDAY, WIKI FEATURED) stay uppercase — that is
authored text, not automatic folding, and it reads as a split-flap ought to.

Nothing changes on a physical wall.

## 1.9.0-beta.12

**Matrix Portal walls get their full alphabet.** The Matrix Portal Gateway (firmware 1.6+)
has an index-addressed display API, and the companion now uses it automatically when it
finds one — a physical split-flap keeps the protocol it has always had.

- **Lowercase and accents in the text you type.** Compose, the Vestaboard API and the MCP
  `show_message` tool now show your message the way you wrote it, instead of SHOUTING IT
  BACK AT YOU. (Apps still uppercase their own output — 34 of the 60 do it themselves.)
- **Pictographs**: ♥ ♦ ♣ ♠ ☺ ♪ ● ■ ⌂ ← ↑ → ↓ ☀ — none of which has a Windows-1252 byte, so
  none of which could be sent at all before.
- **Colours are named**, not smuggled through the letters `r o y g b p w`. That is *why*
  lowercase was impossible: the byte for `r` already meant RED.
- **Only what changed is redrawn.** A clock moving one digit moves one flap instead of
  repainting seventy-five modules.

Nothing changes on a physical wall, and nothing changes for apps.

## 1.9.0-beta.11

**Fixes the gateway tabs disappearing from the top bar** (a regression in beta.8). A local
variable in the tab code was called `gwUrl`, and beta.8 added a global helper of the same
name — the local shadowed it, so the call threw and the whole tab strip failed to render.
A guard now checks every global helper for shadowing, which is what would have caught it;
it found a third, latent one while it was at it.

**Apps that lay out for the wall they are on.**

- **Stocks**: ticker flush left, price flush right — the prices line up in a column and you
  can read down them.
- **World Clock** and **Sun Times**: same, city/label left and the time right.
- **Tides**: the day's tides are a *list*, so on a 4+ row wall they are one page instead of
  one page per tide. Heights line up in a column.
- **Next Launch**: fits on one page on a tall wall, instead of splitting the rocket from its
  mission across a page turn. A five-row wall also gets the launch time, in your timezone.
- **Art Clock**: a taller pixel font on a 5-row wall, and it is now centred on any wall — it
  used to be drawn raw at 3×15, so on any other geometry it sat in the top-left corner.

## 1.9.0-beta.10

**Editing a playlist no longer means retyping its name.** The editor was an anonymous
scratch buffer: "Load" copied a playlist's entries in and forgot where they came from, so
"Save" had to ask — and you had to reproduce the name exactly, or you silently made a
second playlist beside the one you meant to change.

- The list's **Load** button is now **Edit**, and it brings the name with it. **Save**
  writes straight back — no prompt.
- Change the name and Save becomes **Rename & save**, so you can't rename by accident
  while reaching for a copy. The old name is not left behind as a stale duplicate.
- **New** clears the editor, and the playlist you're editing is marked in the list.

**Apps can now opt out of auto-centring.** An app that builds its own layout declares
`"vertical_align": "top"` (or `"bottom"`) in its manifest and its rows are left exactly
where it put them. The key is additive — absent means `"center"`, so every existing app is
untouched — and `"top"` is byte-for-byte the original splitflap-os padding, so it doubles
as the compatibility switch. See COMPATIBILITY.md.

## 1.9.0-beta.9

Vertical centring, properly this time.

- **World Clock, Stocks and YouTube Comments** filled the page with blank rows themselves,
  which left nothing for the layout to centre — so three world clocks sat pinned to the top
  of a five-row wall. They now hand over only the lines they have. Two tickers, three zones
  or a single clock are centred on whatever wall they land on, including one line on a
  three-row display.
- **Cat Facts, On This Day and Sarcastic Fortune Cookies** had the opposite bug, introduced
  in beta.5: they centred themselves, and then got centred a *second* time, leaving them a
  row below the middle. Fixed.
- Crypto no longer leaves its alignment padding as trailing blank rows on the last page.

The rule is now one line of code in one place — an app hands over the lines it has, and the
layout decides where they sit — and a test enforces it across all 60 apps, in both
directions.

## 1.9.0-beta.8

**One companion, several displays.** Drive more than one gateway at once — each with its
own geometry, apps, playlists, triggers and settings — and switch between them in the UI.

- **Add displays** from the Tools menu (⚙ → Displays), or list them in the `gateway_url`
  option separated by commas: `http://192.168.1.218,http://192.168.1.50`. The first is the
  **default display**: the one Home Assistant, the Vestaboard API and anything else that
  doesn't name a display will drive.
- A **display switcher** appears in the header once you have more than one. Everything
  follows it — the live preview, Compose, Playlists, Triggers, and the gateway's own tabs.
- **Each wall gets its own Home Assistant device**, so its App/Playlist controls drive that
  wall and not another.
- Every setting belongs to a display and is stored on **that display's gateway**, so each
  wall's settings can be recovered from its own box. A new display copies the global
  settings from an existing one, so you don't retype an API key.

**If you have one gateway, nothing changes.** The switcher stays hidden, every URL means
what it meant, your Home Assistant entities keep their ids, and your existing settings are
migrated across (the old file is kept as a backup, untouched).

## 1.9.0-beta.7

Three apps that were laid out for a three-row wall, on a five-row one.

- **Weather** paged through as many as five near-empty screens — conditions, air
  quality, UV, pollen, pollen detail — several of them padded out with a
  "PROV OPENMETEO" line nobody asked for. The provider name is gone, and each metric
  is now a single row (`AQI 42 GOOD`), so a tall wall shows the lot at once.
- **Wikipedia** showed the three most-read articles as three separate pages, each
  spending one row on a title and leaving the rest blank. It is a list, so it is now
  a list: one page, one article per row (four of them on a five-row wall).
- **Next holiday** *truncated* long names — "MARTIN LUTHER KING J". It wraps them now,
  and spends the spare row on the date. On a three-row wall a name that doesn't fit
  takes the "NEXT HOLIDAY" header's row rather than losing half of itself.

Three-row walls render exactly as before.

## 1.9.0-beta.6

Groundwork for driving **several gateways from one companion** (Phase 0). There is
**no new feature here and nothing changes**: the companion still drives one gateway,
every URL still means what it meant, and the UI is untouched. What changed is the
plumbing underneath — the geometry, settings store, app loop and Home Assistant device
that were global now belong to a *display* object, so a second one can exist.

Shipping it as a beta on its own, rather than folded into the feature, so that if
anything did shift you know exactly which change to blame.

One real fix fell out of it: the tabs a gateway advertises were stored globally, which
with two gateways would have shown whichever one answered most recently.

## 1.9.0-beta.5

Tall walls (a 5x15 MatrixPortal) now actually use the space.

- **Content is centred vertically.** A three-line app used to sit at the top of a
  five-row wall with two dead rows under it. This is a deliberate divergence from
  splitflap-os, which pads only at the bottom — invisible on the 3-row walls it
  targets. Nothing changes on a 3-row wall.
- **Apps use the extra rows**: World Clock shows as many zones as you have rows (it
  was capped at three by its own settings), Date adds the year, Time adds the day and
  date, Moon Phase fits the whole reading on one page, ISS lists who is aboard, and
  Dashboard adds humidity and wind. Stocks, Crypto, Sports and Countdown already
  adapted — they simply need more tickers/teams/slots configured to fill the wall.

## 1.9.0-beta.4

Two bugs found on a 5x15 MatrixPortal (75 modules).

- **The gateway's tabs were missing, and a JavaScript error was the cause.** The
  translation work added a global `t()` function, and three callbacks already used
  `t` as a variable name — so inside them `t("…")` called a DOM element and threw.
  The worst one only threw **while an app was running**, which is why it passed
  testing and broke in the field: it aborted the UI's startup, and everything after
  it (including the gateway tab strip) never ran.
- **The companion never noticed the wall had changed shape.** It read the gateway's
  geometry once, at startup: a gateway that was still booting then — or one whose
  layout changed later, or a swap to a bigger panel — left the companion stuck on its
  default 3x15, rendering 75 modules as 45. It now re-reads the gateway on every
  heartbeat and resizes when the geometry actually moves, and the web UI follows the
  new shape without a reload.

## 1.9.0-beta.3

- **The settings dialogs are actually translated now.** Labels were, but the
  descriptions under them were not — the server assembles each one ("…  ·  Used by
  Weather, Dashboard"), and an assembled string is no catalog key, so it fell back
  to English whole. Both halves are translated server-side now, app names included
  ("Utilisé par Météo, Tableau de bord").
- **App settings dialogs too**: the labels apps declare in their manifests
  (Temperature Unit, Polling Rate, Countdown 1 Target…) are now in the catalogs —
  about 170 more strings across fr/de/es.
- **Fixed**: the weather app's settings printed a raw key
  ("weatherapi_attribution_notice") where its attribution line should be. A notice
  has no label, and the key was standing in for one.
- The **Home Assistant** option moved off the Configuration page into Home
  Assistant's "unused optional configuration options" — as an add-on it follows the
  gateway's own Home Assistant switch, so it was noise. Still settable there if you
  want to force it on or off.

## 1.9.0-beta.2

- **The UI follows your Home Assistant language.** If your HA profile is in French,
  the add-on is in French — whatever your browser's language happens to be. Home
  Assistant doesn't expose a user's profile language to add-ons through any API, so
  the UI reads it from the HA frontend it is embedded in (same origin, per user,
  exact). Outside Home Assistant nothing changes: the browser still decides.
- **UI language is now a dropdown**, not a free-text box — a typo used to fall back
  to English silently. `auto` (the default) means "follow Home Assistant, then the
  browser"; pick a language to pin it for everyone.

## 1.9.0-beta.1

- **The UI speaks your language.** The web interface (menus, buttons, forms, the
  settings dialogs) now follows the viewer's browser language, with overrides:
  a `?lang=` URL parameter always wins, then an explicitly saved Language
  setting, then the new `ui_language` option here, then the browser. French,
  German and Spanish ship first; anything untranslated falls back to English.
- **The App Library is localized too**: app names and descriptions show in the
  UI language ("Weather" → "Météo"/"Wetter"), and uploaded apps can bundle their
  own translations as `i18n/<lang>.json` inside the zip.
- **Error pages on the flaps follow the content Language** ("NO DATA" → "PAS DE
  DONNEES" on a French wall).
- Channel apps ship translations as `data_<lang>.json` sidecars, and four
  built-ins (motivational quotes, good morning, good night, dad jokes) now carry
  50 pages in 11 languages — dad jokes are native puns per language, not
  translations.
- The HACS integration and this configuration page are translated (fr/de/es).

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
