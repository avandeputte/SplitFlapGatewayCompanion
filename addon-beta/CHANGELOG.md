# Changelog

Home Assistant shows this when an update is available. Newest first; the version headings
have to match the add-on's `version`, or the update notice comes up blank.

## 2.10.12-beta.26

- **Binary Clock is crisp on the LCD** — converted to on-device draw ops (`lcd_ops`):
  anti-aliased dots and scalable-text digits rendered at the panel's native 1280×800, instead of
  a 256×160 pixel frame upscaled ×5 (the pixelated look). LED panels are unchanged.

## 2.10.12-beta.25

- **A running playlist shows as playing** — the banner names the playlist and the entry currently
  up ("Playlist · All apps — Aurora"), that entry's tile lights on the Apps grid as the rotation
  advances, the playing row is marked on the Shows tab (built-in included), and Run reflects
  immediately instead of waiting for the next state update.

## 2.10.12-beta.24

- **Built-in "All apps" playlist** — the Shows tab now always offers a playlist that loops through
  every app on the Apps screen, computed fresh on each read so it mirrors installs/uninstalls
  automatically. Run-only (nothing to edit or delete), first in the list, and offered in Home
  Assistant's playlist select and MCP too. The name is reserved.

## 2.10.12-beta.23

- **Aquarium app removed** — superseded by the firmware's own aquarium effect, which renders
  on-device with zero network traffic. An install that still lists it just skips it.

## 2.10.12-beta.22

- **Aquarium at 8 fps** — plenty for its drift-and-sway motion; the backpressure gate still adapts
  the delivered rate to whatever each wall can render.

## 2.10.12-beta.21

- **Aquarium renders on the wall's fast paths** — the godray shafts are precomposed into the water
  gradient (additive light over a vertical gradient is just a brighter vertical gradient, so they
  draw as opaque columns — ~55 ms of blended quads per frame gone), and the blend mode is
  explicitly reset (0x14 00) right before the fish so the sprite run stays on the wall's
  run-batched fast lane (~20 ms → ~5 ms). The bubble glow is the only blended section left.

## 2.10.12-beta.20

- **Stopping a streaming app is now instant and final** — the OS socket buffer held seconds of
  already-queued frames, and a graceful close kept delivering them after the stop: the wall
  rendered a stopped app for ~14 s more, and those late records re-took the panel over the
  hand-back, so it never returned to the flap wall. The stream socket now runs a small send
  buffer (backpressure reflects the wall's true pace, at most a frame or two in flight) and
  closes abortively — unsent frames die with the stream.
- **Aquarium at 15 fps everywhere** — one production rate; the backpressure gate skips frames on
  a slower panel, so each wall shows the freshest frame at whatever rate it can render.

## 2.10.12-beta.19

- **The unstoppable app is fixed** — a display teardown (disable/re-create, e.g. after the wall
  changed IP) that failed partway leaked its render loop: an orphan no stop could reach, pushing
  frames to the wall forever (it even survived a wall reboot). Teardown now starts with a
  synchronous kill switch every driver loop obeys, and each teardown step is best-effort.
- **The draw stream no longer builds a backlog** — a wall that renders slower than an app draws
  (the aquarium pushes 10 fps, the 1280×800 LCD renders ~2) silently queued seconds of stale
  frames in the socket: late, jerky, and still playing after a stop. A full pipe now skips the
  frame — the next one supersedes it — so the wall always shows the freshest state.
- **Aquarium leaned out and paced to the panel** — weeds removed, far fewer bubbles, and on a huge
  panel it draws at the rate the wall can actually show (~2.5 fps) instead of flooding it; LED
  panels keep the lively ~10 fps look.

## 2.10.12-beta.18

- **App changes no longer trigger a settings write on the gateway** — every switch updated
  `last_run` and the mirror pushed the whole settings blob to the wall, a flash write that stalls
  the LCD's scanout (the visible white blinks) and wears the part. In mirror mode `last_run` is now
  volatile: it persists locally (restart-resume unchanged) but never schedules a gateway push by
  itself; it rides along when a real settings change pushes. Gateway-only mode is unchanged.

## 2.10.12-beta.17

- **App switches no longer clear the panel first** — starting a canvas app claimed the panel with a
  takeover the firmware answers with a clear-and-present: a black gap between apps and one more
  full-panel present per switch (every present is a visible blink on the LCD). The engine now only
  stands down a live device renderer and lets the new app's first frame claim the panel, so a
  switch is a clean cut from the old picture to the new. The stop path keeps the authoritative
  clear.

## 2.10.12-beta.16

- **Lumina Clock renders at the LCD's native 1280×800** (`lcd_native`) instead of on the logical
  256×160 panel upscaled ×5 — crisp digits. It also **fits the panel again**: the font-fit loop
  stepped the size down one pixel per iteration capped at 60 tries, which could not converge from
  the large native start size, so the digits overflowed both edges; it now converges proportionally
  and fits at any resolution (matrix panels land on the exact same size as before).
- **ISS Tracker renders native on the LCD too**, and **centers its title / coordinates / crew as one
  block** so the crew count sits with the coordinates instead of pinned to the bottom edge (it read
  as "too far down"). Its map's grid, dotted orbit and station marker now scale with the panel, so
  the map stays coherent at native resolution rather than thinning to hairlines. Small LED panels are
  unchanged (weights stay 1px, the sub-112px layout is untouched).
- **Aquarium streams on the LCD again** — the firmware fixed the draw-stream-around-atlas-upload
  crash, so the `lcd_no_stream` opt-out (and its whole engine mechanism) is removed; it takes the
  fast draw-stream path there like any other frame-push app.

## 2.10.12-beta.15

- **Aquarium no longer crashes the LCD** — the eager draw-stream adoption (beta.12) opened a stream
  for the aquarium, and the 0.1.0 LCD firmware crashes on the stream + sprite-atlas-upload sequence
  (the atlas PUT must close any open stream, and the open→atlas→close churn panics the wall). The
  aquarium now stays on HTTP ops on the LCD (`lcd_no_stream`), where streaming gained it nothing
  anyway; it still streams normally on the Matrix Gateway.

## 2.10.12-beta.14

- **Effects (and on-device anim/ticker) work on the LCD from the companion again** — two bugs: an
  effect app got the logical frame-push surface, which lacked the effect API, so it raised
  `AttributeError` and never started; and the companion took the panel over first, which parks the
  LCD panel and the firmware does not un-park it when the effect then starts. Now a device-side
  renderer gets the effect/anim/ticker API (delegated to the live panel) and the companion hands the
  panel back (release) instead of taking it over — exactly what the wall's own UI does.
- **Settings sync to the LCD** — the store is now gated on the gateway's `settingsStore` capability
  instead of its product name / version number (an "LCD Gateway" reporting 0.1.0 was wrongly
  excluded and kept its settings local).
- **Local split-flap preview restored** — the flap-grid preview (rendered from state, no gateway
  round-trip) is back for flap apps; only a canvas/panel app drops the live image — that was the
  readback poll that pinned the gateway worker — and shows a short note in its place.
- **LCD app layouts fixed at native 1280×800** — countdown (speck event title), date (giant clipped
  day number), dashboard (speck date), moon-phase (8px phase name), time (off-centre), metals,
  sensor-graph and stock-graph now scale with the panel instead of fixed small-panel caps. LED
  rendering byte-identical.
- **More debug logging** — the running app logs at INFO; every gateway request logs its endpoint,
  payload size and payload type at DEBUG.

## 2.10.12-beta.13

- **Removed the live wall preview from the web UI** — it refreshed a full panel readback on a
  ~300 ms timer, which pinned the gateway's single HTTP worker and read the wall "offline" while a
  browser watched. The Home-all button and the interactive game pad stay (in a slim Display bar);
  the offline badge and active-app UI are unaffected.

## 2.10.12-beta.12

- **Stopped apps no longer freeze the LCD** — stopping a canvas app (or a display going idle) now
  clears the panel framebuffer instead of leaving its last frame lit (an aquarium stuck on-screen
  long after it was turned off); releasing canvas mode alone does not repaint this hardware, so the
  stop path takes the panel over and hands it back.
- **Lighter live-preview readback (LCD)** — the preview reads a downscaled ~320 px panel (`?scale=N`)
  instead of the full 2 MB frame, so watching an on-device app's preview no longer pins the gateway's
  single worker and reads the wall "offline" (~4 s → ~0.15 s per poll).
- **Frame-push apps stream from the first frame (LCD)** — a canvas app on a stream-capable wall opens
  the draw stream up front, so even its first frame rides the stream instead of a one-shot ~2 MB PUT
  (the "switched to weather, wall offline" window).
- **Fractional sprite scale** — the sprite op accepts a float scale (via the on-device SPRITE2 op) for
  smooth sizing, not just whole 1–4.

## 2.10.12-beta.11

- **Weather sun fix (LCD)** — the sun/moon disc no longer sits partly under the info-column
  blur (it looked smeared); the disc is placed clear of the scrim and the blur stops short of it.

## 2.10.12-beta.10

- **Weather & Dashboard draw on-device now (LCD)** — both redraw their whole tall-panel scene
  as ops: the sky (gradient + sun/moon disc + clouds + rain/snow), the text as scalable `gtext`,
  and the info-panel scrim via the on-device `blur` op — crisp at 1280×800, a few hundred bytes a
  frame over the stream, instead of an animated pixel frame. LED walls unchanged.

## 2.10.12-beta.9

- **17 more apps draw as on-device text on the LCD** — the clocks and counters (Date, Countdown,
  Word Clock, Time Since), the quote/fact/trivia family (Advice, Cat/Dog Facts, Chuck Norris,
  Quote, Trivia, Useless Fact), and the numeric cards (Crypto, Stocks, Exchange Rates, Metals,
  BTC Fear & Greed, Moon Phase) now render their text as scalable anti-aliased `gtext` ops at the
  real 1280×800 — crisp, and a few hundred bytes a frame over the stream — instead of an upscaled
  pixel frame. Each keeps its LED look byte-for-byte.

## 2.10.12-beta.8

- **The clock draws as on-device text now (LCD)** — on a wall with the firmware's scalable
  `gtext`, the Clock renders its digits + date as a few anti-aliased text ops at the real
  1280×800 instead of an upscaled pixel frame: crisp, and sub-millisecond a frame over the
  draw stream (verified on the wall). LED walls are byte-for-byte unchanged. First of the
  text apps to convert; the rest follow the same pattern.

## 2.10.12-beta.7

- **LCD speed, properly this time** — profiled directly on the 1280×800 wall: an HTTP request
  costs ~1–2.4 s there, while the persistent draw stream runs at ~1 ms/frame. beta.5 had this
  backwards — it moved frame-push apps to HTTP to dodge the 2 MB stream keyframe, which turned
  the clocks and weather into ~2.4 s/frame and made switching to them feel broken. Now every
  canvas app streams, and the 2 MB keyframe is fixed at the source: it's never sent periodically
  over a stream (TCP can't drift there), only once when the stream (re)opens. Aquarium and
  app-switching are quick again.

## 2.10.12-beta.6

- **Aquarium fix (LCD): sprite-sheet churn** — a streaming sprite app re-checks its atlas each
  minute, but that check is a GET that 409s while the draw stream is open, so the companion
  wrongly concluded the sheet was evicted and re-uploaded it (~0.4 MB on the LCD) while closing
  and reopening the stream every 60 s. It now keeps the sheet belief while streaming.
- **Robust panel takeover** — every app start now drops any draw stream a prior app left open,
  so a streaming app switched away can't freeze the wall on its last frame.

## 2.10.12-beta.5

- **No more ~2 MB keyframes on the LCD** — a frame-push app no longer adopts the persistent
  draw stream on a large panel (where a full frame is raw rgb565, ~2 MB); its full frames go
  over HTTP as QOI (tens of KB) instead. Binary-ops apps still stream — a batch is a few
  hundred bytes at any panel size.
- **Groundwork for on-device text** — the companion can now emit the firmware's new `gtext`
  (scalable anti-aliased TrueType) and `blur` ops (`canvas.gtext` / `canvas.blur`, binary
  encodings and the `text2` capability), so the text apps can start drawing their type as a
  few ops instead of pushing pixel frames. App conversions follow.

## 2.10.12-beta.4

- **Aquarium speed fix (LCD)** — the beta.2 native-ops pass grew the fish sprite sheet to
  ~1.7 MB (240 px tiles), right under the panel's 2 MB atlas cap — a slow one-shot upload,
  re-hashed every frame. The tiles are now drawn small and scaled up on-device (integer
  sprite scale), so the sheet is ~0.4 MB with the fish unchanged at 240 px. LED walls
  unchanged.

## 2.10.12-beta.3

- **LCD Dashboard & Rocket Launch polish** — Dashboard weather no longer draws the
  temperature over the moon and spells the condition out ("Partly Cloudy"); the Rocket
  Launch card wraps the full mission name ("Starlink Group 12-40") instead of clipping it
  to "STARLINK G…".

## 2.10.12-beta.2

- **LCD is quick now ⚡** — full frames encode ~40× faster (vectorised QOI) and only the
  pixels that changed cross the wire, so apps stop stuttering on raw megapixel frames; the
  Aquarium draws entirely on-device (a few hundred bytes a frame, not a ~2 MB picture).
- **Apps fit the LCD's taller shape** — Dashboard, Weather, the clocks, games, quotes and
  every channel redraw for the 1280×800 panel's 1.6∶1 aspect (type sized up, content filling
  the height) instead of an LED layout stranded in a big black field.
- **Clap gestures removed** — microphone claps proved unreliable and are gone from the
  firmware; a tap (IMU) still advances the playlist. The "On a clap" setting is retired.
- Custom-app entry point `fetch_matrix()` is renamed `fetch_canvas()` — it drives LCD panels
  too. Only matters if you author your own apps.

## 2.10.12-beta.1

- **LCD Gateway support 🖥️** — the new `surface` capability picks the render path: on
  an LCD (1280×800) apps draw on a logical LED-style panel upscaled to crisp full
  frames (fish are fish-sized again), while Stock/Sensor Graph, channels and toasts
  render native with type fitted to the real resolution. Zones, games, the pad,
  preview and GIF capture all carry over.

## 2.10.11

- **Multiview 🪟** — split the Matrix panel into 2–3 vertical zones, one app each, with
  a playlist-grade editor on the new **Shows** tab: rich app picker, per-zone setting
  overrides, drag-to-reorder, saved multiviews (persisted like playlists) and a dim
  separator line between zones. Run ad-hoc, save, drop into playlists (＋ Multiview),
  or call the MCP `run_zones` tool. Gateway-resident apps (effects, sound-reactive
  visuals) sit out — they render on the wall itself.
- **Panel toasts 🔔** — triggers and timed messages on a Matrix wall arrive as a drawn
  card (icon + accent bar + big text, sliding up) instead of flap-cell letters;
  `/api/message` takes optional `icon` (bell/info/alert/check/cross/heart) and `accent`.
- **Arcade high scores 🏆** — every game keeps its best across restarts; beat it for a
  golden NEW BEST! on the game-over card.
- **Name your place** — Global settings → "Location display name": apps call home by
  your word ("Lebo"), not the geocoder's ("Mt Lebanon").
- **⏺ GIF (developer mode)** — record 8 seconds of the panel from the Live Display
  header and download a chunky-pixel GIF.

## 2.10.10

- **Clap & tap gestures 👏** — a clap (microphones) or tap (IMU) advances the running
  playlist to its next entry, with a tiny acknowledgment chirp from the speaker;
  per-gesture action in Global settings (next / stop / nothing). A double clap/tap
  still dismisses the panel's own timer/alarm before it reaches the companion.
- **Timer & alarms everywhere ⏲️** — the Matrix Gateway's kitchen timer and four daily
  alarms surface in the HACS integration (v1.4.0), the MQTT device and as MCP tools
  (`start_timer`, `set_alarm`, …) — all gated on the gateway's capability tokens.
- **Gateway settings too** — Quiet Time (now + the nightly schedule), the speaker,
  panel brightness and the dim schedule: HACS entities, `get/set_gateway_settings`
  MCP tools, and a REST proxy (`/api/gateway/settings`, `/api/timer`, `/api/alarms`).
- **Sensor Graph preloads history** — the window seeds from the Home Assistant history
  API on first draw, so a playlist slot shows a full line immediately; long entity
  names ellipsize at a readable size instead of shrinking into blobs.
- **Threshold polarity for the HA apps** — one grammar on the Sensor Graph and the
  board: `lo,hi` is a comfort band (green inside), `<warn,bad` lower-is-better (CO₂),
  `>warn,good` higher-is-better (battery), with a ◦/</> polarity button per entity
  row. ⚠️ Bare `lo,hi` on the board now reads as a *band* — prefix `<` to keep the
  old green-below-low meaning.

## 2.10.9

- **Eight new apps 🕹️** — Snake, Flappy, Breakout, Pong, Invaders and Simon join the
  arcade: playable from the web-UI pad, self-playing attract mode otherwise. Falling
  Sand pours steerable technicolor dunes, and Sensor Graph charts any Home Assistant
  sensor's rolling history under its live value.
- **Clean app switches** — taking the panel stands down whatever the previous app left
  running on-device (effect, animation, ticker) and clears the whole screen, so no
  pixels linger when apps change.
- **Photo Frame plays movies 🎞️** — MPGA files on the microSD card join the rotation,
  streamed straight off the card; apps' `play_sound` can stream card WAVs through the
  speaker.
- **Drawn app icons 🎨** — the catalog shows real vector icons (new `icon_svg` manifest
  field): Pac-Man Chomper, L+T Tetris, a BCD Binary Clock, falling-code Matrix Rain, a
  labyrinth Maze, and every new game.
- **One firmware, no ladders 🪜** — all Matrix-firmware version gating removed (binary
  ops, compositing, anti-aliasing and transforms are simply assumed present) and the
  boolean `opsBin` flag is read correctly, so full-rate binary drawing always engages.
  Physical-gateway support untouched.
- **It's the Matrix Gateway** — the old "Matrix Portal" name retired across the UI,
  docs and wiki; documentation brought fully up to the current firmware.

## 2.10.8

- **Firmware 3.12 support** ⚡ — binary draw parity (opsBin v2): anti-aliasing, transforms,
  layers, macros and beziers stream binary now; the aquarium's smooth strokes are back at
  full frame rate. Apps gain `save/translate/scale/rotate`, `layer/composite`, `define/call`
  and `bezier`.
- **3.12 lockstep** — effect knobs derive from the per-effect defs, and feature gates are
  keyed on the product line, so physical-only pages (Calibration, Provision) can never
  appear for a Matrix wall.

## 2.10.7

- **New app: Photo Frame** 🖼️ — a slideshow of the photos on the gateway's microSD card
  (firmware 3.10+); fill or letterbox, optional shuffle.
- **Smooth aquarium** 🐠 — sprite/compositing apps stream binary frames like the games do;
  steadier pacing, ~10 fps.
- **Games stay quiet until you play**, and the auto-play delay is a setting (5–120 s).
- **New effects surfaced** — Oscilloscope 📈, Beat Ripples 🌊 and Maze.
- **OpenAPI at the standard locations** 📜 — `/openapi.json`, `/openapi.yaml`, `/docs`,
  `/redoc`, `/.well-known/api-catalog`.
- Internal: comprehensive code review applied (correctness fixes, module splits, dead
  code removed); Tetris fits near-square panels.

## 2.10.6

- **New app: Tetris** 🟦 — horizontal Tetris for a wide panel; plays itself, or take the
  controls (up/down move, right rotate, left drop).
- **Chomper: lives and a proper game over** — a READY? hold between lives, fade-to-black
  GAME OVER with your score, press to restart.
- **Richer canvas art on firmware-3.8 walls** 🎨 — Aquarium godrays and glow, Chomper
  power-pellet halo. Older walls unchanged.

## 2.10.5

- **Fix: the gateway was shown offline** (and nothing could drive the display) even when it
  was reachable. The 2.10.4 move to httpx2 missed one file — the transport that talks to the
  gateway — so its connection attempt failed on a missing import and was reported as the
  wall being unreachable. Affected every display. Fixed, with a guard so it cannot recur.

## 2.10.4

The interactive release — and a big pass on newer Matrix Gateway firmware, internals, and
being current on the tools underneath.

- **Play a game on the panel.** The new **Chomper** app is a Pac-Man-style maze the Matrix
  panel plays by itself — until you grab an on-screen D-pad (arrow keys / WASD too) and
  steer it live, with sound on the gateway speaker. Let go and it drifts back to playing
  itself. A proof of concept for interactive panel games, running at game-rate latency.
- **Newer firmware, used to the fullest.** Self-describing effects (firmware 3.5
  `effectDefs`) mean every panel effect — including the audio-reactive **Spectrum** and
  **Soundwall** — shows exactly the knobs it supports, with no companion update needed as
  firmware adds more. Home Assistant cards gain live **arc-gauge dials** for thresholded
  numbers, and the full 3.5 drawing vocabulary (arcs, polygons, textboxes, sprite
  transforms) is available to apps.
- **Lower latency to the panel.** Drawing now streams as compact **binary ops** over a
  persistent connection where the wall supports it — much smaller frames and no per-frame
  round trip, which is what makes live play feel immediate. Older walls keep the JSON path,
  pixel-identical.
- **Sharper, more readable apps.** A full code review trimmed roughly two thousand lines of
  duplicate and dead code; every app's panel text now renders through one shared toolkit,
  and several apps that could render text too small on short panels are fixed.
- **Simpler UI.** The Matrix **Panel tab is retired** — its overlay ticker moves into
  Compose, and its other controls (transitions, animation and font libraries) are left to
  the gateway's own interface, which already manages them.
- **Runs more reliably.** A broken or incompatible optional dependency can no longer stop
  the whole add-on from starting. Under the hood, the companion is now current on httpx2
  and the MCP 2.0 SDK, running a single HTTP stack.

## 2.10.3

- **Weather and Weather Sky are one app.** The Weather app's Matrix-panel view is now the
  living sky scene — a glowing sun by day, a moon and colored stars by night, drifting
  cloud, falling rain or snow, and on big panels a full info column with a three-day
  forecast strip. It follows the same weather provider, location, and API-key settings as
  the flap pages. Existing Weather Sky installs and playlist entries move over
  automatically; a **Show place name** toggle controls the city label.

## 2.10.2

- **Channels stay readable on short panels.** On 32-pixel-tall panels a long quote or joke
  now splits across several screens instead of shrinking the type below the panel's
  readable floor (tiny sizes rendered garbled glyphs).
- **Bigger facts and quotes on short panels.** Cat Facts, Dog Facts, Useless Facts, Advice,
  and Quote drop their header on 32-pixel-tall panels so the text itself renders about a
  third larger — a quote's attribution now fits on screen too.

## 2.10.1

- **Playlist entry settings now steer where an app renders.** A per-entry "Show on Matrix panel"
  override in a playlist is honored — previously only the app's saved setting was consulted, so
  the same app can now appear once on the panel and once as flap text in one playlist.
- An entry's own **Loop delay** override now also paces its panel redraws and channel dwell
  (and an entry with other overrides no longer loses its saved loop delay on the panel).

## 2.10.0

Every app now lives on both displays. This release turns the Matrix panel into a first-class
surface for the entire app library, alongside a major panel-quality pass and support for the
Matrix Gateway's newest firmware features.

- **Every app has a rich Matrix-panel view.** All 40+ apps draw a designed, full-color panel view —
  clocks that fill the panel, a drawn moon with its true phase, a sun arc, a tide curve, an ISS
  tracker map, live scoreboards, departure boards, quote and fact cards, and more. Each app has a
  "Show on Matrix panel" toggle (shown only on Matrix displays, at the top of its settings);
  turned off, the app shows its classic text instead. Physical split-flap walls are unchanged.
- **One app per idea.** The separate panel-only apps merged into their classic counterparts:
  Overview into Dashboard, HA Dashboard into Home Assistant, Date Card into Date, Countdown Bars
  into Countdown, World Time into World Clock, and Weather Panel into Weather Sky. Installs and
  playlists follow the merges automatically.
- **Faster animation on firmware 3.2+.** The companion drives fast-drawing apps over the gateway's
  persistent draw stream — smooth animation at roughly triple the previous frame rate. Older
  firmware keeps the existing path.
- **Panel views use every LED.** Layouts fill the full panel height at every panel size, text never
  drops below a legible size (elements shorten or step aside instead), row spacing is even, and
  icons render correctly on low-bit-depth panels.
- **For app authors:** an app declares its displays with `"surfaces": ["flap", "matrix"]` and
  renders each with a matching entry point — `fetch()` for flaps, `fetch_matrix()` for the panel.
  See the Writing Apps wiki page.

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
