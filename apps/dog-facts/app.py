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


def _need_lines(lens, cols):
    need, cur = 1, 0
    for wl in lens:
        add = wl if cur == 0 else cur + 1 + wl
        if add <= cols:
            cur = add
        else:
            need += 1
            cur = wl
    return need


def _balance(words, lens, cols, k):
    """Split words into exactly k lines (each <= cols) minimizing raggedness, so
    lines fill evenly instead of orphaning the last word; prefers ending a line at
    sentence punctuation. A tiny DP."""
    n = len(words)
    pre = [0]
    for wl in lens:
        pre.append(pre[-1] + wl)

    def linelen(i, j):
        return pre[j + 1] - pre[i] + (j - i)

    INF = float("inf")
    dp = [[INF] * (n + 1) for _ in range(k + 1)]
    nxt = [[0] * (n + 1) for _ in range(k + 1)]
    dp[0][n] = 0.0
    for kk in range(1, k + 1):
        for i in range(n - 1, -1, -1):
            j = i
            while j < n:
                ll = linelen(i, j)
                if ll > cols and j > i:
                    break
                rest = dp[kk - 1][j + 1]
                if rest < INF:
                    slack = cols - ll
                    cost = slack * slack + rest
                    if words[j][-1:] in ".!?":
                        cost -= cols
                    if cost < dp[kk][i]:
                        dp[kk][i] = cost
                        nxt[kk][i] = j + 1
                j += 1
    if dp[k][0] >= INF:
        return None
    out, i, kk = [], 0, k
    while kk > 0:
        j = nxt[kk][i]
        out.append(" ".join(words[i:j]))
        i, kk = j, kk - 1
    return out


def _greedy(words, cols):
    lines, cur = [], ''
    for w in words:
        w = w if len(w) <= cols else w[:cols]
        if len(cur) + len(w) + (1 if cur else 0) <= cols:
            cur = f'{cur} {w}'.strip()
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or ['']


def _pages(format_lines, title, text, rows, cols):
    """Lay the text out (optionally under a title). When it fits on one page the
    words are balanced evenly across the lines; longer text word-wraps and
    paginates. With no title the text uses every row and is vertically centered."""
    words = text.split() or ['']
    lens = [len(w) for w in words]
    if rows == 1:
        return [ln.center(cols)[:cols] for ln in _greedy(words, cols)]
    body = rows - 1 if title else rows
    if max(lens) <= cols:
        need = _need_lines(lens, cols)
        if need <= body:
            bal = _balance(words, lens, cols, need)
            if bal is not None:
                if title:
                    return [format_lines(title, *bal)]
                # format_lines centres it. Centring here too would centre it TWICE
                # and leave it sitting below the middle.
                return [format_lines(*bal)]
    lines = _greedy(words, cols)
    pages, i = ([format_lines(title, *lines[:body])], body) if title else ([], 0)
    while i < len(lines):
        pages.append(format_lines(*lines[i:i + rows]))
        i += rows
    return pages


def _pick(facts, max_len, rows, cols):
    """Choose the fact to show, out of the handful the API sent.

    dogapi cannot be asked for a short one, so the choosing happens here — and it is worth
    doing properly, because a fact that does not fit the wall does not get shorter, it gets
    PAGINATED, and a passer-by then reads two thirds of a sentence about beagles.

    So: prefer the LONGEST fact that still lands on a single page. Longest, not shortest,
    because a fact that fills the wall is a better use of it than three words floating in the
    middle — but never at the cost of spilling onto a second page. If nothing fits (the wall is
    small, or the API sent five paragraphs), fall back to the shortest, which at least
    paginates the least.
    """
    clean = [f.strip() for f in facts if f and f.strip()]
    if not clean:
        return ''
    allowed = [f for f in clean if len(f) <= max_len] or clean
    onepage = [f for f in allowed
               if _need_lines([len(w) for w in f.split()] or [0], cols) <= rows]
    return max(onepage, key=len) if onepage else min(allowed, key=len)


def fetch(settings, format_lines, get_rows, get_cols):
    import requests
    rows, cols = get_rows(), get_cols()
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
        text = _pick(facts, max_len, rows, cols)
        if not text:
            return [format_lines('Dog fact', 'No data', '')]
        return _pages(format_lines, '', text, rows, cols)   # no title — just the fact
    except Exception:
        return [format_lines('Dog fact', 'Offline', '')]
