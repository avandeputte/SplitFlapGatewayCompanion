"""The apps must not SHOUT.

A split-flap has no lowercase flaps, so for years every app wrote its text in capitals. That
is no longer the app's job. A Matrix Portal renders full mixed case, and the companion folds
to uppercase itself — last, once, and only for the walls that need it (``renderer.fold()``,
called from ``engine._normalize``). An app that shouts takes that choice away from the display,
and its shouting survives onto the one wall that could have shown the words as they are
actually written.

The failure is INVISIBLE on a physical split-flap — the companion uppercases anyway — which is
exactly why it needs a test. It went unnoticed through a whole release: the apps' JSON data was
de-shouted, the app CODE was not, and so every app still shouted on a Matrix Portal.

THE ONE EXCEPTION: an animation app (``"animation": true``) has its page sent RAW, and on that
path a lowercase letter is not a letter at all — it is a COLOUR FLAP (``r`` red, ``o`` orange,
``y`` yellow, ``g`` green, ``b`` blue, ``p`` violet, ``w`` white). De-shouting one of those
would silently turn its text into coloured squares, so their capitals are load-bearing and
they are skipped entirely.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

APPS = Path(__file__).resolve().parents[2] / "apps"

# The flap alphabet itself. An app that filters text against the modules' character set spells
# the set out as a literal; that is not prose.
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Whole words only: `\b` keeps this from firing on the capitals inside a mixed-case identifier
# (``MRData`` is a JSON field, not someone shouting "MRD"). Runs of one or two letters are
# almost always a real abbreviation ("UP", "DN", "5K"), so three is the threshold.
SHOUT = re.compile(r"\b[A-Z]{3,}\b")

# Acronyms, tickers, currency and unit codes. Not shouting — this is how they are written.
ACRONYMS = {
    "API", "URL", "AQI", "ISS", "USG", "MLK", "MLLW", "MPH", "KPH",
    "USD", "EUR", "GBP", "GBX", "JPY", "BTC", "XAU", "XAG",
    # Crypto tickers — this is how they are written (crypto shows them instead of
    # mangling the CoinGecko id slug into "BITCOI").
    "ETH", "USDT", "BNB", "SOL", "XRP", "ADA", "DOGE", "TRX", "DOT",
    "LTC", "XMR", "LINK", "SHIB", "AVAX",
    # The guarded-timezone fallback (pytz.timezone(... or 'UTC')) — a zone name
    # handed to pytz, and an acronym when it does appear as text.
    "UTC",
    # Pillow image-mode constant in the canvas-image app, not display text.
    "RGB",
}

# Uppercase strings that are NOT display text: they are matched against what an API sends back,
# or sent to it. Re-casing one of these does not merely look different — it SILENTLY BREAKS THE
# MATCH and the feature quietly stops working. They are listed per app, explicitly, so that a
# new one has to be justified here rather than waved through by a blanket rule.
CODES = {
    # MBTA alert `effect` values, and the stop id in the code default
    # ("place-NSTAT" is an API identifier, matched by the MBTA, not display text).
    "metro": {"DELAY", "DETOUR", "SHUTTLE", "SUSPENSION", "NSTAT"},
    # ESPN puts the period in `shortDetail` as free text; these are searched for in it.
    "sports": {"OVERTIME", "EXTRA", "SHOOTOUT", "PENALTY", "ESPN", "EPL",
               "NFL", "NBA", "MLB", "NHL", "NCAAF", "NCAAB", "NCAAW", "MLS", "USL",
               "NWSL", "WNBA", "UCL", "UFC", "PGA", "MSOC", "WSOC"},
    # Military and cargo callsign PREFIXES, compared against an uppercased callsign; plus the
    # flight-data providers, which are badges rather than words ("Fltaware" would be nonsense).
    "planes_overhead": {"RCH", "REACH", "SPAR", "SAM", "VENUS", "EVAC", "JAKE", "TOPGUN",
                        "VIPER", "MAGMA", "IRON", "DOOM", "SKULL", "GHOST", "ARMY", "NAVY",
                        "USMC", "USCG", "AFSOC", "DUKE", "BOXER",
                        "OPENSKY", "FLTAWARE", "FLIGHTAWARE", "AIRLABS", "AVSTACK"},
    # Internal severity/colour tokens, mapped to a coloured tile before anything is shown.
    "weather": {"GREEN", "YELLOW", "ORANGE", "RED", "NONE", "UNKNOWN"},
    # Column labels for the satellite's position — abbreviations, not words.
    "iss": {"LAT", "LON"},
    # iCal (RFC 5545) is a wire format, and these are its property and component names, matched
    # against the bytes Google sends. They are as much "display text" as `Content-Type` is.
    "calendar": {"BEGIN", "END", "VEVENT", "VALARM", "VTIMEZONE", "DTSTART", "DTEND",
                 "SUMMARY", "RRULE", "EXDATE", "STATUS", "CANCELLED", "TZID", "VALUE", "DATE"},
}


def _apps():
    for manifest in sorted(APPS.glob("*/manifest.json")):
        try:
            m = json.loads(manifest.read_text())
        except ValueError:
            continue
        if m.get("animation"):
            continue          # its capitals are colour flaps — see the module docstring
        if m.get("surface") == "canvas":
            # A canvas app draws its own pixels with a real font (Pillow) straight onto
            # the Matrix panel — it never returns flap pages and never passes through
            # renderer.fold. Its case is rendered literally, so uppercase labels
            # ("DAYS", "MIN") are a deliberate typographic choice, not shouting.
            continue
        if m.get("canvas_view"):
            # A dual-view app (flap pages AND a rich canvas rendering) carries the same kind of
            # Pillow typography as a surface:canvas app — its uppercase labels ("DAYS", "ARRIVED",
            # "SET A TARGET") belong to that panel view, drawn literally, not to its flap pages
            # (which are de-shouted at the source like every other app). Exempt for the same reason.
            continue
        app = manifest.parent / "app.py"
        if app.exists():
            yield manifest.parent.name, app


def _docstrings(tree):
    """Docstrings and bare string statements: prose ABOUT the code, never shown on a wall."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr) \
               and isinstance(node.body[0].value, ast.Constant) \
               and isinstance(node.body[0].value.value, str):
                out.add(id(node.body[0].value))
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            out.add(id(node.value))
    return out


def _shouted(name: str, src: str) -> set[str]:
    tree = ast.parse(src)
    skip = _docstrings(tree)
    allowed = ACRONYMS | CODES.get(name, set())
    words = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip or ALPHABET in node.value:
            continue
        words |= {w for w in SHOUT.findall(node.value) if w not in allowed}
    return words


@pytest.mark.parametrize("name,path", list(_apps()), ids=lambda v: v if isinstance(v, str) else "")
def test_app_does_not_shout(name, path):
    """No app writes its display text in capitals: the wall decides the case, not the app."""
    shouted = _shouted(name, path.read_text())
    assert not shouted, (
        f"{name} still SHOUTS: {sorted(shouted)}\n"
        f"Write the words as a person writes them — the companion uppercases for a split-flap "
        f"by itself (renderer.fold), and a Matrix Portal shows them as written. If one of "
        f"these is a genuine acronym, add it to ACRONYMS. If it is a code matched against an "
        f"API rather than display text, add it to CODES[{name!r}] — and say why."
    )
