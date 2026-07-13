#!/usr/bin/env python3
"""Tools for de-shouting the apps' bundled data files.

The apps' JSON data (jokes, quotes, fortunes, holiday names) is stored ENTIRELY IN CAPITALS,
because until now a split-flap had no lowercase flaps to show anything else. A Matrix Portal
does, and the companion now folds the case for the walls that need it — so the data should
hold the words as they are actually written, and let the display decide.

THE INVARIANT
-------------
A conversion is valid only if ``converted.upper() == original``.

That is the whole safety story, and it is worth being precise about why:

  * it proves NO WORD CHANGED — not a typo fixed, not a joke "improved", not a line dropped;
  * it proves THE LENGTH IS IDENTICAL, which matters more than it looks: several of these
    files store PRE-WRAPPED LINES, hand-split to fit a 15-column wall ("WHY DID THE" /
    "SCARECROW WIN?"). Change a length and you silently re-wrap someone's joke;
  * and it means a physical split-flap renders byte-for-byte what it always did, because
    the companion uppercases for it anyway.

So the model may only re-case. Anything else is rejected here rather than trusted.

Usage:
  python scripts/case_data.py extract   # dump the strings needing conversion, per file
  python scripts/case_data.py verify    # check every data file against git HEAD
  python scripts/case_data.py mirror    # copy converted files onto their locale duplicates
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

APPS = Path(__file__).resolve().parents[1] / "apps"
SKIP = {"manifest.json", "registry.json"}
ENCODINGS = ("utf-8", "cp1252", "latin-1")


def load(path: Path):
    """(doc, encoding). Eight of these files are cp1252, and must be written back as cp1252
    or every accented character in them turns to mojibake."""
    raw = path.read_bytes()
    for enc in ENCODINGS:
        try:
            return json.loads(raw.decode(enc)), enc
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError(f"cannot decode {path}")


def dump(path: Path, doc, enc: str) -> None:
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    path.write_bytes(text.encode(enc))


def data_files() -> list[Path]:
    return sorted(p for p in APPS.rglob("*.json") if p.name not in SKIP)


def shouting(s: str) -> bool:
    """A string that is entirely capitals and has letters in it."""
    return bool(s.strip()) and s == s.upper() and re.search(r"[^\W\d_]", s, re.UNICODE) is not None


def walk(doc, fn):
    """Rebuild `doc` with fn applied to every string."""
    if isinstance(doc, str):
        return fn(doc)
    if isinstance(doc, list):
        return [walk(v, fn) for v in doc]
    if isinstance(doc, dict):
        return {k: walk(v, fn) for k, v in doc.items()}
    return doc


# The SHOUTING original to compare against. A fixed tag, NOT HEAD: once the converted data
# is committed, HEAD is the converted data and "verify" would pass by comparing it with
# itself — the safety net would quietly become a no-op exactly when it is still needed.
BASELINE = os.environ.get("CASE_BASELINE", "v1.9.0-beta.13")


def _git_show(path: Path) -> bytes | None:
    rel = path.relative_to(APPS.parent)
    try:
        return subprocess.run(["git", "show", f"{BASELINE}:{rel}"], cwd=APPS.parent,
                              capture_output=True, check=True).stdout
    except subprocess.CalledProcessError:
        return None


def verify(only: str = "") -> int:
    """Every string in every data file must be the committed one, differing ONLY in case."""
    bad = 0
    checked = 0
    files = [Path(only).resolve()] if only else data_files()
    for f in files:
        old_raw = _git_show(f)
        if old_raw is None:
            continue
        for enc in ENCODINGS:
            try:
                old = json.loads(old_raw.decode(enc))
                break
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        else:
            print(f"  ?? {f}: cannot decode the committed version")
            bad += 1
            continue

        new, _ = load(f)
        olds, news = [], []
        walk(old, lambda s: olds.append(s) or s)
        walk(new, lambda s: news.append(s) or s)

        if len(olds) != len(news):
            print(f"  !! {f}: {len(olds)} strings became {len(news)} — content was added or lost")
            bad += 1
            continue
        for o, n in zip(olds, news):
            checked += 1
            if n.upper() != o.upper():
                print(f"  !! {f}: the text itself changed\n       was: {o!r}\n       now: {n!r}")
                bad += 1
            elif o != o.upper() and o != n:
                print(f"  !! {f}: a string that was NOT shouting got changed\n       was: {o!r}\n       now: {n!r}")
                bad += 1
    print(f"\n  checked {checked} strings across {len(files)} file(s) — "
          f"{'OK: only the case changed' if not bad else f'{bad} PROBLEM(S)'}")
    return bad


def mirror() -> None:
    """Several locales ship byte-identical data (fortunes_de / _de-at / _de-ch). Convert one,
    then copy it onto the others so they cannot drift apart."""
    groups = defaultdict(list)
    for f in data_files():
        old = _git_show(f)
        if old is not None:
            groups[hashlib.sha1(old).hexdigest()].append(f)
    n = 0
    for files in groups.values():
        if len(files) < 2:
            continue
        # whichever of them has actually been converted is the source
        src = next((f for f in files if not all(shouting(s) or not s.strip()
                                                for s in _all_strings(f))), None)
        if src is None:
            continue
        for f in files:
            if f != src and f.read_bytes() != src.read_bytes():
                f.write_bytes(src.read_bytes())
                print(f"  {f.name} <- {src.name}")
                n += 1
    print(f"  mirrored {n} duplicate locale file(s)")


def _all_strings(f: Path) -> list[str]:
    doc, _ = load(f)
    out: list[str] = []
    walk(doc, lambda s: out.append(s) or s)
    return out


def extract() -> None:
    for f in data_files():
        ss = [s for s in _all_strings(f) if shouting(s)]
        if ss:
            print(f"{f.relative_to(APPS)}\t{len(ss)}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if cmd == "verify":
        sys.exit(1 if verify(sys.argv[2] if len(sys.argv) > 2 else "") else 0)
    elif cmd == "mirror":
        mirror()
    elif cmd == "extract":
        extract()
    else:
        print(__doc__)
