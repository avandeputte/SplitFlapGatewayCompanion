"""A translation has to be showable on the wall it is written for.

Two ways a translated string fails, and neither of them raises anything at runtime — they just
look wrong on a wall in someone's hallway:

**A character with no flap.** A split-flap module can only show a character PRINTED ON ITS
REEL, and each language has its own 64-flap set (see the wiki's *Flaps & Character Sets*). If a
French string contains `é`, a French reel shows it — it carries ÀÂÇÈÉÊËÎÏÔÙÛÜ precisely so it
can. But `%` is NOT on the French reel, and a module asked for a character it does not carry
simply homes: a blank hole in the middle of the word. The gateway does not report this, and
nothing upstream can detect it, because the companion cannot ask a wall what its reels carry
(there is no such endpoint) — so it has to be checked here, against what we know we ship.

What must be on the reel is the string AFTER the companion folds it, since a split-flap is sent
uppercase (`renderer.fold`). `Días` is legal in Spanish because the Spanish reel carries Í.

**A string wider than the wall.** The common wall is 15 modules across. A longer label is not
rejected anywhere; it is silently cut, which is how `Prox. lanzamiento` had been rendering as
`Prox. lanzamien`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import renderer

DATA = Path(__file__).resolve().parents[1] / "app" / "i18n_data.json"

# The width of the common wall. Every one of these labels is meant to sit on one line of it.
MAX_COLS = 15

# The 64-flap reels we publish, one per locale. Uppercase only: this is what a split-flap is
# actually sent. Keep in step with the wiki's Flaps & Character Sets page.
_BASE = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789€!?.,'-"
REELS = {
    "fr": _BASE + "ÀÂÇÈÉÊËÎÏÔÙÛÜ",
    "de": _BASE + "ÄÖÜß" + ":%&()@#+=",
    "es": _BASE + "ÁÉÍÑÓÚÜ" + ":%&()@",
    "it": _BASE + "ÀÈÉÌÒÙ" + ":%&()@#",
    "pt": _BASE + "ÀÁÂÃÇÉÊÍÓÔÕÚ" + ":",
    "nl": _BASE + "ÁÉËÍÓÚÜ" + ":%&()@",
    "sv": _BASE + "ÅÄÖ" + ":%&()@#+=/",
    "da": _BASE + "ÆØÅ" + ":%&()@#+=/",
    "no": _BASE + "ÆØÅ" + ":%&()@#+=/",
}


def _translations():
    doc = json.loads(DATA.read_text())
    for domain, entries in doc["strings"].items():
        for key, entry in entries.items():
            for lang, value in (entry.get("translations") or {}).items():
                yield domain, key, lang, value


CASES = list(_translations())
IDS = [f"{lang}:{domain}:{key}" for domain, key, lang, _ in CASES]


@pytest.mark.parametrize("domain,key,lang,value", CASES, ids=IDS)
def test_translation_is_on_the_reel(domain, key, lang, value):
    """Every character exists as a flap on that language's reel."""
    reel = REELS.get(lang)
    if reel is None:
        pytest.skip(f"no published reel for {lang!r}")
    missing = sorted({c for c in renderer.fold(value) if c not in reel})
    assert not missing, (
        f"{lang} {key!r} = {value!r} needs {missing}, which the {lang} reel does not carry — "
        f"those modules would home, leaving holes in the word."
    )


@pytest.mark.parametrize("domain,key,lang,value", CASES, ids=IDS)
def test_translation_fits_the_wall(domain, key, lang, value):
    """And it fits across a 15-module wall, rather than being silently cut."""
    assert len(value) <= MAX_COLS, (
        f"{lang} {key!r} = {value!r} is {len(value)} characters; the wall is {MAX_COLS}. "
        f"Abbreviate it (the file's convention is a trailing period, e.g. 'Torm. severa')."
    )
