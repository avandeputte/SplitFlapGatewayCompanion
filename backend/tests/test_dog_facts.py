"""Dog facts — the sibling of cat-facts, and the one thing it has to do differently.

catfact.ninja takes a `max_length` parameter, so the cat app asks the API for a fact that fits
the wall. dogapi has no such parameter: it sends whatever it sends, and some of its facts run
to a paragraph. So the dog app asks for several and CHOOSES, and that choice is the thing worth
testing — a fact that does not fit the wall is not shortened, it is paginated, and a passer-by
then reads two thirds of a sentence about beagles.
"""

from __future__ import annotations

import pytest

from conftest import load_app, make_runtime

dog = load_app("dog-facts")

SHORT = "Dogs have three eyelids."                                   # 24
MEDIUM = "A dog's nose print is as unique as a human fingerprint."   # 55
LONG = ("Dogs can hear sounds at a frequency of up to 45,000 hertz, while humans can only "
        "hear up to 20,000 hertz, which is why they notice things you never will.")


def test_it_prefers_the_longest_fact_that_still_fits_one_page():
    """Longest, not shortest: a fact that fills the wall uses it better than three words
    floating in the middle — but never at the cost of spilling onto a second page."""
    # A 5x22 wall holds MEDIUM comfortably; LONG needs more.
    assert dog._pick([SHORT, MEDIUM, LONG], 250, rows=5, cols=22) == MEDIUM


def test_it_falls_back_to_the_shortest_when_nothing_fits():
    """A 2x12 wall fits none of them; the shortest at least paginates the least."""
    assert dog._pick([MEDIUM, LONG], 250, rows=2, cols=12) == MEDIUM


def test_max_length_is_honoured_over_filling_the_wall():
    """If you asked for facts under 40 characters, a 55-character one is not an answer."""
    assert dog._pick([SHORT, MEDIUM], 40, rows=5, cols=22) == SHORT


def test_blank_facts_are_ignored():
    assert dog._pick(["", "   ", SHORT], 250, rows=5, cols=22) == SHORT
    assert dog._pick(["", "  "], 250, rows=5, cols=22) == ""


# --- the page ---------------------------------------------------------------

def _render(rows, cols):
    rt = make_runtime(installed=["dog-facts"], rows=rows, cols=cols)
    return rt.get_pages("dog-facts")


def _text(pages, rows, cols):
    return " ".join(" ".join(p[r * cols:(r + 1) * cols].strip() for r in range(rows))
                    for p in pages)


@pytest.fixture
def api(monkeypatch):
    import requests

    holder = {"json": {"data": [{"attributes": {"body": MEDIUM}}]}, "boom": False}

    class _Resp:
        def json(self):
            return holder["json"]

    def _get(*a, **k):
        if holder["boom"]:
            raise requests.exceptions.ConnectionError("no network")
        return _Resp()

    monkeypatch.setattr(requests, "get", _get)
    return holder


def test_a_fact_reaches_the_wall(api):
    assert "fingerprint" in _text(_render(5, 22), 5, 22)


def test_an_unreachable_api_says_offline(api):
    api["boom"] = True
    assert "Offline" in _text(_render(3, 15), 3, 15)


def test_an_empty_response_says_no_data(api):
    api["json"] = {"data": []}
    assert "No data" in _text(_render(3, 15), 3, 15)


def test_a_malformed_response_does_not_take_the_app_down(api):
    """The API changing shape under us must not leave a wall showing a stack trace."""
    api["json"] = {"data": [{"nope": True}, None]}
    page = _text(_render(3, 15), 3, 15)
    assert "No data" in page or "Offline" in page
