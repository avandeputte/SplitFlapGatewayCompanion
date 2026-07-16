#!/usr/bin/env python3
"""Rebuild apps/holidays/data/ from a windows-1252-holidays zip.

Usage: python3 scripts/extract_holidays.py <holidays.zip>

The zip (generated from python-holidays by its own gen.py) carries one file per
language-region locale with ten years of holidays, one entry per holiday per
year. This restructures it for the app:

  * locales whose LANGUAGE the companion doesn't offer are dropped;
  * the ten per-year entries of each holiday collapse into one record with a
    ``dates`` list (an estimated lunar date is prefixed ``~``) — the name and
    flags were being repeated ten times, two thirds of the bytes;
  * false/null flags are omitted; files are minified.

The app reads the directory listing itself, so there is no index to keep in
sync — data/_about.json only records provenance.
"""

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "apps" / "holidays" / "data"

sys.path.insert(0, str(ROOT / "backend"))
from app.i18n import LANGUAGE_OPTIONS, base_lang  # noqa: E402


def main(zip_path: str) -> None:
    supported = {base_lang(o["value"]) for o in LANGUAGE_OPTIONS}
    z = zipfile.ZipFile(zip_path)
    prefix = "windows-1252-holidays/"
    idx = json.loads(z.read(prefix + "_index.json"))

    DEST.mkdir(exist_ok=True)
    # Cultural records (recurring, curated by hand — see the app's category
    # layer) are NOT in the zip. Preserve them across a rebuild: they carry a
    # 'recurs' M/D and 'cultural' flag, and would otherwise be wiped along with
    # the dataset they now share a file with.
    preserved: dict = {}
    for old in DEST.glob("*.json"):
        if old.name != "_about.json":
            try:
                doc = json.loads(old.read_text("utf-8"))
                cult = [h for h in doc.get("holidays", []) if h.get("cultural")]
                if cult:
                    preserved[old.stem] = cult
            except Exception:
                pass
        old.unlink()

    kept, dropped = [], []
    for entry in idx["locales"]:
        locale = entry["locale"]
        if base_lang(locale) not in supported:
            dropped.append(locale)
            continue
        doc = json.loads(z.read(prefix + entry["file"]))
        groups: dict = {}
        for h in doc["holidays"]:
            key = (h["name"], h.get("public"), h.get("religious"),
                   h.get("tradition"), tuple(h.get("subdivisions") or ()))
            g = groups.setdefault(key, {"name": h["name"], "dates": []})
            if h.get("public"):
                g["public"] = True
            if h.get("religious"):
                g["religious"] = True
            if h.get("tradition"):
                g["tradition"] = h["tradition"]
            if h.get("subdivisions"):
                g["subdivisions"] = h["subdivisions"]
            g["dates"].append(("~" if h.get("estimated") else "") + h["date"])
        for g in groups.values():
            g["dates"].sort(key=lambda d: d.lstrip("~"))

        def _order(h):
            # dated records by first date; recurring (cultural) by their M/D.
            if h.get("dates"):
                return (h["dates"][0].lstrip("~"), h["name"])
            m, d = (int(x) for x in h.get("recurs", "13/1").split("/"))
            return (f"2026-{m:02d}-{d:02d}", h["name"])

        holidays = list(groups.values()) + preserved.get(locale.lower(), [])
        out = {"locale": locale, "language": doc["language"], "region": doc["region"],
               "holidays": sorted(holidays, key=_order)}
        dest = DEST / f"{locale.lower()}.json"
        dest.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n",
                        "utf-8")
        kept.append(locale)

    (DEST / "_about.json").write_text(json.dumps({
        "source": idx.get("source"),
        "generated": idx.get("generated"),
        "year_range": idx.get("year_range"),
        "rebuilt_with": "scripts/extract_holidays.py",
        "locales": len(kept),
        "cultural_records_preserved": sum(len(v) for v in preserved.values()),
        "dropped_unsupported_languages": sorted(dropped),
    }, ensure_ascii=False, indent=1) + "\n", "utf-8")

    size = sum(f.stat().st_size for f in DEST.glob("*.json"))
    print(f"{len(kept)} locales kept, {len(dropped)} dropped {sorted(dropped)}, "
          f"{size / 1e6:.1f} MB in {DEST}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
