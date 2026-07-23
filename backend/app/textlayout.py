"""Balanced word-wrapping and pagination for app text.

This is the layout the advice / quote / fact apps each carried a byte-identical
copy of. Now it lives once, and an app opts in with a ``paginate`` parameter
(like ``i18n`` or ``get_weather``); the runtime binds it to the wall.

**Balanced** means: when the text fits on one page, the words are spread evenly
across the lines it needs — a tiny DP that minimizes raggedness and prefers to
end a line at sentence punctuation — instead of greedily filling and orphaning
the last word on a line of its own. Text too long for one page word-wraps
greedily and paginates.
"""

from __future__ import annotations


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
    """Split words into exactly ``k`` lines (each <= cols) minimizing raggedness,
    preferring to end a line at sentence punctuation. Returns the lines, or None
    if it can't be done in ``k`` lines. A tiny DP."""
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
    lines, cur = [], ""
    for w in words:
        w = w if len(w) <= cols else w[:cols]
        if len(cur) + len(w) + (1 if cur else 0) <= cols:
            cur = f"{cur} {w}".strip()
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def balanced_pages(text, rows, cols, title=""):
    """``text`` laid out as pages of lines — ``list[list[str]]``, each inner list
    one page's lines (<= cols each, <= rows per page). ``title``, if given, is the
    first line of the first page and the body flows below it. On a one-row wall
    each wrapped line becomes its own page."""
    words = str(text).split() or [""]
    lens = [len(w) for w in words]
    if rows == 1:
        return [[ln] for ln in _greedy(words, cols)]
    body = rows - 1 if title else rows
    if max(lens) <= cols:
        need = _need_lines(lens, cols)
        if need <= body:
            bal = _balance(words, lens, cols, need)
            if bal is not None:
                return [[title, *bal] if title else bal]
    lines = _greedy(words, cols)
    if title:
        pages, i = [[title, *lines[:body]]], body
    else:
        pages, i = [], 0
    while i < len(lines):
        pages.append(lines[i:i + rows])
        i += rows
    return pages
