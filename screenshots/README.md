# Screenshots

Rendered views of every dual-surface app and channel — 57 of the catalog's 74 apps
(matrix-only canvas apps, on-device panel effects, and flap animations are not in the
render harness) — generated straight from the app code with sample data, one section
per app: its Matrix-panel (LED) view at the four common panel resolutions,
and its split-flap view on three wall heights (15 columns each). Apps adapt to the
surface they are given: smaller panels get simpler layouts, extra flap rows get more
content, fewer rows drop the least important line.

A few notes:

- Matrix images are saved at 4× so the LED pixels stay crisp; the physical panel
  quantizes color (typically 3 bitplanes), so very dim tones look slightly smoother in
  these PNGs than on real LEDs.
- Channels (quotes, jokes, facts …) render generically on the panel — big text plus a
  themed icon — shown with one representative line; a long line splits across screens
  on short panels.
- Colored flap tiles are the wall's seven color flaps (emoji render as solid color
  modules); characters with no flap show a blank module. Animation apps have no static
  flap mockup.
- Files are named by app id (so `entity-board.png` is the **Home Assistant** app), in
  one folder per surface size: [r256x64/](r256x64/), [r128x64/](r128x64/),
  [r128x32/](r128x32/), [r64x32/](r64x32/), [flap-3x15/](flap-3x15/),
  [flap-5x15/](flap-5x15/), [flap-2x15/](flap-2x15/).
- Prefer everything at a glance, per surface? The contact sheets:
  [256×64](contact-sheet-256x64.png) · [128×64](contact-sheet-128x64.png) ·
  [128×32](contact-sheet-128x32.png) · [64×32](contact-sheet-64x32.png) ·
  [flap 3×15](contact-sheet-flap-3x15.png) · [flap 5×15](contact-sheet-flap-5x15.png) ·
  [flap 2×15](contact-sheet-flap-2x15.png)

## Advice

| | |
|---|---|
| ![Advice — Matrix 256 × 64](r256x64/advice.png)<br>Matrix 256 × 64 | ![Advice — Matrix 128 × 64](r128x64/advice.png)<br>Matrix 128 × 64 |
| ![Advice — Matrix 128 × 32](r128x32/advice.png)<br>Matrix 128 × 32 | ![Advice — Matrix 64 × 32](r64x32/advice.png)<br>Matrix 64 × 32 |
| ![Advice — Flap wall 3 × 15](flap-3x15/advice.png)<br>Flap wall 3 × 15 | ![Advice — Flap wall 5 × 15](flap-5x15/advice.png)<br>Flap wall 5 × 15 |
| ![Advice — Flap wall 2 × 15](flap-2x15/advice.png)<br>Flap wall 2 × 15 |  |

## Aurora Watch

| | |
|---|---|
| ![Aurora Watch — Matrix 256 × 64](r256x64/aurora.png)<br>Matrix 256 × 64 | ![Aurora Watch — Matrix 128 × 64](r128x64/aurora.png)<br>Matrix 128 × 64 |
| ![Aurora Watch — Matrix 128 × 32](r128x32/aurora.png)<br>Matrix 128 × 32 | ![Aurora Watch — Matrix 64 × 32](r64x32/aurora.png)<br>Matrix 64 × 32 |
| ![Aurora Watch — Flap wall 3 × 15](flap-3x15/aurora.png)<br>Flap wall 3 × 15 | ![Aurora Watch — Flap wall 5 × 15](flap-5x15/aurora.png)<br>Flap wall 5 × 15 |
| ![Aurora Watch — Flap wall 2 × 15](flap-2x15/aurora.png)<br>Flap wall 2 × 15 |  |

## Binary Clock

| | |
|---|---|
| ![Binary Clock — Matrix 256 × 64](r256x64/binary-clock.png)<br>Matrix 256 × 64 | ![Binary Clock — Matrix 128 × 64](r128x64/binary-clock.png)<br>Matrix 128 × 64 |
| ![Binary Clock — Matrix 128 × 32](r128x32/binary-clock.png)<br>Matrix 128 × 32 | ![Binary Clock — Matrix 64 × 32](r64x32/binary-clock.png)<br>Matrix 64 × 32 |
| ![Binary Clock — Flap wall 3 × 15](flap-3x15/binary-clock.png)<br>Flap wall 3 × 15 | ![Binary Clock — Flap wall 5 × 15](flap-5x15/binary-clock.png)<br>Flap wall 5 × 15 |
| ![Binary Clock — Flap wall 2 × 15](flap-2x15/binary-clock.png)<br>Flap wall 2 × 15 |  |

## BirdNET

| | |
|---|---|
| ![BirdNET — Matrix 256 × 64](r256x64/birdnet.png)<br>Matrix 256 × 64 | ![BirdNET — Matrix 128 × 64](r128x64/birdnet.png)<br>Matrix 128 × 64 |
| ![BirdNET — Matrix 128 × 32](r128x32/birdnet.png)<br>Matrix 128 × 32 | ![BirdNET — Matrix 64 × 32](r64x32/birdnet.png)<br>Matrix 64 × 32 |
| ![BirdNET — Flap wall 3 × 15](flap-3x15/birdnet.png)<br>Flap wall 3 × 15 | ![BirdNET — Flap wall 5 × 15](flap-5x15/birdnet.png)<br>Flap wall 5 × 15 |
| ![BirdNET — Flap wall 2 × 15](flap-2x15/birdnet.png)<br>Flap wall 2 × 15 |  |

## BTC Fear & Greed

| | |
|---|---|
| ![BTC Fear & Greed — Matrix 256 × 64](r256x64/bitcoin-fear-greed.png)<br>Matrix 256 × 64 | ![BTC Fear & Greed — Matrix 128 × 64](r128x64/bitcoin-fear-greed.png)<br>Matrix 128 × 64 |
| ![BTC Fear & Greed — Matrix 128 × 32](r128x32/bitcoin-fear-greed.png)<br>Matrix 128 × 32 | ![BTC Fear & Greed — Matrix 64 × 32](r64x32/bitcoin-fear-greed.png)<br>Matrix 64 × 32 |
| ![BTC Fear & Greed — Flap wall 3 × 15](flap-3x15/bitcoin-fear-greed.png)<br>Flap wall 3 × 15 | ![BTC Fear & Greed — Flap wall 5 × 15](flap-5x15/bitcoin-fear-greed.png)<br>Flap wall 5 × 15 |
| ![BTC Fear & Greed — Flap wall 2 × 15](flap-2x15/bitcoin-fear-greed.png)<br>Flap wall 2 × 15 |  |

## Calendar

| | |
|---|---|
| ![Calendar — Matrix 256 × 64](r256x64/calendar.png)<br>Matrix 256 × 64 | ![Calendar — Matrix 128 × 64](r128x64/calendar.png)<br>Matrix 128 × 64 |
| ![Calendar — Matrix 128 × 32](r128x32/calendar.png)<br>Matrix 128 × 32 | ![Calendar — Matrix 64 × 32](r64x32/calendar.png)<br>Matrix 64 × 32 |
| ![Calendar — Flap wall 3 × 15](flap-3x15/calendar.png)<br>Flap wall 3 × 15 | ![Calendar — Flap wall 5 × 15](flap-5x15/calendar.png)<br>Flap wall 5 × 15 |
| ![Calendar — Flap wall 2 × 15](flap-2x15/calendar.png)<br>Flap wall 2 × 15 |  |

## Cat Facts

| | |
|---|---|
| ![Cat Facts — Matrix 256 × 64](r256x64/cat-facts.png)<br>Matrix 256 × 64 | ![Cat Facts — Matrix 128 × 64](r128x64/cat-facts.png)<br>Matrix 128 × 64 |
| ![Cat Facts — Matrix 128 × 32](r128x32/cat-facts.png)<br>Matrix 128 × 32 | ![Cat Facts — Matrix 64 × 32](r64x32/cat-facts.png)<br>Matrix 64 × 32 |
| ![Cat Facts — Flap wall 3 × 15](flap-3x15/cat-facts.png)<br>Flap wall 3 × 15 | ![Cat Facts — Flap wall 5 × 15](flap-5x15/cat-facts.png)<br>Flap wall 5 × 15 |
| ![Cat Facts — Flap wall 2 × 15](flap-2x15/cat-facts.png)<br>Flap wall 2 × 15 |  |

## Chuck Norris

| | |
|---|---|
| ![Chuck Norris — Matrix 256 × 64](r256x64/chuck-norris.png)<br>Matrix 256 × 64 | ![Chuck Norris — Matrix 128 × 64](r128x64/chuck-norris.png)<br>Matrix 128 × 64 |
| ![Chuck Norris — Matrix 128 × 32](r128x32/chuck-norris.png)<br>Matrix 128 × 32 | ![Chuck Norris — Matrix 64 × 32](r64x32/chuck-norris.png)<br>Matrix 64 × 32 |
| ![Chuck Norris — Flap wall 3 × 15](flap-3x15/chuck-norris.png)<br>Flap wall 3 × 15 | ![Chuck Norris — Flap wall 5 × 15](flap-5x15/chuck-norris.png)<br>Flap wall 5 × 15 |
| ![Chuck Norris — Flap wall 2 × 15](flap-2x15/chuck-norris.png)<br>Flap wall 2 × 15 |  |

## Comments

| | |
|---|---|
| ![Comments — Matrix 256 × 64](r256x64/yt_comments.png)<br>Matrix 256 × 64 | ![Comments — Matrix 128 × 64](r128x64/yt_comments.png)<br>Matrix 128 × 64 |
| ![Comments — Matrix 128 × 32](r128x32/yt_comments.png)<br>Matrix 128 × 32 | ![Comments — Matrix 64 × 32](r64x32/yt_comments.png)<br>Matrix 64 × 32 |
| ![Comments — Flap wall 3 × 15](flap-3x15/yt_comments.png)<br>Flap wall 3 × 15 | ![Comments — Flap wall 5 × 15](flap-5x15/yt_comments.png)<br>Flap wall 5 × 15 |
| ![Comments — Flap wall 2 × 15](flap-2x15/yt_comments.png)<br>Flap wall 2 × 15 |  |

## Countdown

| | |
|---|---|
| ![Countdown — Matrix 256 × 64](r256x64/countdown.png)<br>Matrix 256 × 64 | ![Countdown — Matrix 128 × 64](r128x64/countdown.png)<br>Matrix 128 × 64 |
| ![Countdown — Matrix 128 × 32](r128x32/countdown.png)<br>Matrix 128 × 32 | ![Countdown — Matrix 64 × 32](r64x32/countdown.png)<br>Matrix 64 × 32 |
| ![Countdown — Flap wall 3 × 15](flap-3x15/countdown.png)<br>Flap wall 3 × 15 | ![Countdown — Flap wall 5 × 15](flap-5x15/countdown.png)<br>Flap wall 5 × 15 |
| ![Countdown — Flap wall 2 × 15](flap-2x15/countdown.png)<br>Flap wall 2 × 15 |  |

## Crypto

| | |
|---|---|
| ![Crypto — Matrix 256 × 64](r256x64/crypto.png)<br>Matrix 256 × 64 | ![Crypto — Matrix 128 × 64](r128x64/crypto.png)<br>Matrix 128 × 64 |
| ![Crypto — Matrix 128 × 32](r128x32/crypto.png)<br>Matrix 128 × 32 | ![Crypto — Matrix 64 × 32](r64x32/crypto.png)<br>Matrix 64 × 32 |
| ![Crypto — Flap wall 3 × 15](flap-3x15/crypto.png)<br>Flap wall 3 × 15 | ![Crypto — Flap wall 5 × 15](flap-5x15/crypto.png)<br>Flap wall 5 × 15 |
| ![Crypto — Flap wall 2 × 15](flap-2x15/crypto.png)<br>Flap wall 2 × 15 |  |

## Dad Jokes (quiz)

| | |
|---|---|
| ![Dad Jokes — Matrix 256 × 64](r256x64/dad-jokes.png)<br>Matrix 256 × 64 | ![Dad Jokes — Matrix 128 × 64](r128x64/dad-jokes.png)<br>Matrix 128 × 64 |
| ![Dad Jokes — Matrix 128 × 32](r128x32/dad-jokes.png)<br>Matrix 128 × 32 | ![Dad Jokes — Matrix 64 × 32](r64x32/dad-jokes.png)<br>Matrix 64 × 32 |
| ![Dad Jokes — Flap wall 3 × 15](flap-3x15/dad-jokes.png)<br>Flap wall 3 × 15 | ![Dad Jokes — Flap wall 5 × 15](flap-5x15/dad-jokes.png)<br>Flap wall 5 × 15 |
| ![Dad Jokes — Flap wall 2 × 15](flap-2x15/dad-jokes.png)<br>Flap wall 2 × 15 |  |

## Dashboard

| | |
|---|---|
| ![Dashboard — Matrix 256 × 64](r256x64/dashboard.png)<br>Matrix 256 × 64 | ![Dashboard — Matrix 128 × 64](r128x64/dashboard.png)<br>Matrix 128 × 64 |
| ![Dashboard — Matrix 128 × 32](r128x32/dashboard.png)<br>Matrix 128 × 32 | ![Dashboard — Matrix 64 × 32](r64x32/dashboard.png)<br>Matrix 64 × 32 |
| ![Dashboard — Flap wall 3 × 15](flap-3x15/dashboard.png)<br>Flap wall 3 × 15 | ![Dashboard — Flap wall 5 × 15](flap-5x15/dashboard.png)<br>Flap wall 5 × 15 |
| ![Dashboard — Flap wall 2 × 15](flap-2x15/dashboard.png)<br>Flap wall 2 × 15 |  |

## Date

| | |
|---|---|
| ![Date — Matrix 256 × 64](r256x64/date.png)<br>Matrix 256 × 64 | ![Date — Matrix 128 × 64](r128x64/date.png)<br>Matrix 128 × 64 |
| ![Date — Matrix 128 × 32](r128x32/date.png)<br>Matrix 128 × 32 | ![Date — Matrix 64 × 32](r64x32/date.png)<br>Matrix 64 × 32 |
| ![Date — Flap wall 3 × 15](flap-3x15/date.png)<br>Flap wall 3 × 15 | ![Date — Flap wall 5 × 15](flap-5x15/date.png)<br>Flap wall 5 × 15 |
| ![Date — Flap wall 2 × 15](flap-2x15/date.png)<br>Flap wall 2 × 15 |  |

## Dog Facts

| | |
|---|---|
| ![Dog Facts — Matrix 256 × 64](r256x64/dog-facts.png)<br>Matrix 256 × 64 | ![Dog Facts — Matrix 128 × 64](r128x64/dog-facts.png)<br>Matrix 128 × 64 |
| ![Dog Facts — Matrix 128 × 32](r128x32/dog-facts.png)<br>Matrix 128 × 32 | ![Dog Facts — Matrix 64 × 32](r64x32/dog-facts.png)<br>Matrix 64 × 32 |
| ![Dog Facts — Flap wall 3 × 15](flap-3x15/dog-facts.png)<br>Flap wall 3 × 15 | ![Dog Facts — Flap wall 5 × 15](flap-5x15/dog-facts.png)<br>Flap wall 5 × 15 |
| ![Dog Facts — Flap wall 2 × 15](flap-2x15/dog-facts.png)<br>Flap wall 2 × 15 |  |

## Earthquakes

| | |
|---|---|
| ![Earthquakes — Matrix 256 × 64](r256x64/earthquakes.png)<br>Matrix 256 × 64 | ![Earthquakes — Matrix 128 × 64](r128x64/earthquakes.png)<br>Matrix 128 × 64 |
| ![Earthquakes — Matrix 128 × 32](r128x32/earthquakes.png)<br>Matrix 128 × 32 | ![Earthquakes — Matrix 64 × 32](r64x32/earthquakes.png)<br>Matrix 64 × 32 |
| ![Earthquakes — Flap wall 3 × 15](flap-3x15/earthquakes.png)<br>Flap wall 3 × 15 | ![Earthquakes — Flap wall 5 × 15](flap-5x15/earthquakes.png)<br>Flap wall 5 × 15 |
| ![Earthquakes — Flap wall 2 × 15](flap-2x15/earthquakes.png)<br>Flap wall 2 × 15 |  |

## Exchange Rates

| | |
|---|---|
| ![Exchange Rates — Matrix 256 × 64](r256x64/exchange-rates.png)<br>Matrix 256 × 64 | ![Exchange Rates — Matrix 128 × 64](r128x64/exchange-rates.png)<br>Matrix 128 × 64 |
| ![Exchange Rates — Matrix 128 × 32](r128x32/exchange-rates.png)<br>Matrix 128 × 32 | ![Exchange Rates — Matrix 64 × 32](r64x32/exchange-rates.png)<br>Matrix 64 × 32 |
| ![Exchange Rates — Flap wall 3 × 15](flap-3x15/exchange-rates.png)<br>Flap wall 3 × 15 | ![Exchange Rates — Flap wall 5 × 15](flap-5x15/exchange-rates.png)<br>Flap wall 5 × 15 |
| ![Exchange Rates — Flap wall 2 × 15](flap-2x15/exchange-rates.png)<br>Flap wall 2 × 15 |  |

## Formula 1

| | |
|---|---|
| ![Formula 1 — Matrix 256 × 64](r256x64/formula1.png)<br>Matrix 256 × 64 | ![Formula 1 — Matrix 128 × 64](r128x64/formula1.png)<br>Matrix 128 × 64 |
| ![Formula 1 — Matrix 128 × 32](r128x32/formula1.png)<br>Matrix 128 × 32 | ![Formula 1 — Matrix 64 × 32](r64x32/formula1.png)<br>Matrix 64 × 32 |
| ![Formula 1 — Flap wall 3 × 15](flap-3x15/formula1.png)<br>Flap wall 3 × 15 | ![Formula 1 — Flap wall 5 × 15](flap-5x15/formula1.png)<br>Flap wall 5 × 15 |
| ![Formula 1 — Flap wall 2 × 15](flap-2x15/formula1.png)<br>Flap wall 2 × 15 |  |

## Fortune Cookie (channel)

| | |
|---|---|
| ![Fortune Cookie — Matrix 256 × 64](r256x64/fortune-cookie.png)<br>Matrix 256 × 64 | ![Fortune Cookie — Matrix 128 × 64](r128x64/fortune-cookie.png)<br>Matrix 128 × 64 |
| ![Fortune Cookie — Matrix 128 × 32](r128x32/fortune-cookie.png)<br>Matrix 128 × 32 | ![Fortune Cookie — Matrix 64 × 32](r64x32/fortune-cookie.png)<br>Matrix 64 × 32 |
| ![Fortune Cookie — Flap wall 3 × 15](flap-3x15/fortune-cookie.png)<br>Flap wall 3 × 15 | ![Fortune Cookie — Flap wall 5 × 15](flap-5x15/fortune-cookie.png)<br>Flap wall 5 × 15 |
| ![Fortune Cookie — Flap wall 2 × 15](flap-2x15/fortune-cookie.png)<br>Flap wall 2 × 15 |  |

## Good Morning (channel)

| | |
|---|---|
| ![Good Morning — Matrix 256 × 64](r256x64/good-morning.png)<br>Matrix 256 × 64 | ![Good Morning — Matrix 128 × 64](r128x64/good-morning.png)<br>Matrix 128 × 64 |
| ![Good Morning — Matrix 128 × 32](r128x32/good-morning.png)<br>Matrix 128 × 32 | ![Good Morning — Matrix 64 × 32](r64x32/good-morning.png)<br>Matrix 64 × 32 |
| ![Good Morning — Flap wall 3 × 15](flap-3x15/good-morning.png)<br>Flap wall 3 × 15 | ![Good Morning — Flap wall 5 × 15](flap-5x15/good-morning.png)<br>Flap wall 5 × 15 |
| ![Good Morning — Flap wall 2 × 15](flap-2x15/good-morning.png)<br>Flap wall 2 × 15 |  |

## Good Night (channel)

| | |
|---|---|
| ![Good Night — Matrix 256 × 64](r256x64/good-night.png)<br>Matrix 256 × 64 | ![Good Night — Matrix 128 × 64](r128x64/good-night.png)<br>Matrix 128 × 64 |
| ![Good Night — Matrix 128 × 32](r128x32/good-night.png)<br>Matrix 128 × 32 | ![Good Night — Matrix 64 × 32](r64x32/good-night.png)<br>Matrix 64 × 32 |
| ![Good Night — Flap wall 3 × 15](flap-3x15/good-night.png)<br>Flap wall 3 × 15 | ![Good Night — Flap wall 5 × 15](flap-5x15/good-night.png)<br>Flap wall 5 × 15 |
| ![Good Night — Flap wall 2 × 15](flap-2x15/good-night.png)<br>Flap wall 2 × 15 |  |

## Harry Potter (channel)

| | |
|---|---|
| ![Harry Potter — Matrix 256 × 64](r256x64/harry-potter-quotes.png)<br>Matrix 256 × 64 | ![Harry Potter — Matrix 128 × 64](r128x64/harry-potter-quotes.png)<br>Matrix 128 × 64 |
| ![Harry Potter — Matrix 128 × 32](r128x32/harry-potter-quotes.png)<br>Matrix 128 × 32 | ![Harry Potter — Matrix 64 × 32](r64x32/harry-potter-quotes.png)<br>Matrix 64 × 32 |
| ![Harry Potter — Flap wall 3 × 15](flap-3x15/harry-potter-quotes.png)<br>Flap wall 3 × 15 | ![Harry Potter — Flap wall 5 × 15](flap-5x15/harry-potter-quotes.png)<br>Flap wall 5 × 15 |
| ![Harry Potter — Flap wall 2 × 15](flap-2x15/harry-potter-quotes.png)<br>Flap wall 2 × 15 |  |

## Home Assistant

| | |
|---|---|
| ![Home Assistant — Matrix 256 × 64](r256x64/entity-board.png)<br>Matrix 256 × 64 | ![Home Assistant — Matrix 128 × 64](r128x64/entity-board.png)<br>Matrix 128 × 64 |
| ![Home Assistant — Matrix 128 × 32](r128x32/entity-board.png)<br>Matrix 128 × 32 | ![Home Assistant — Matrix 64 × 32](r64x32/entity-board.png)<br>Matrix 64 × 32 |
| ![Home Assistant — Flap wall 3 × 15](flap-3x15/entity-board.png)<br>Flap wall 3 × 15 | ![Home Assistant — Flap wall 5 × 15](flap-5x15/entity-board.png)<br>Flap wall 5 × 15 |
| ![Home Assistant — Flap wall 2 × 15](flap-2x15/entity-board.png)<br>Flap wall 2 × 15 |  |

## ISS Tracker

| | |
|---|---|
| ![ISS Tracker — Matrix 256 × 64](r256x64/iss.png)<br>Matrix 256 × 64 | ![ISS Tracker — Matrix 128 × 64](r128x64/iss.png)<br>Matrix 128 × 64 |
| ![ISS Tracker — Matrix 128 × 32](r128x32/iss.png)<br>Matrix 128 × 32 | ![ISS Tracker — Matrix 64 × 32](r64x32/iss.png)<br>Matrix 64 × 32 |
| ![ISS Tracker — Flap wall 3 × 15](flap-3x15/iss.png)<br>Flap wall 3 × 15 | ![ISS Tracker — Flap wall 5 × 15](flap-5x15/iss.png)<br>Flap wall 5 × 15 |
| ![ISS Tracker — Flap wall 2 × 15](flap-2x15/iss.png)<br>Flap wall 2 × 15 |  |

## Livestream

| | |
|---|---|
| ![Livestream — Matrix 256 × 64](r256x64/livestream.png)<br>Matrix 256 × 64 | ![Livestream — Matrix 128 × 64](r128x64/livestream.png)<br>Matrix 128 × 64 |
| ![Livestream — Matrix 128 × 32](r128x32/livestream.png)<br>Matrix 128 × 32 | ![Livestream — Matrix 64 × 32](r64x32/livestream.png)<br>Matrix 64 × 32 |
| ![Livestream — Flap wall 3 × 15](flap-3x15/livestream.png)<br>Flap wall 3 × 15 | ![Livestream — Flap wall 5 × 15](flap-5x15/livestream.png)<br>Flap wall 5 × 15 |
| ![Livestream — Flap wall 2 × 15](flap-2x15/livestream.png)<br>Flap wall 2 × 15 |  |

## Magic 8 Ball (channel)

| | |
|---|---|
| ![Magic 8 Ball — Matrix 256 × 64](r256x64/magic-8-ball.png)<br>Matrix 256 × 64 | ![Magic 8 Ball — Matrix 128 × 64](r128x64/magic-8-ball.png)<br>Matrix 128 × 64 |
| ![Magic 8 Ball — Matrix 128 × 32](r128x32/magic-8-ball.png)<br>Matrix 128 × 32 | ![Magic 8 Ball — Matrix 64 × 32](r64x32/magic-8-ball.png)<br>Matrix 64 × 32 |
| ![Magic 8 Ball — Flap wall 3 × 15](flap-3x15/magic-8-ball.png)<br>Flap wall 3 × 15 | ![Magic 8 Ball — Flap wall 5 × 15](flap-5x15/magic-8-ball.png)<br>Flap wall 5 × 15 |
| ![Magic 8 Ball — Flap wall 2 × 15](flap-2x15/magic-8-ball.png)<br>Flap wall 2 × 15 |  |

## Metro

| | |
|---|---|
| ![Metro — Matrix 256 × 64](r256x64/metro.png)<br>Matrix 256 × 64 | ![Metro — Matrix 128 × 64](r128x64/metro.png)<br>Matrix 128 × 64 |
| ![Metro — Matrix 128 × 32](r128x32/metro.png)<br>Matrix 128 × 32 | ![Metro — Matrix 64 × 32](r64x32/metro.png)<br>Matrix 64 × 32 |
| ![Metro — Flap wall 3 × 15](flap-3x15/metro.png)<br>Flap wall 3 × 15 | ![Metro — Flap wall 5 × 15](flap-5x15/metro.png)<br>Flap wall 5 × 15 |
| ![Metro — Flap wall 2 × 15](flap-2x15/metro.png)<br>Flap wall 2 × 15 |  |

## Moon Phase

| | |
|---|---|
| ![Moon Phase — Matrix 256 × 64](r256x64/moon-phase.png)<br>Matrix 256 × 64 | ![Moon Phase — Matrix 128 × 64](r128x64/moon-phase.png)<br>Matrix 128 × 64 |
| ![Moon Phase — Matrix 128 × 32](r128x32/moon-phase.png)<br>Matrix 128 × 32 | ![Moon Phase — Matrix 64 × 32](r64x32/moon-phase.png)<br>Matrix 64 × 32 |
| ![Moon Phase — Flap wall 3 × 15](flap-3x15/moon-phase.png)<br>Flap wall 3 × 15 | ![Moon Phase — Flap wall 5 × 15](flap-5x15/moon-phase.png)<br>Flap wall 5 × 15 |
| ![Moon Phase — Flap wall 2 × 15](flap-2x15/moon-phase.png)<br>Flap wall 2 × 15 |  |

## Motivational Quotes (channel)

| | |
|---|---|
| ![Motivational Quotes — Matrix 256 × 64](r256x64/motivational-quotes.png)<br>Matrix 256 × 64 | ![Motivational Quotes — Matrix 128 × 64](r128x64/motivational-quotes.png)<br>Matrix 128 × 64 |
| ![Motivational Quotes — Matrix 128 × 32](r128x32/motivational-quotes.png)<br>Matrix 128 × 32 | ![Motivational Quotes — Matrix 64 × 32](r64x32/motivational-quotes.png)<br>Matrix 64 × 32 |
| ![Motivational Quotes — Flap wall 3 × 15](flap-3x15/motivational-quotes.png)<br>Flap wall 3 × 15 | ![Motivational Quotes — Flap wall 5 × 15](flap-5x15/motivational-quotes.png)<br>Flap wall 5 × 15 |
| ![Motivational Quotes — Flap wall 2 × 15](flap-2x15/motivational-quotes.png)<br>Flap wall 2 × 15 |  |

## Movie Quotes (channel)

| | |
|---|---|
| ![Movie Quotes — Matrix 256 × 64](r256x64/movie-quotes.png)<br>Matrix 256 × 64 | ![Movie Quotes — Matrix 128 × 64](r128x64/movie-quotes.png)<br>Matrix 128 × 64 |
| ![Movie Quotes — Matrix 128 × 32](r128x32/movie-quotes.png)<br>Matrix 128 × 32 | ![Movie Quotes — Matrix 64 × 32](r64x32/movie-quotes.png)<br>Matrix 64 × 32 |
| ![Movie Quotes — Flap wall 3 × 15](flap-3x15/movie-quotes.png)<br>Flap wall 3 × 15 | ![Movie Quotes — Flap wall 5 × 15](flap-5x15/movie-quotes.png)<br>Flap wall 5 × 15 |
| ![Movie Quotes — Flap wall 2 × 15](flap-2x15/movie-quotes.png)<br>Flap wall 2 × 15 |  |

## News Headlines

| | |
|---|---|
| ![News Headlines — Matrix 256 × 64](r256x64/news-headlines.png)<br>Matrix 256 × 64 | ![News Headlines — Matrix 128 × 64](r128x64/news-headlines.png)<br>Matrix 128 × 64 |
| ![News Headlines — Matrix 128 × 32](r128x32/news-headlines.png)<br>Matrix 128 × 32 | ![News Headlines — Matrix 64 × 32](r64x32/news-headlines.png)<br>Matrix 64 × 32 |
| ![News Headlines — Flap wall 3 × 15](flap-3x15/news-headlines.png)<br>Flap wall 3 × 15 | ![News Headlines — Flap wall 5 × 15](flap-5x15/news-headlines.png)<br>Flap wall 5 × 15 |
| ![News Headlines — Flap wall 2 × 15](flap-2x15/news-headlines.png)<br>Flap wall 2 × 15 |  |

## On This Day

| | |
|---|---|
| ![On This Day — Matrix 256 × 64](r256x64/on-this-day.png)<br>Matrix 256 × 64 | ![On This Day — Matrix 128 × 64](r128x64/on-this-day.png)<br>Matrix 128 × 64 |
| ![On This Day — Matrix 128 × 32](r128x32/on-this-day.png)<br>Matrix 128 × 32 | ![On This Day — Matrix 64 × 32](r64x32/on-this-day.png)<br>Matrix 64 × 32 |
| ![On This Day — Flap wall 3 × 15](flap-3x15/on-this-day.png)<br>Flap wall 3 × 15 | ![On This Day — Flap wall 5 × 15](flap-5x15/on-this-day.png)<br>Flap wall 5 × 15 |
| ![On This Day — Flap wall 2 × 15](flap-2x15/on-this-day.png)<br>Flap wall 2 × 15 |  |

## One Liners (channel)

| | |
|---|---|
| ![One Liners — Matrix 256 × 64](r256x64/funny-one-liners.png)<br>Matrix 256 × 64 | ![One Liners — Matrix 128 × 64](r128x64/funny-one-liners.png)<br>Matrix 128 × 64 |
| ![One Liners — Matrix 128 × 32](r128x32/funny-one-liners.png)<br>Matrix 128 × 32 | ![One Liners — Matrix 64 × 32](r64x32/funny-one-liners.png)<br>Matrix 64 × 32 |
| ![One Liners — Flap wall 3 × 15](flap-3x15/funny-one-liners.png)<br>Flap wall 3 × 15 | ![One Liners — Flap wall 5 × 15](flap-5x15/funny-one-liners.png)<br>Flap wall 5 × 15 |
| ![One Liners — Flap wall 2 × 15](flap-2x15/funny-one-liners.png)<br>Flap wall 2 × 15 |  |

## Planes Overhead

| | |
|---|---|
| ![Planes Overhead — Matrix 256 × 64](r256x64/planes_overhead.png)<br>Matrix 256 × 64 | ![Planes Overhead — Matrix 128 × 64](r128x64/planes_overhead.png)<br>Matrix 128 × 64 |
| ![Planes Overhead — Matrix 128 × 32](r128x32/planes_overhead.png)<br>Matrix 128 × 32 | ![Planes Overhead — Matrix 64 × 32](r64x32/planes_overhead.png)<br>Matrix 64 × 32 |
| ![Planes Overhead — Flap wall 3 × 15](flap-3x15/planes_overhead.png)<br>Flap wall 3 × 15 | ![Planes Overhead — Flap wall 5 × 15](flap-5x15/planes_overhead.png)<br>Flap wall 5 × 15 |
| ![Planes Overhead — Flap wall 2 × 15](flap-2x15/planes_overhead.png)<br>Flap wall 2 × 15 |  |

## Precious Metals

| | |
|---|---|
| ![Precious Metals — Matrix 256 × 64](r256x64/metals.png)<br>Matrix 256 × 64 | ![Precious Metals — Matrix 128 × 64](r128x64/metals.png)<br>Matrix 128 × 64 |
| ![Precious Metals — Matrix 128 × 32](r128x32/metals.png)<br>Matrix 128 × 32 | ![Precious Metals — Matrix 64 × 32](r64x32/metals.png)<br>Matrix 64 × 32 |
| ![Precious Metals — Flap wall 3 × 15](flap-3x15/metals.png)<br>Flap wall 3 × 15 | ![Precious Metals — Flap wall 5 × 15](flap-5x15/metals.png)<br>Flap wall 5 × 15 |
| ![Precious Metals — Flap wall 2 × 15](flap-2x15/metals.png)<br>Flap wall 2 × 15 |  |

## Public Holidays

| | |
|---|---|
| ![Public Holidays — Matrix 256 × 64](r256x64/holidays.png)<br>Matrix 256 × 64 | ![Public Holidays — Matrix 128 × 64](r128x64/holidays.png)<br>Matrix 128 × 64 |
| ![Public Holidays — Matrix 128 × 32](r128x32/holidays.png)<br>Matrix 128 × 32 | ![Public Holidays — Matrix 64 × 32](r64x32/holidays.png)<br>Matrix 64 × 32 |
| ![Public Holidays — Flap wall 3 × 15](flap-3x15/holidays.png)<br>Flap wall 3 × 15 | ![Public Holidays — Flap wall 5 × 15](flap-5x15/holidays.png)<br>Flap wall 5 × 15 |
| ![Public Holidays — Flap wall 2 × 15](flap-2x15/holidays.png)<br>Flap wall 2 × 15 |  |

## Quote

| | |
|---|---|
| ![Quote — Matrix 256 × 64](r256x64/quote.png)<br>Matrix 256 × 64 | ![Quote — Matrix 128 × 64](r128x64/quote.png)<br>Matrix 128 × 64 |
| ![Quote — Matrix 128 × 32](r128x32/quote.png)<br>Matrix 128 × 32 | ![Quote — Matrix 64 × 32](r64x32/quote.png)<br>Matrix 64 × 32 |
| ![Quote — Flap wall 3 × 15](flap-3x15/quote.png)<br>Flap wall 3 × 15 | ![Quote — Flap wall 5 × 15](flap-5x15/quote.png)<br>Flap wall 5 × 15 |
| ![Quote — Flap wall 2 × 15](flap-2x15/quote.png)<br>Flap wall 2 × 15 |  |

## Random Fact

| | |
|---|---|
| ![Random Fact — Matrix 256 × 64](r256x64/useless-fact.png)<br>Matrix 256 × 64 | ![Random Fact — Matrix 128 × 64](r128x64/useless-fact.png)<br>Matrix 128 × 64 |
| ![Random Fact — Matrix 128 × 32](r128x32/useless-fact.png)<br>Matrix 128 × 32 | ![Random Fact — Matrix 64 × 32](r64x32/useless-fact.png)<br>Matrix 64 × 32 |
| ![Random Fact — Flap wall 3 × 15](flap-3x15/useless-fact.png)<br>Flap wall 3 × 15 | ![Random Fact — Flap wall 5 × 15](flap-5x15/useless-fact.png)<br>Flap wall 5 × 15 |
| ![Random Fact — Flap wall 2 × 15](flap-2x15/useless-fact.png)<br>Flap wall 2 × 15 |  |

## Rocket Launch

| | |
|---|---|
| ![Rocket Launch — Matrix 256 × 64](r256x64/rocket-launch.png)<br>Matrix 256 × 64 | ![Rocket Launch — Matrix 128 × 64](r128x64/rocket-launch.png)<br>Matrix 128 × 64 |
| ![Rocket Launch — Matrix 128 × 32](r128x32/rocket-launch.png)<br>Matrix 128 × 32 | ![Rocket Launch — Matrix 64 × 32](r64x32/rocket-launch.png)<br>Matrix 64 × 32 |
| ![Rocket Launch — Flap wall 3 × 15](flap-3x15/rocket-launch.png)<br>Flap wall 3 × 15 | ![Rocket Launch — Flap wall 5 × 15](flap-5x15/rocket-launch.png)<br>Flap wall 5 × 15 |
| ![Rocket Launch — Flap wall 2 × 15](flap-2x15/rocket-launch.png)<br>Flap wall 2 × 15 |  |

## Sarcastic Fortune Cookies (channel)

| | |
|---|---|
| ![Sarcastic Fortune Cookies — Matrix 256 × 64](r256x64/sarcastic-fortune-cookies.png)<br>Matrix 256 × 64 | ![Sarcastic Fortune Cookies — Matrix 128 × 64](r128x64/sarcastic-fortune-cookies.png)<br>Matrix 128 × 64 |
| ![Sarcastic Fortune Cookies — Matrix 128 × 32](r128x32/sarcastic-fortune-cookies.png)<br>Matrix 128 × 32 | ![Sarcastic Fortune Cookies — Matrix 64 × 32](r64x32/sarcastic-fortune-cookies.png)<br>Matrix 64 × 32 |
| ![Sarcastic Fortune Cookies — Flap wall 3 × 15](flap-3x15/sarcastic-fortune-cookies.png)<br>Flap wall 3 × 15 | ![Sarcastic Fortune Cookies — Flap wall 5 × 15](flap-5x15/sarcastic-fortune-cookies.png)<br>Flap wall 5 × 15 |
| ![Sarcastic Fortune Cookies — Flap wall 2 × 15](flap-2x15/sarcastic-fortune-cookies.png)<br>Flap wall 2 × 15 |  |

## Shower Thoughts (channel)

| | |
|---|---|
| ![Shower Thoughts — Matrix 256 × 64](r256x64/shower-thoughts.png)<br>Matrix 256 × 64 | ![Shower Thoughts — Matrix 128 × 64](r128x64/shower-thoughts.png)<br>Matrix 128 × 64 |
| ![Shower Thoughts — Matrix 128 × 32](r128x32/shower-thoughts.png)<br>Matrix 128 × 32 | ![Shower Thoughts — Matrix 64 × 32](r64x32/shower-thoughts.png)<br>Matrix 64 × 32 |
| ![Shower Thoughts — Flap wall 3 × 15](flap-3x15/shower-thoughts.png)<br>Flap wall 3 × 15 | ![Shower Thoughts — Flap wall 5 × 15](flap-5x15/shower-thoughts.png)<br>Flap wall 5 × 15 |
| ![Shower Thoughts — Flap wall 2 × 15](flap-2x15/shower-thoughts.png)<br>Flap wall 2 × 15 |  |

## Sports

| | |
|---|---|
| ![Sports — Matrix 256 × 64](r256x64/sports.png)<br>Matrix 256 × 64 | ![Sports — Matrix 128 × 64](r128x64/sports.png)<br>Matrix 128 × 64 |
| ![Sports — Matrix 128 × 32](r128x32/sports.png)<br>Matrix 128 × 32 | ![Sports — Matrix 64 × 32](r64x32/sports.png)<br>Matrix 64 × 32 |
| ![Sports — Flap wall 3 × 15](flap-3x15/sports.png)<br>Flap wall 3 × 15 | ![Sports — Flap wall 5 × 15](flap-5x15/sports.png)<br>Flap wall 5 × 15 |
| ![Sports — Flap wall 2 × 15](flap-2x15/sports.png)<br>Flap wall 2 × 15 |  |

## Star Wars (channel)

| | |
|---|---|
| ![Star Wars — Matrix 256 × 64](r256x64/star-wars-quotes.png)<br>Matrix 256 × 64 | ![Star Wars — Matrix 128 × 64](r128x64/star-wars-quotes.png)<br>Matrix 128 × 64 |
| ![Star Wars — Matrix 128 × 32](r128x32/star-wars-quotes.png)<br>Matrix 128 × 32 | ![Star Wars — Matrix 64 × 32](r64x32/star-wars-quotes.png)<br>Matrix 64 × 32 |
| ![Star Wars — Flap wall 3 × 15](flap-3x15/star-wars-quotes.png)<br>Flap wall 3 × 15 | ![Star Wars — Flap wall 5 × 15](flap-5x15/star-wars-quotes.png)<br>Flap wall 5 × 15 |
| ![Star Wars — Flap wall 2 × 15](flap-2x15/star-wars-quotes.png)<br>Flap wall 2 × 15 |  |

## Stocks

| | |
|---|---|
| ![Stocks — Matrix 256 × 64](r256x64/stocks.png)<br>Matrix 256 × 64 | ![Stocks — Matrix 128 × 64](r128x64/stocks.png)<br>Matrix 128 × 64 |
| ![Stocks — Matrix 128 × 32](r128x32/stocks.png)<br>Matrix 128 × 32 | ![Stocks — Matrix 64 × 32](r64x32/stocks.png)<br>Matrix 64 × 32 |
| ![Stocks — Flap wall 3 × 15](flap-3x15/stocks.png)<br>Flap wall 3 × 15 | ![Stocks — Flap wall 5 × 15](flap-5x15/stocks.png)<br>Flap wall 5 × 15 |
| ![Stocks — Flap wall 2 × 15](flap-2x15/stocks.png)<br>Flap wall 2 × 15 |  |

## Stoic Quotes (channel)

| | |
|---|---|
| ![Stoic Quotes — Matrix 256 × 64](r256x64/stoic-quotes.png)<br>Matrix 256 × 64 | ![Stoic Quotes — Matrix 128 × 64](r128x64/stoic-quotes.png)<br>Matrix 128 × 64 |
| ![Stoic Quotes — Matrix 128 × 32](r128x32/stoic-quotes.png)<br>Matrix 128 × 32 | ![Stoic Quotes — Matrix 64 × 32](r64x32/stoic-quotes.png)<br>Matrix 64 × 32 |
| ![Stoic Quotes — Flap wall 3 × 15](flap-3x15/stoic-quotes.png)<br>Flap wall 3 × 15 | ![Stoic Quotes — Flap wall 5 × 15](flap-5x15/stoic-quotes.png)<br>Flap wall 5 × 15 |
| ![Stoic Quotes — Flap wall 2 × 15](flap-2x15/stoic-quotes.png)<br>Flap wall 2 × 15 |  |

## Sun Times

| | |
|---|---|
| ![Sun Times — Matrix 256 × 64](r256x64/sun-times.png)<br>Matrix 256 × 64 | ![Sun Times — Matrix 128 × 64](r128x64/sun-times.png)<br>Matrix 128 × 64 |
| ![Sun Times — Matrix 128 × 32](r128x32/sun-times.png)<br>Matrix 128 × 32 | ![Sun Times — Matrix 64 × 32](r64x32/sun-times.png)<br>Matrix 64 × 32 |
| ![Sun Times — Flap wall 3 × 15](flap-3x15/sun-times.png)<br>Flap wall 3 × 15 | ![Sun Times — Flap wall 5 × 15](flap-5x15/sun-times.png)<br>Flap wall 5 × 15 |
| ![Sun Times — Flap wall 2 × 15](flap-2x15/sun-times.png)<br>Flap wall 2 × 15 |  |

## The Office (channel)

| | |
|---|---|
| ![The Office — Matrix 256 × 64](r256x64/office-quotes.png)<br>Matrix 256 × 64 | ![The Office — Matrix 128 × 64](r128x64/office-quotes.png)<br>Matrix 128 × 64 |
| ![The Office — Matrix 128 × 32](r128x32/office-quotes.png)<br>Matrix 128 × 32 | ![The Office — Matrix 64 × 32](r64x32/office-quotes.png)<br>Matrix 64 × 32 |
| ![The Office — Flap wall 3 × 15](flap-3x15/office-quotes.png)<br>Flap wall 3 × 15 | ![The Office — Flap wall 5 × 15](flap-5x15/office-quotes.png)<br>Flap wall 5 × 15 |
| ![The Office — Flap wall 2 × 15](flap-2x15/office-quotes.png)<br>Flap wall 2 × 15 |  |

## Tides

| | |
|---|---|
| ![Tides — Matrix 256 × 64](r256x64/tides.png)<br>Matrix 256 × 64 | ![Tides — Matrix 128 × 64](r128x64/tides.png)<br>Matrix 128 × 64 |
| ![Tides — Matrix 128 × 32](r128x32/tides.png)<br>Matrix 128 × 32 | ![Tides — Matrix 64 × 32](r64x32/tides.png)<br>Matrix 64 × 32 |
| ![Tides — Flap wall 3 × 15](flap-3x15/tides.png)<br>Flap wall 3 × 15 | ![Tides — Flap wall 5 × 15](flap-5x15/tides.png)<br>Flap wall 5 × 15 |
| ![Tides — Flap wall 2 × 15](flap-2x15/tides.png)<br>Flap wall 2 × 15 |  |

## Time

| | |
|---|---|
| ![Time — Matrix 256 × 64](r256x64/time.png)<br>Matrix 256 × 64 | ![Time — Matrix 128 × 64](r128x64/time.png)<br>Matrix 128 × 64 |
| ![Time — Matrix 128 × 32](r128x32/time.png)<br>Matrix 128 × 32 | ![Time — Matrix 64 × 32](r64x32/time.png)<br>Matrix 64 × 32 |
| ![Time — Flap wall 3 × 15](flap-3x15/time.png)<br>Flap wall 3 × 15 | ![Time — Flap wall 5 × 15](flap-5x15/time.png)<br>Flap wall 5 × 15 |
| ![Time — Flap wall 2 × 15](flap-2x15/time.png)<br>Flap wall 2 × 15 |  |

## Time Since

| | |
|---|---|
| ![Time Since — Matrix 256 × 64](r256x64/time-since.png)<br>Matrix 256 × 64 | ![Time Since — Matrix 128 × 64](r128x64/time-since.png)<br>Matrix 128 × 64 |
| ![Time Since — Matrix 128 × 32](r128x32/time-since.png)<br>Matrix 128 × 32 | ![Time Since — Matrix 64 × 32](r64x32/time-since.png)<br>Matrix 64 × 32 |
| ![Time Since — Flap wall 3 × 15](flap-3x15/time-since.png)<br>Flap wall 3 × 15 | ![Time Since — Flap wall 5 × 15](flap-5x15/time-since.png)<br>Flap wall 5 × 15 |
| ![Time Since — Flap wall 2 × 15](flap-2x15/time-since.png)<br>Flap wall 2 × 15 |  |

## Trivia

| | |
|---|---|
| ![Trivia — Matrix 256 × 64](r256x64/trivia.png)<br>Matrix 256 × 64 | ![Trivia — Matrix 128 × 64](r128x64/trivia.png)<br>Matrix 128 × 64 |
| ![Trivia — Matrix 128 × 32](r128x32/trivia.png)<br>Matrix 128 × 32 | ![Trivia — Matrix 64 × 32](r64x32/trivia.png)<br>Matrix 64 × 32 |
| ![Trivia — Flap wall 3 × 15](flap-3x15/trivia.png)<br>Flap wall 3 × 15 | ![Trivia — Flap wall 5 × 15](flap-5x15/trivia.png)<br>Flap wall 5 × 15 |
| ![Trivia — Flap wall 2 × 15](flap-2x15/trivia.png)<br>Flap wall 2 × 15 |  |

## Weather

| | |
|---|---|
| ![Weather — Matrix 256 × 64](r256x64/weather.png)<br>Matrix 256 × 64 | ![Weather — Matrix 128 × 64](r128x64/weather.png)<br>Matrix 128 × 64 |
| ![Weather — Matrix 128 × 32](r128x32/weather.png)<br>Matrix 128 × 32 | ![Weather — Matrix 64 × 32](r64x32/weather.png)<br>Matrix 64 × 32 |
| ![Weather — Flap wall 3 × 15](flap-3x15/weather.png)<br>Flap wall 3 × 15 | ![Weather — Flap wall 5 × 15](flap-5x15/weather.png)<br>Flap wall 5 × 15 |
| ![Weather — Flap wall 2 × 15](flap-2x15/weather.png)<br>Flap wall 2 × 15 |  |

## Wikipedia Today

| | |
|---|---|
| ![Wikipedia Today — Matrix 256 × 64](r256x64/wiki-today.png)<br>Matrix 256 × 64 | ![Wikipedia Today — Matrix 128 × 64](r128x64/wiki-today.png)<br>Matrix 128 × 64 |
| ![Wikipedia Today — Matrix 128 × 32](r128x32/wiki-today.png)<br>Matrix 128 × 32 | ![Wikipedia Today — Matrix 64 × 32](r64x32/wiki-today.png)<br>Matrix 64 × 32 |
| ![Wikipedia Today — Flap wall 3 × 15](flap-3x15/wiki-today.png)<br>Flap wall 3 × 15 | ![Wikipedia Today — Flap wall 5 × 15](flap-5x15/wiki-today.png)<br>Flap wall 5 × 15 |
| ![Wikipedia Today — Flap wall 2 × 15](flap-2x15/wiki-today.png)<br>Flap wall 2 × 15 |  |

## Word Clock

| | |
|---|---|
| ![Word Clock — Matrix 256 × 64](r256x64/word-clock.png)<br>Matrix 256 × 64 | ![Word Clock — Matrix 128 × 64](r128x64/word-clock.png)<br>Matrix 128 × 64 |
| ![Word Clock — Matrix 128 × 32](r128x32/word-clock.png)<br>Matrix 128 × 32 | ![Word Clock — Matrix 64 × 32](r64x32/word-clock.png)<br>Matrix 64 × 32 |
| ![Word Clock — Flap wall 3 × 15](flap-3x15/word-clock.png)<br>Flap wall 3 × 15 | ![Word Clock — Flap wall 5 × 15](flap-5x15/word-clock.png)<br>Flap wall 5 × 15 |
| ![Word Clock — Flap wall 2 × 15](flap-2x15/word-clock.png)<br>Flap wall 2 × 15 |  |

## Word of the Day

| | |
|---|---|
| ![Word of the Day — Matrix 256 × 64](r256x64/word-of-the-day.png)<br>Matrix 256 × 64 | ![Word of the Day — Matrix 128 × 64](r128x64/word-of-the-day.png)<br>Matrix 128 × 64 |
| ![Word of the Day — Matrix 128 × 32](r128x32/word-of-the-day.png)<br>Matrix 128 × 32 | ![Word of the Day — Matrix 64 × 32](r64x32/word-of-the-day.png)<br>Matrix 64 × 32 |
| ![Word of the Day — Flap wall 3 × 15](flap-3x15/word-of-the-day.png)<br>Flap wall 3 × 15 | ![Word of the Day — Flap wall 5 × 15](flap-5x15/word-of-the-day.png)<br>Flap wall 5 × 15 |
| ![Word of the Day — Flap wall 2 × 15](flap-2x15/word-of-the-day.png)<br>Flap wall 2 × 15 |  |

## World Clock

| | |
|---|---|
| ![World Clock — Matrix 256 × 64](r256x64/world_clock.png)<br>Matrix 256 × 64 | ![World Clock — Matrix 128 × 64](r128x64/world_clock.png)<br>Matrix 128 × 64 |
| ![World Clock — Matrix 128 × 32](r128x32/world_clock.png)<br>Matrix 128 × 32 | ![World Clock — Matrix 64 × 32](r64x32/world_clock.png)<br>Matrix 64 × 32 |
| ![World Clock — Flap wall 3 × 15](flap-3x15/world_clock.png)<br>Flap wall 3 × 15 | ![World Clock — Flap wall 5 × 15](flap-5x15/world_clock.png)<br>Flap wall 5 × 15 |
| ![World Clock — Flap wall 2 × 15](flap-2x15/world_clock.png)<br>Flap wall 2 × 15 |  |

## YouTube

| | |
|---|---|
| ![YouTube — Matrix 256 × 64](r256x64/youtube.png)<br>Matrix 256 × 64 | ![YouTube — Matrix 128 × 64](r128x64/youtube.png)<br>Matrix 128 × 64 |
| ![YouTube — Matrix 128 × 32](r128x32/youtube.png)<br>Matrix 128 × 32 | ![YouTube — Matrix 64 × 32](r64x32/youtube.png)<br>Matrix 64 × 32 |
| ![YouTube — Flap wall 3 × 15](flap-3x15/youtube.png)<br>Flap wall 3 × 15 | ![YouTube — Flap wall 5 × 15](flap-5x15/youtube.png)<br>Flap wall 5 × 15 |
| ![YouTube — Flap wall 2 × 15](flap-2x15/youtube.png)<br>Flap wall 2 × 15 |  |
