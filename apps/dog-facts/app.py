"""A random dog fact (keyless: dogapi.dog).

The sibling of `cat-facts`, and deliberately identical in the ways that matter — the same
settings, the same evenly-balanced layout — because the two sit next to each other in the app
list and there is no reason for one to behave differently from the other.

One thing IS different, and it is the whole reason this file is not a copy. catfact.ninja takes
a `max_length` parameter, so the cat app can simply ask for a fact that fits the wall. dogapi
has no such parameter: it sends whatever it sends, and some of its facts are a paragraph long.
So this asks for a HANDFUL and picks the best one that fits, rather than taking the first and
paginating a wall of text at somebody.
"""


def _pick(facts, max_len, fits_one_page):
    """Choose the fact to show, out of the handful the API sent.

    dogapi cannot be asked for a short one, so the choosing happens here — and it is worth
    doing properly, because a fact that does not fit the wall does not get shorter, it gets
    PAGINATED, and a passer-by then reads two thirds of a sentence about beagles.

    So: prefer the LONGEST fact that still lands on a single page. Longest, not shortest,
    because a fact that fills the wall is a better use of it than three words floating in the
    middle — but never at the cost of spilling onto a second page. If nothing fits (the wall is
    small, or the API sent five paragraphs), fall back to the shortest, which at least
    paginates the least. ``fits_one_page(text)`` is the wall's own answer — the engine's
    pagination, so this app never has to know how the layout wraps."""
    clean = [f.strip() for f in facts if f and f.strip()]
    if not clean:
        return ''
    allowed = [f for f in clean if len(f) <= max_len] or clean
    onepage = [f for f in allowed if fits_one_page(f)]
    return max(onepage, key=len) if onepage else min(allowed, key=len)


def fetch(settings, format_lines, get_rows, get_cols, paginate=None):
    paginate = paginate or (lambda t, title='': [format_lines(title, t)] if title else [format_lines(t)])
    import requests
    try:
        max_len = int(float(settings.get('max_length', '120') or 120))
    except (TypeError, ValueError):
        max_len = 120
    max_len = max(40, min(250, max_len))
    try:
        # Ask for several, because the API cannot be asked for a short one. Five is enough to
        # usually find one that fits without making the wall wait on a bigger response.
        d = requests.get('https://dogapi.dog/api/v2/facts',
                         params={'limit': 5}, timeout=8).json()
        facts = [str(((item or {}).get('attributes') or {}).get('body', '') or '')
                 for item in (d.get('data') or [])]
        text = _pick(facts, max_len, lambda f: len(paginate(f)) == 1)
        if not text:
            return [format_lines('Dog fact', 'No data', '')]
        return paginate(text)   # no title — just the fact
    except Exception:
        return [format_lines('Dog fact', 'Offline', '')]
