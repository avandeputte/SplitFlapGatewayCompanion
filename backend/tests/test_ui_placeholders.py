"""A translated string has to carry the same placeholders as the English it replaces.

`t("Manage %s displays", "2")` substitutes at runtime. If the German for that string lost its
`%s`, the number simply never appears — the button says "Anzeigen verwalten" and the user never
learns there are two. If it GAINED one, the substitution runs out of arguments and the string
comes out mangled, or raises.

Neither failure is loud. Nothing type-checks a translation file, the UI does not validate it at
load, and it only shows up in the one language the reviewer does not read. So it is checked
here, mechanically, for every catalog.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

I18N = Path(__file__).resolve().parents[1] / "app" / "static" / "i18n"

# printf-style (%s, %d) and brace-style ({}, {name}) — both appear in this codebase's UI.
PLACEHOLDER = re.compile(r"%[sd]|\{\w*\}")


def _catalogs():
    for p in sorted(I18N.glob("*.json")):
        if p.stem == "en":
            continue
        yield p.stem, json.loads(p.read_text())


CASES = [(lang, en, de) for lang, cat in _catalogs() for en, de in cat.items()
         if PLACEHOLDER.search(en) or PLACEHOLDER.search(de)]


@pytest.mark.parametrize("lang,english,translated", CASES,
                         ids=[f"{lang}:{en[:32]}" for lang, en, _ in CASES])
def test_the_translation_keeps_the_placeholders(lang, english, translated):
    want = sorted(PLACEHOLDER.findall(english))
    got = sorted(PLACEHOLDER.findall(translated))
    assert want == got, (
        f"[{lang}] placeholders differ.\n"
        f"  en: {english!r}  -> {want}\n"
        f"  {lang}: {translated!r}  -> {got}\n"
        f"A dropped %s silently swallows the value it was going to show; an extra one breaks "
        f"the substitution."
    )


def test_every_translated_key_exists_in_english():
    """A key that is not in en.json is dead weight: nothing ever looks it up, and it usually
    means the English was reworded and the translation was left behind, still shipping."""
    en = json.loads((I18N / "en.json").read_text())
    orphans = {lang: sorted(k for k in cat if k not in en) for lang, cat in _catalogs()}
    orphans = {lang: ks for lang, ks in orphans.items() if ks}
    assert not orphans, f"keys with no English source (run scripts/extract_ui_strings.py): {orphans}"
