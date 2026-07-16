"""National Today - Holiday of the Day plugin for Split-Flap Display.

Country-aware: next to the default ``holidays.json`` (the US-flavoured baseline
and universal fallback) sit curated ``holidays_<cc>.json`` sidecars — the same
convention channel apps use for ``data_<lang>.json``. The wall's configured
Location picks the sidecar: a wall in Germany leads with "Tag des deutschen
Bieres" and still shows the international day after it, and a date with no
local entry simply falls back to the default file, so a sidecar only ever adds.
Sidecar names are written in the country's own language — they are proper
names of local observances, not text to translate.
"""

def fetch(settings, format_lines, get_rows, get_cols, i18n=None, get_location=None):
    from datetime import datetime
    import pytz
    import json
    import os

    def t(s):
        return i18n.t(s, "holiday") if i18n is not None else s

    try:
        tz = pytz.timezone(settings.get('timezone') or 'UTC')
    except Exception:
        tz = pytz.utc
    now = datetime.now(tz)
    key = f'{now.month}/{now.day}'

    here = os.path.dirname(__file__)

    def load(fname):
        try:
            with open(os.path.join(here, fname)) as f:
                doc = json.load(f)
                return doc if isinstance(doc, dict) else {}
        except Exception:
            return {}

    names = list(load('holidays.json').get(key, []))

    # Where the wall IS decides whose days it celebrates — geography, not
    # language (a French-speaking wall in Canada wants Canada's days).
    country = None
    if get_location is not None:
        try:
            country = (get_location() or {}).get('country')
        except Exception:
            country = None
    if country:
        local = load(f'holidays_{str(country).lower()}.json').get(key, [])
        # Local days lead; the international/default day still shows after them.
        names = list(local) + [n for n in names if n not in local]

    rows, cols = get_rows(), get_cols()
    pages = []
    for name in names:
        # The catalog ships common holiday names localized — use the translation
        # when there is one ("Christmas Day" -> "Weihnachten"), keep ours if not.
        if i18n is not None:
            name = i18n.holiday(name) or name
        # Sequential wrap onto the rows below the title. The old two-line greedy
        # version skipped words mid-sentence and then re-slotted later ones, which
        # could scramble "Wear Pajamas to Work Day" into nonsense.
        words, lines, cur = name.split(), [], ''
        for word in words:
            if cur and len(cur) + 1 + len(word) > cols:
                lines.append(cur)
                cur = word[:cols]
            else:
                cur = f'{cur} {word}'.strip() if cur else word[:cols]
        if cur:
            lines.append(cur)
        pages.append(format_lines(t('Today is'), *lines[:max(1, rows - 1)]))

    return pages or [format_lines(t('Today is'), t('A great day'), '')]
