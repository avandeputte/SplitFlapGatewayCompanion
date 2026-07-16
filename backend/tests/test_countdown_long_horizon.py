"""A countdown to retirement is thousands of days out, and the layouts must say
so legibly. Past a year, years lead: "8Y 267D 14H" where "3187D 14H 22M" only
counted. The tall wall's instrument panel gains a years row — which is also what
retired the 999-day clamp: with days split into years + days-within-the-year,
nothing can overflow the 3-character value column again, so the panel can never
lie about a far-off date. On narrow signs a bare "8Y" says less than the day
total, so promotion only happens when years AND days fit together."""
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

from conftest import load_app

DRAWN = SimpleNamespace(instant=True)


def _pages(rows, cols, days_out, caps=None, **settings):
    target = (datetime.now() + timedelta(days=days_out, hours=12)).replace(microsecond=0)
    settings.setdefault("countdown_event", "RETIRE")
    settings.setdefault("countdown_target", target.strftime("%Y-%m-%dT%H:%M:%S"))
    m = load_app("countdown")
    return m.fetch(settings, lambda *lines, **kw: list(lines),
                   lambda: rows, lambda: cols, caps=caps)


def test_years_lead_on_a_normal_wall():
    lines = _pages(3, 15, 3187)[0]
    assert re.match(r"^8Y 267D( \d{1,2}H)?", lines[1]), lines
    assert all(len(l) <= 15 for l in lines)


def test_narrow_sign_keeps_the_day_total():
    """On 6 columns "8Y 267D" (7 chars) can't fit — a bare "8Y" would say LESS
    than the day count, so the promotion doesn't happen."""
    lines = _pages(2, 6, 3187)[0]
    assert lines[1] == "3187D", lines


def test_under_a_year_is_unchanged():
    lines = _pages(3, 15, 45)[0]
    assert re.match(r"^45D \d{1,2}H \d{1,2}M$", lines[1]), lines


def test_panel_gains_a_years_row_and_never_lies():
    lines = _pages(6, 15, 3187)[0]
    body = lines[1:]
    assert any(re.match(r"^ *8Y", l) for l in body), body      # years row
    assert any(re.match(r"^267D", l) for l in body), body      # days WITHIN the year
    assert not any("999" in l for l in body), body             # the old clamp's lie
    assert all(len(l) <= 15 for l in body)


def test_panel_seconds_yield_to_years_on_a_five_row_wall():
    """Title + 4 unit rows is all a 5-row wall has. Eight years out, the ticking
    seconds row is the one that steps aside — not the years row."""
    lines = _pages(5, 15, 3187, caps=DRAWN, show_seconds="true")[0]
    body = lines[1:]
    assert any(re.match(r"^ *8Y", l) for l in body), body
    assert not any(re.search(r"\dS\b", l) for l in body), body
    # Under a year the Y row is gone and seconds fit again.
    lines = _pages(5, 15, 45, caps=DRAWN, show_seconds="true")[0]
    body = lines[1:]
    assert not any(re.match(r"^ *\d+Y", l) for l in body), body
    assert any(re.search(r"\dS\b", l) for l in body), body
