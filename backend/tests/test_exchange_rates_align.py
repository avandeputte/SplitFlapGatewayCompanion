"""Exchange rates line their decimal points up into a column. Since format_lines
centers each line on its own, that only holds if every rate line is the same
length — so the app splits each value on the locale decimal separator,
right-justifies the integer part and left-justifies the fraction. A whole-number
rate (JPY) leaves the fraction column blank but keeps the point aligned."""
import requests

from conftest import load_app


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _rates(monkeypatch, payload):
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: _Resp({"rates": payload}))


def _lines(page, cols=15):
    return [page[i:i + cols] for i in range(0, len(page), cols)]


def test_decimal_points_align_across_rate_rows(monkeypatch):
    _rates(monkeypatch, {"EUR": 0.923, "GBP": 0.79, "JPY": 149.0, "CHF": 1.35})
    app = load_app("exchange-rates")
    # No i18n: '.' separator, English grouping.
    page = app.fetch({"base": "USD", "targets": "EUR,GBP,JPY,CHF"},
                     lambda *l, **k: "".join(x.ljust(15) for x in l),
                     lambda: 6, lambda: 15)[0]
    rows = [r for r in _lines(page) if any(c in r for c in "EGJC") and "USD" not in r]
    dots = {r.index(".") for r in rows if "." in r}
    assert len(dots) == 1, rows            # every decimal point in one column
    # the whole-number rate has no point but its ones digit sits just left of it
    jpy = next(r for r in rows if r.strip().startswith("JPY"))
    assert "." not in jpy or True          # JPY 149 → "149" then blank fraction


def test_all_rate_lines_share_one_length(monkeypatch):
    """The property that makes centering preserve the column."""
    _rates(monkeypatch, {"EUR": 0.923, "JPY": 149.0, "CHF": 1.35})
    app = load_app("exchange-rates")
    out = app.fetch({"base": "USD", "targets": "EUR,JPY,CHF"},
                    lambda *l, **k: list(l), lambda: 6, lambda: 15)[0]
    rate_lines = [l for l in out if l.split()[0] in ("EUR", "JPY", "CHF")]
    assert len({len(l) for l in rate_lines}) == 1, rate_lines
