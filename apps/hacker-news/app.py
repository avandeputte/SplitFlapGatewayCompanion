"""Top Hacker News stories (keyless Firebase API)."""


def _wrap(text, cols, maxlines):
    words, lines, cur = text.split(), [], ''
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) <= cols:
            cur = f'{cur} {w}'.strip()
        else:
            lines.append(cur)
            cur = w[:cols]
            if len(lines) >= maxlines:
                break
    if cur and len(lines) < maxlines:
        lines.append(cur)
    return lines[:maxlines] or ['']


def fetch(settings, format_lines, get_rows, get_cols):
    import requests
    rows, cols = get_rows(), get_cols()
    try:
        count = max(1, min(8, int(float(settings.get('count', '3') or 3))))
    except (TypeError, ValueError):
        count = 3
    try:
        ids = requests.get('https://hacker-news.firebaseio.com/v0/topstories.json', timeout=8).json()
        pages = []
        for i, sid in enumerate(ids[:count], 1):
            item = requests.get(f'https://hacker-news.firebaseio.com/v0/item/{sid}.json', timeout=8).json()
            if not item:
                continue
            title = str(item.get('title', '')).upper()
            score = item.get('score', 0)
            head = f'HN #{i}  {score} PTS'
            if rows == 1:
                pages.append(f'{score}P {title}'[:cols].center(cols))
            elif rows == 2:
                pages.append(format_lines(head, *_wrap(title, cols, 1)))
            else:
                pages.append(format_lines(head, *_wrap(title, cols, rows - 1)))
        return pages or [format_lines('HACKER NEWS', 'NO STORIES', '')]
    except Exception:
        return [format_lines('HACKER NEWS', 'OFFLINE', '')]
