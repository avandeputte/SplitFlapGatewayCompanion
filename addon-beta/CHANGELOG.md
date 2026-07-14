# Changelog

Home Assistant shows this when an update is available. Newest first; the version headings
have to match the add-on's `version`, or the update notice comes up blank.

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
