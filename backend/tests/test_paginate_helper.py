"""The injected `paginate` helper — the balanced word-wrap the advice / quote /
fact apps each used to carry a byte-identical copy of. An app opts in with a
`paginate` parameter and the runtime binds it to the wall (textlayout.py)."""
from app.textlayout import balanced_pages
from conftest import make_runtime


def test_short_text_is_balanced_not_greedy():
    pages = balanced_pages("aaaa bbbb cccc dddd", rows=3, cols=10)
    assert len(pages) == 1
    lines = pages[0]
    assert len(lines) == 2 and all(len(l) <= 10 for l in lines)
    assert lines == ["aaaa bbbb", "cccc dddd"]      # even 2+2, not greedy


def test_a_title_takes_the_first_row():
    pages = balanced_pages("hello world of flaps", rows=3, cols=12, title="NEWS")
    assert pages[0][0] == "NEWS"


def test_long_text_paginates():
    text = " ".join(f"word{i}" for i in range(30))
    pages = balanced_pages(text, rows=3, cols=12)
    assert len(pages) > 1 and all(len(p) <= 3 for p in pages)


def test_one_row_wall_is_a_page_per_line():
    pages = balanced_pages("alpha beta gamma delta", rows=1, cols=11)
    assert all(len(p) == 1 for p in pages)


def test_the_helper_is_injected_and_bound_to_the_wall(tmp_path):
    rt = make_runtime(tmp_path, ["advice"], rows=3, cols=15)
    kw = rt._helper_kwargs("advice", rt._wants["advice"], {})
    assert "paginate" in rt._wants["advice"]
    pages = kw["paginate"]("Advice: be kind whenever possible it is always possible")
    assert pages and all(len(p) == 45 for p in pages)
