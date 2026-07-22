"""Channel page order is configurable in the manifest. A quotes channel can shuffle
(order: random); a jokes channel must not, because a setup and its punchline are
consecutive pages. When a random app DOES have multi-page items, they stay together
and in order — grouped by a uniform group_size or explicit per-page group markers.
"""
import json

from conftest import make_runtime


def _channel(tmp_path, pages, **manifest):
    d = tmp_path / "user_apps" / "chan"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(
        {"id": "chan", "name": "Chan", "type": "channel", **manifest}), "utf-8")
    (d / "data.json").write_text(json.dumps({"pages": pages}), "utf-8")
    rt = make_runtime(tmp_path, ["chan"], user_apps_dir=tmp_path / "user_apps")
    return rt


def _pages(rt):
    return rt._channel_pages("chan", "en-US")


def test_sequential_is_the_default_and_preserves_order(tmp_path):
    rt = _channel(tmp_path, [{"lines": [str(i), "", ""]} for i in range(10)])
    assert _pages(rt) == [rt.format_lines(str(i), "", "") for i in range(10)]


def test_random_reorders_but_keeps_every_page(tmp_path):
    rt = _channel(tmp_path, [{"lines": [str(i), "", ""]} for i in range(30)],
                  order="random")
    runs = [tuple(_pages(rt)) for _ in range(20)]
    assert len({r for r in runs}) > 1, "random never actually reordered"
    complete = sorted(rt.format_lines(str(i), "", "") for i in range(30))
    for run in runs:
        assert sorted(run) == complete, "a page went missing or was duplicated"


def test_group_size_keeps_multi_page_items_together(tmp_path):
    # 4 jokes, each setup+punchline; random order, but never a split.
    pages = []
    for j in range(4):
        pages += [{"lines": [f"J{j} setup", "", ""]}, {"lines": [f"J{j} punch", "", ""]}]
    rt = _channel(tmp_path, pages, order="random", group_size=2)
    for _ in range(30):
        flat = _pages(rt)
        # every setup is immediately followed by its own punchline
        for i in range(0, len(flat), 2):
            assert "setup" in flat[i] and "punch" in flat[i + 1]
            assert flat[i].split()[0] == flat[i + 1].split()[0]


def test_explicit_group_markers_keep_mixed_items_together(tmp_path):
    pages = [
        {"lines": ["solo A", "", ""]},
        {"lines": ["pair 1of2", "", ""], "group": "g1"},
        {"lines": ["pair 2of2", "", ""], "group": "g1"},
        {"lines": ["solo B", "", ""]},
    ]
    rt = _channel(tmp_path, pages, order="random")
    for _ in range(30):
        flat = _pages(rt)
        i = next(k for k, p in enumerate(flat) if "1of2" in p)
        assert "2of2" in flat[i + 1], flat        # the pair never comes apart


def test_the_shipped_defaults_are_sane():
    """Quote/joke channels shuffle (a quiz keeps each question+answer together as it shuffles);
    one-liners stay in their curated order."""
    import pathlib
    apps = pathlib.Path(__file__).resolve().parents[2] / "apps"

    def order(app):
        return json.loads((apps / app / "manifest.json").read_text()).get("order", "sequential")

    for a in ("magic-8-ball", "stoic-quotes", "star-wars-quotes",
              "good-morning", "good-night", "fortune-cookie", "dad-jokes"):
        assert order(a) == "random", a
    for a in ("funny-one-liners",):
        assert order(a) == "sequential", a


def test_shipped_channels_use_the_groups_format():
    """The catalog channels were converted from hand-split `pages` to `groups`
    (you write text, the engine wraps). A regression back to pre-split lines
    would show here."""
    import json
    import pathlib
    apps = pathlib.Path(__file__).resolve().parents[2] / "apps"
    for app in ("dad-jokes", "magic-8-ball", "stoic-quotes", "office-quotes",
                "shower-thoughts", "motivational-quotes", "harry-potter-quotes"):
        doc = json.loads((apps / app / "data.json").read_text("utf-8"))
        assert "groups" in doc and doc["groups"], f"{app}: not in groups format"


def test_a_shipped_multipage_item_never_splits_under_random(tmp_path):
    """office-quotes is order:random and has two 2-page items; render it many
    times and assert the pages of each item are always consecutive."""
    from conftest import make_runtime
    rt = make_runtime(tmp_path, ["office-quotes"], rows=3, cols=15)
    for _ in range(40):
        pages = rt._channel_pages("office-quotes", "en-US")
        # the Gretzky item's two pages: "...shots you don't take" then "Wayne Gretzky"
        idx = next((i for i, p in enumerate(pages) if "Gretzky" in p), None)
        if idx is not None:
            assert "100%" in pages[idx - 1] or "shots" in pages[idx - 1], \
                "the Gretzky quote's two pages came apart"
