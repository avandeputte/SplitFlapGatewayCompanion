"""tabs.py — the tab advertisement exchanged with the gateway (Gateway 3.4+).

At registration the companion tells the gateway which tabs its own UI has, and
the gateway answers with its own list (``gwTabs``). Each side then renders the
other's tabs instead of a list hard-coded on the far side that goes stale the
moment the near side gains or loses a tab — which is exactly what happened when
the gateway folded its Backup tab into Settings.

Both directions degrade to a hard-coded list when the peer says nothing, so every
old/new combination keeps working:

* new companion + old gateway — the gateway ignores the unknown ``tabs`` field and
  answers without ``gwTabs``; the companion's UI falls back to its built-in list
  (``GATEWAY_TABS_FALLBACK`` in app.js, which still has **Backup** — a pre-3.4
  gateway really does have that tab).
* old companion + new gateway — the companion advertises nothing, so the gateway's
  dashboard falls back to its own built-in list of companion tabs.

An ``id`` is the peer's URL hash (``…/#apps``), not a display string.
"""

from __future__ import annotations

import re

# What this companion's UI offers. Must stay in step with the local tabs in
# static/index.html — tests/test_tabs.py asserts the two agree.
COMPANION_TABS: list[dict[str, str]] = [
    {"id": "apps", "label": "Apps"},
    {"id": "compose", "label": "Compose"},
    {"id": "playlists", "label": "Playlists"},
    {"id": "triggers", "label": "Triggers"},
    # Matrix-only in the UI (hidden on a flap wall), but still a tab the companion owns — the
    # nav and this list must agree (tests/test_tabs.py), so it is advertised either way.
    {"id": "panel", "label": "Panel"},
]

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
_MAX_TABS = 12
_MAX_LABEL = 24


def clean_tabs(raw: object) -> list[dict[str, str]]:
    """Validate a peer's advertised tab list; ``[]`` when it isn't usable.

    The ids land in hrefs and the labels in the nav, so a malformed or oversized
    advertisement is dropped whole rather than partially trusted — the caller then
    falls back to its built-in list, which is what an unadvertised peer gets too.
    """
    if not isinstance(raw, list) or not raw or len(raw) > _MAX_TABS:
        return []
    out: list[dict[str, str]] = []
    for t in raw:
        if not isinstance(t, dict):
            return []
        tid, label = t.get("id"), t.get("label")
        if not isinstance(tid, str) or not _ID_RE.match(tid):
            return []
        if not isinstance(label, str) or not (0 < len(label) <= _MAX_LABEL):
            return []
        if not label.isprintable():
            return []
        out.append({"id": tid, "label": label})
    return out
