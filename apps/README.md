# apps/ — plugin library

App plugins live here, one folder per app: `apps/<id>/manifest.json` +
`apps/<id>/app.py` (plus any bundled data). The plugin contract is a **faithful,
behavior-identical port of splitflap-os** so that **any splitflap-os app folder
drops in here unmodified and works** (and apps authored here run on splitflap-os).
See [`../COMPATIBILITY.md`](../COMPATIBILITY.md).

Phase 2 vendors the splitflap-os apps into this directory and adds the plugin
runtime that loads them. Until then this folder is intentionally empty.

Everything under `apps/` that is copied from
[csader/splitflap-os](https://github.com/csader/splitflap-os) is licensed
**CC BY-NC-SA 4.0** — see [`../ATTRIBUTION.md`](../ATTRIBUTION.md).
