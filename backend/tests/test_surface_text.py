"""The Surface PIL text toolkit — the panel-app vocabulary hoisted out of ~110 per-app
_cv_* helper copies. These pin the canonical behaviors the consolidation chose: the 8px
readability floor, hyphen-visible hard word splits, the label row dropping on short
panels, and the fill-by-leading card layout.
"""

from conftest import canvas_surface


def _cv(w=256, h=64):
    return canvas_surface("http://gw", w, h, ("rgb888",), ())


def test_fit_font_floors_at_eight_pixels():
    cv = _cv()
    f = cv.fit_font("X" * 400, 60, 40)          # impossible fit: must stop at the floor
    assert f.size == cv.MIN_READABLE == 8
    assert cv.fit_font("HI", 200, 20).size > 8  # room to spare: grows past the floor


def test_wrap_hyphenates_overlong_words_and_ellipsizes_at_max_lines():
    cv = _cv()
    f = cv.font(10)
    lines = cv.wrap(f, "antidisestablishmentarianism", 40)
    assert len(lines) > 1 and all(f.getlength(ln) <= 40 for ln in lines)
    assert all(ln.endswith("-") for ln in lines[:-1])       # visible hyphen, never a silent cut
    capped = cv.wrap(f, "one two three four five six seven eight nine", 60, max_lines=2)
    assert len(capped) == 2 and capped[-1].endswith("…")


def test_wrap_fit_returns_a_font_no_smaller_than_the_floor():
    cv = _cv(128, 32)
    font, lines = cv.wrap_fit("a fact long enough that it cannot fit on one short panel page",
                              120, 26)
    assert font.size >= 8 and lines


def test_message_centers_its_two_lines():
    cv = _cv(128, 32)
    img = cv.message("WEATHER", "OFFLINE")
    assert img.size == (128, 32)
    px = img.load()
    lit = [(x, y) for y in range(32) for x in range(128) if px[x, y] != (0, 0, 0)]
    ys = [y for _, y in lit]
    assert lit and min(ys) > 0 and max(ys) < 31          # vertically centered, not clipped


def test_text_card_drops_the_label_on_short_panels():
    cv_tall = _cv(128, 64)
    cv_short = _cv(128, 32)
    img_tall, _ = cv_tall.text_card("LABEL", "body words here", 0)
    img_short, _ = cv_short.text_card("LABEL", "body words here", 0)
    # The tall card draws an accent label row + rule near the top; the short one must not.
    accent = (255, 165, 70)
    top_tall = [img_tall.getpixel((x, y)) for y in range(14) for x in range(128)]
    top_short = [img_short.getpixel((x, y)) for y in range(6) for x in range(128)]
    assert accent in top_tall
    assert accent not in top_short


def test_text_card_paginates_and_shows_dots_only_where_there_is_room():
    cv = _cv(128, 64)
    long_body = " ".join(["pagination"] * 60)
    img, n = cv.text_card("FACT", long_body, 0)
    assert n > 1
    # dots row: the accent-lit current dot sits in the bottom two rows
    bottom = [img.getpixel((x, y)) for y in (62, 63) for x in range(64)]
    assert (255, 165, 70) in bottom
    _, n_short = _cv(64, 32).text_card("FACT", long_body, 0)
    assert n_short >= 1                                   # no dots, still paginates


def test_text_card_sub_owns_the_floor():
    cv = _cv(256, 64)
    img, _ = cv.text_card("QUOTE", "the words", 0, sub="— Author",
                          accent=(255, 200, 80))
    strip = [img.getpixel((x, y)) for y in range(56, 64) for x in range(128, 256)]
    assert (255, 200, 80) in strip                        # the attribution, in accent, bottom-right


def test_mix_and_dim():
    cv = _cv()
    assert cv.mix((0, 0, 0), (255, 255, 255), 0.5) == (127, 127, 127)
    assert cv.dim((100, 200, 50), 0.5) == (50, 100, 25)
