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
byte to send (the byte for `r` already means RED). A Matrix Portal has both, and shows your
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
  their lowercase `r o y g b p w` mean the COLOUR FLAPS, not letters. An animation that
  shows text must uppercase it itself.
* **Codes, not prose.** A currency code, a team abbreviation, a country code or a string
  you compare against is not display text. Keep `.upper()` there; it is logic.

## Asking what the wall can show

Some walls are drawn rather than mechanical (a Matrix Portal), and those have fourteen
pictographs the reel has no flap for. Declare `caps` and the runtime hands you the answer:

```python
def fetch(settings, format_lines, get_rows, get_cols, i18n=None, caps=None):
    high = '↑' if (caps and caps.pictographs) else 'HIGH'
```

`caps` has `lowercase`, `pictographs` and `named_colours`. It is optional and defaults to
`None`, so an app that asks for it still runs on a host that injects nothing — treat
`None` as "a plain reel". Available pictographs: `♥ ♦ ♣ ♠ ☺ ♪ ● ■ ⌂ ← ↑ → ↓ ☀`

**Check before you use one.** A wall without them substitutes the nearest character it has,
and only some of those still mean anything:

| pictograph | on a plain reel | verdict |
|---|---|---|
| `← ↑ → ↓` | `< ^ > v` | **safe without a check** — still reads |
| `■` `⌂` `☺` | `#` `^` `:` | usable |
| `♥ ♦ ♣ ♠ ♪ ● ☀` | `*` | **the meaning is lost** — check `caps.pictographs` |

The seven **colour flaps** are a different matter: every wall has had them from the start, so
a colour tile (🟥🟩🟦🟨🟧🟪⬜) is always safe. But a colour is invisible with colours turned
off — so if you are showing a *direction*, show an arrow as well.

## Where your lines land on the wall

Return only the lines you actually have — `format_lines` places them. Given fewer lines
than the wall is tall, it **centres** the block, so a 3-line app looks right on a 3-row
wall and on a 5-row one. Do **not** pad to `get_rows()` yourself: that fills the page, and
the block ends up pinned to the top of a tall wall.

If your app builds its own layout and wants its rows left exactly where it put them, say
so in the manifest:

```json
{ "vertical_align": "top" }
```

| value | what it does |
|---|---|
| `center` | *(default, and what you get if the key is absent)* block centred; odd spare row falls to the bottom |
| `top` | block starts at row 0, spare rows fall to the bottom — your lines land byte-for-byte where you put them |
| `bottom` | block pushed to the bottom |

With `top` you can emit blank lines wherever you want them and they will be respected.
Without it, an app that centres its own block gets centred **twice** and drifts below the
middle.

Some apps under `apps/` are copied from an upstream open-source app library and
are licensed **CC BY-NC-SA 4.0** — the source and full attribution are in
[`../ATTRIBUTION.md`](../ATTRIBUTION.md).
