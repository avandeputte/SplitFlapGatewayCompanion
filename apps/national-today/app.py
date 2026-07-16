"""National Today - Holiday of the Day plugin for Split-Flap Display."""

def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
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

    # Load bundled holidays
    holidays_path = os.path.join(os.path.dirname(__file__), 'holidays.json')
    try:
        with open(holidays_path) as f:
            holidays = json.load(f)
    except Exception:
        holidays = {}

    rows, cols = get_rows(), get_cols()
    names = holidays.get(key, [t('A great day')])
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
