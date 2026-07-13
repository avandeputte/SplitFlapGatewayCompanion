#!/usr/bin/env python3
"""Extract the SPA's chrome strings into static/i18n/en.json (the key list).

Sources, in one pass:
  - every t("...") / t('...') literal in app.js
  - every data-i18n / data-i18n-title / data-i18n-label attribute in index.html
  - dynamic-source keys the UI passes through t() at runtime: the settings
    catalog's labels/notes/placeholders/options, the schema-injected override
    fields, gateway fallback tab labels, app types and store categories.

en.json is a sorted ARRAY of keys (English needs no map — the key is the
string). Language files are {key: translation}; tests hold them subset-of-en.
Run me after touching UI strings; the test suite freezes the result.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "backend" / "app" / "static"
sys.path.insert(0, str(ROOT / "backend"))

keys: set[str] = set()

js = (STATIC / "app.js").read_text("utf-8")
# (?<![A-Za-z0-9_$.]) keeps method calls that merely END in t (post(, split(,
# closest(...) out of the catalog.
for pat in (r'(?<![A-Za-z0-9_$.])t\(\s*"((?:[^"\\]|\\.)+?)"',
            r"(?<![A-Za-z0-9_$.])t\(\s*'((?:[^'\\]|\\.)+?)'"):
    for m in re.finditer(pat, js):
        keys.add(m.group(1).replace('\\"', '"').replace("\\'", "'"))

html = (STATIC / "index.html").read_text("utf-8")
for m in re.finditer(r'data-i18n(?:-title|-label)?="([^"]+)"', html):
    keys.add(m.group(1))

# Dynamic sources: what reaches t() as a variable.
from app.catalog import CATALOG  # noqa: E402
for c in CATALOG:
    for k in ("label", "note", "ph"):
        if c.get(k):
            keys.add(c[k])
    for o in c.get("options") or []:
        if isinstance(o, dict) and o.get("label"):
            keys.add(o["label"])

keys |= {
    # settings_schema-injected override fields (plugins.py)
    "Follow global", "Language", "Location",
    "Override the global Language for this app only.",
    "Override the global Location for this app only (place search).",
    "Also uses global settings: %s — set these under Global settings.",
    # Assembled server-side in global_settings_schema (the composed note is no key)
    "Used by %s",
    "Used by this app (auto-detected — not in the app's manifest)",
    "On", "Off", "Yes", "No",
    # gateway fallback tab labels (GW_TABS_FALLBACK + what 3.4 advertises)
    "Modules", "Display", "Provision", "Calibration", "Monitor",
    "Settings", "Backup", "Status", "Bus Monitor", "Companion",
    # store metadata rendered through t(): manifest types + TitleCased categories
    "channel", "functional",
}
cats = set()
for mf in (ROOT / "apps").glob("*/manifest.json"):
    try:
        cats.add(json.loads(mf.read_text("utf-8")).get("category", "other"))
    except Exception:
        pass
keys |= {c[:1].upper() + c[1:] for c in cats if c}

# Manifest-declared settings labels/notes/placeholders and their option labels: the
# settings form renders every one of them through t(), so they belong in the chrome
# catalog. Doing it here covers the whole vendored library at once; a third-party app
# can still override any of them from its own i18n/<lang>.json "settings" map.
for mf in (ROOT / "apps").glob("*/manifest.json"):
    try:
        m = json.loads(mf.read_text("utf-8"))
    except Exception:
        continue
    for s in m.get("settings") or []:
        if not isinstance(s, dict):
            continue
        for k in ("label", "note", "ph", "text"):
            if s.get(k):
                keys.add(s[k])
        for o in s.get("options") or []:
            if isinstance(o, dict) and o.get("label"):
                keys.add(str(o["label"]))
        it = s.get("inline_toggle") or {}
        for o in it.get("options") or []:
            if isinstance(o, dict) and o.get("label"):
                keys.add(str(o["label"]))

# A chrome string worth translating contains letters; bare numbers,
# separators and selector-ish fragments are extraction noise or non-language.
keys = {k for k in keys
        if any(ch.isalpha() for ch in k) and not k.startswith(("/", "."))}

out = STATIC / "i18n" / "en.json"
out.write_text(json.dumps(sorted(keys), ensure_ascii=False, indent=2) + "\n", "utf-8")
print(f"{len(keys)} keys -> {out.relative_to(ROOT)}")
