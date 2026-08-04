# apps/ — plugin library

App plugins live here, one folder per app: `apps/<id>/manifest.json` +
`apps/<id>/app.py` (plus any bundled data). The plugin contract is the
**companion's own app format**: a plain `fetch()` entry point, with every extra
(injected helpers, `caps`, `i18n`) opt-in by parameter name — so **an app folder
written against the bare contract drops in here unmodified and works**. (An app
authored here also runs on a bare host that injects none of the extras,
unoptimized — best-effort, no guarantees.)
See [Compatibility](https://github.com/avandeputte/SplitFlapGateway/wiki/Compatibility).

## Do not uppercase your own text

Return the text **as written**. The companion folds it to uppercase on a wall that needs it
— a physical split-flap has no lowercase flaps, and its one-byte protocol has no lowercase
byte to send (the byte for `r` already means RED). A Matrix Gateway has both, and shows your
text as you wrote it.

Calling `.upper()` yourself was always redundant, because a non-raw page is folded anyway.
Once a wall could show lowercase it became actively harmful: the case was destroyed before
the display ever got a say.

```python
title = article["title"]           # "Manufacturers Trust Company Building"
# NOT: title = article["title"].upper()
```

Two exceptions, both real:

* **Animations** (`"animation": true` in the manifest) are sent RAW and are *not* folded —
  their lowercase `r o y g b p w` mean the COLOR FLAPS, not letters. An animation that
  shows text must uppercase it itself.
* **Codes, not prose.** A currency code, a team abbreviation, a country code or a string
  you compare against is not display text. Keep `.upper()` there; it is logic.

## Asking what the wall can show

Some walls are drawn rather than mechanical (a Matrix Gateway), and those have fourteen
pictographs the reel has no flap for. Declare `caps` and the runtime hands you the answer:

```python
def fetch(settings, format_lines, get_rows, get_cols, i18n=None, caps=None):
    high = '↑' if (caps and caps.pictographs) else 'HIGH'
```

`caps` has `lowercase`, `pictographs` and `named_colors`. It is optional and defaults to
`None`, so an app that asks for it still runs on a host that injects nothing — treat
`None` as "a plain reel". Available pictographs: `♥ ♦ ♣ ♠ ☺ ♪ ● ■ ⌂ ← ↑ → ↓ ☀`

**Check before you use one.** A wall without them substitutes the nearest character it has,
and only some of those still mean anything:

| pictograph | on a plain reel | verdict |
|---|---|---|
| `← ↑ → ↓` | `< ^ > v` | **safe without a check** — still reads |
| `■` `⌂` `☺` | `#` `^` `:` | usable |
| `♥ ♦ ♣ ♠ ♪ ● ☀` | `*` | **the meaning is lost** — check `caps.pictographs` |

The seven **color flaps** are a different matter: every wall has had them from the start, so
a color tile (🟥🟩🟦🟨🟧🟪⬜) is always safe. But a color is invisible with colors turned
off — so if you are showing a *direction*, show an arrow as well.

## Where your lines land on the wall

Return only the lines you actually have — `format_lines` places them. Given fewer lines
than the wall is tall, it **centers** the block, so a 3-line app looks right on a 3-row
wall and on a 5-row one. Do **not** pad to `get_rows()` yourself: that fills the page, and
the block ends up pinned to the top of a tall wall.

If your app builds its own layout and wants its rows left exactly where it put them, say
so in the manifest:

```json
{ "vertical_align": "top" }
```

| value | what it does |
|---|---|
| `center` | *(default, and what you get if the key is absent)* block centered; odd spare row falls to the bottom |
| `top` | block starts at row 0, spare rows fall to the bottom — your lines land byte-for-byte where you put them |
| `bottom` | block pushed to the bottom |

With `top` you can emit blank lines wherever you want them and they will be respected.
Without it, an app that centers its own block gets centered **twice** and drifts below the
middle.


## The Matrix-panel surface (`fetch_canvas`)

Most apps here define a second entry point alongside `fetch()`:

```python
def fetch_canvas(settings, canvas, controls=None, play_sound=None):
    canvas.text(2, 2, "HELLO", (255, 200, 0), size=10)
    canvas.show()
    return 5           # seconds to hold before the next redraw
```

It draws the app's **rich view on a Matrix LED panel** and is a **companion-only
extension** — a bare upstream host never calls it, so it may rely on the injected
`canvas` surface freely. Two styles: *ops-drawing* (call `canvas.rect/circle/text/...`
then `canvas.show()`; the batch renders on-device, streamed as binary ops where the
firmware supports it) and *frame-push* (build a PIL image with `canvas.blank()`/
`canvas.vgrad()`/`canvas.font()` and hand it to `canvas.frame(img)`). Read settings with
`canvas.num(settings, key, default, lo, hi)` — the shared clamped raw-string parser —
and gate newer draw ops on `canvas.has_op(...)` / the `canvas.can_*` capability flags. A wall
with a microSD card (`canvas.can_sd`) adds `canvas.sd_list()` / `canvas.sd_get()`,
`canvas.play_anim_path()` (MPGA movies streamed off the card) and `play_sound(wav=…)`.
Interactive games additionally declare `"interactive": true` and take the `controls` /
`play_sound` helpers.

The full surface reference (draw ops, the PIL text toolkit, capabilities, compositing)
lives on the wiki: [Writing Matrix Apps](https://github.com/avandeputte/SplitFlapGateway/wiki/Writing-Matrix-Apps).
A dual-surface app keeps `fetch()` as its flap view; the manifest's `surfaces` list and
the per-app "Show on Matrix panel" toggle decide which view a given wall runs.

## App icons

`"icon"` is an emoji — the app's identity everywhere text-shaped (modal titles, MCP
tools, this list). An app may also carry `"icon_svg"`: a `data:image/svg+xml,...` URI
the web UI's catalog card and library row prefer, for icons an emoji can't draw (see
Chomper's Pac-Man or Tetris's tetrominoes). It must be a `data:image/` URI — anything
else is dropped at the catalog boundary, since manifests arrive in uploaded zips.
