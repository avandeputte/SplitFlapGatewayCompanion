"""
Sarcastic Fortune Cookies — a functional Split-Flap plugin.

Shows a random tongue-in-cheek "fortune cookie" one-liner and swaps it for a new
random one on a chosen interval. Fortunes ship in several languages, one bundled
``fortunes_<lang>.json`` file each; the language follows the global Language
setting, falling back to English when there's no file for it. Accented fortunes
keep their Windows-1252 accents (É, Ü, ß, Œ, …) — the gateway and modules render
the cp1252 code page directly, so nothing is stripped.

Drop-in compatible with the splitflap-os plugin ABI:
    fetch(settings, format_lines, get_rows, get_cols) -> list[str]
"""

import json
import os
import random
import time

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(lang):
    """Load + cache the fortune list for a language (read once per process)."""
    cache = getattr(fetch, "_data", None)
    if cache is None:
        cache = {}
        fetch._data = cache
    if lang not in cache:
        path = os.path.join(_HERE, "fortunes_%s.json" % lang)
        entries = []
        # Bundled files are UTF-8; fall back to cp1252 in case a file is replaced
        # with a Windows-1252 one.
        for enc in ("utf-8", "cp1252"):
            try:
                with open(path, encoding=enc) as fh:
                    entries = json.load(fh)
                break
            except (UnicodeDecodeError, FileNotFoundError, ValueError, OSError):
                continue
        cache[lang] = [e["fortune"] for e in entries
                       if isinstance(e, dict) and e.get("fortune")]
    return cache[lang]


def _wrap(text, rows, cols):
    """Fit ``text`` into at most ``rows`` lines of ``cols`` characters.

    Word-wrap first (prettier); if that needs more than ``rows`` lines, fall back
    to a hard character wrap so even a maximally long fortune still lands on a
    single page. An accented letter counts as one character (one module).
    """
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= cols:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > rows:
        flat = " ".join(words)
        lines = [flat[i:i + cols] for i in range(0, len(flat), cols)]
    return lines[:rows]


def fetch(settings, format_lines, get_rows, get_cols):
    rows, cols = get_rows(), get_cols()

    # Language follows the global Language setting; use it when we bundle
    # fortunes for it, otherwise fall back to English.
    lang = str(settings.get("language") or "en").lower()
    if not _load(lang):
        lang = "en"
    try:
        every = int(settings.get("frequency") or 300)
    except (ValueError, TypeError):
        every = 300

    now = time.time()
    state = getattr(fetch, "_state", None)
    # Pick a new fortune on first run, when the chosen interval elapses, or when
    # the language changes (so a language switch shows on the next tick).
    if (state is None
            or state.get("lang") != lang
            or not state.get("pages")
            or (now - state.get("at", 0)) >= every):
        fortunes = _load(lang)
        text = random.choice(fortunes) if fortunes else "NO FORTUNES FOUND"
        lines = _wrap(text, rows, cols)
        # Vertically centre when the board has spare rows (format_lines centres
        # each line horizontally and pads the remaining rows at the bottom).
        top = (rows - len(lines)) // 2
        page = format_lines(*([""] * top + lines))
        fetch._state = {"at": now, "lang": lang, "pages": [page]}
    return fetch._state["pages"]
