# apps/ — plugin library

App plugins live here, one folder per app: `apps/<id>/manifest.json` +
`apps/<id>/app.py` (plus any bundled data). The plugin contract is a **faithful,
behavior-identical port of splitflap-os** so that **any splitflap-os app folder
drops in here unmodified and works** (and apps authored here run on splitflap-os).
See [`../COMPATIBILITY.md`](../COMPATIBILITY.md).

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
| `top` | block starts at row 0, spare rows fall to the bottom — byte-for-byte splitflap-os |
| `bottom` | block pushed to the bottom |

With `top` you can emit blank lines wherever you want them and they will be respected.
Without it, an app that centres its own block gets centred **twice** and drifts below the
middle.

Everything under `apps/` that is copied from
[csader/splitflap-os](https://github.com/csader/splitflap-os) is licensed
**CC BY-NC-SA 4.0** — see [`../ATTRIBUTION.md`](../ATTRIBUTION.md).
