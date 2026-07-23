"""The Scoreboard shares ONE sprite sheet across all its games (blitted by index), rather than
uploading a fresh two-logo sheet per game — which would take a whole atlas slot per matchup and
duplicate each team's logo. This pins the app-level shape; the wire-level dedup (identical tiles
upload once) is covered in test_canvas.py.
"""

import importlib.util
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def _load():
    p = ROOT / "apps" / "canvas-scoreboard" / "app.py"
    spec = importlib.util.spec_from_file_location("_sb", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Cv:
    width, height, can_sprite = 128, 64, True

    def __init__(self):
        self.uploads = []          # tiles-per-upload, one entry per upload_atlas call
        self.blits = []            # sprite index per blit

    def clear(self, *a, **k): pass
    def roundrect(self, *a, **k): pass
    def shadow_text(self, *a, **k): pass
    def face(self, z): return 8
    def face_width(self, f): return 5
    def cp(self, x): return str(x)
    def upload_atlas(self, imgs, fmt="rgb888"): self.uploads.append(len(imgs)); return True
    def sprite(self, i, x, y): self.blits.append(i)
    def show(self): return True


def _slate(n):
    teams = ["BOS", "LAL", "GSW", "MIA", "NYK", "PHI", "BKN", "LAC"]
    out = []
    for i in range(n):
        a, h = teams[i % len(teams)], teams[(i + 3) % len(teams)]
        out.append({"alogo": f"L/{a}", "hlogo": f"L/{h}", "aa": a, "ha": h,
                    "ac": (0, 120, 50), "hc": (80, 40, 130), "as": "1", "hs": "2",
                    "state": "in", "lg": "NBA", "status": "Q4", "anm": [a], "hnm": [h]})
    return out


def test_scoreboard_uses_one_shared_sheet_of_all_logos():
    m = _load()
    m._logo_tile = lambda url, size, cache: Image.new("RGB", (size, size), (50, 80, 160))
    m._games = lambda follow, filt: _slate(6)          # 6 games, 8 distinct teams
    m.fetch_matrix.__dict__.pop("_state", None)
    cv = _Cv()
    for _ in range(18):                                 # three full rotations
        m.fetch_matrix({"follow": "nba"}, cv)

    st = m.fetch_matrix.__dict__["_state"]
    assert len(st["sheet"]) == 8                        # ONE sheet holding all 8 distinct logos
    assert len(st["sheet_idx"]) == 8
    assert max(cv.uploads) == 8                         # never a per-game 2-tile sheet after growth
    assert cv.uploads[-6:] == [8, 8, 8, 8, 8, 8]        # stable after the first rotation -> wire dedups it
    assert all(0 <= i < 8 for i in cv.blits)            # every game blits by index into that sheet


def test_scoreboard_refetches_when_the_follow_changes():
    """The game list is cached, but keyed on the selection — so a per-playlist team override takes
    effect at once instead of showing the previous slate until the 120 s cache lapses."""
    m = _load()
    calls = []
    m._logo_tile = lambda url, size, cache: None
    m._games = lambda follow, filt: calls.append(follow) or []
    m.fetch_matrix.__dict__.pop("_state", None)
    cv = _Cv()
    m.fetch_matrix({"follow": "nba"}, cv)          # first slate
    m.fetch_matrix({"follow": "nba"}, cv)          # same -> served from cache
    m.fetch_matrix({"follow": "epl:ARS"}, cv)      # changed -> refetch at once
    assert calls == ["nba", "epl:ARS"]


def test_a_missing_logo_falls_back_to_a_color_chip():
    m = _load()
    m._logo_tile = lambda url, size, cache: None        # no logo fetchable
    m._games = lambda follow, filt: _slate(2)
    m.fetch_matrix.__dict__.pop("_state", None)
    cv = _Cv()
    m.fetch_matrix({"follow": "nba"}, cv)
    assert m.fetch_matrix.__dict__["_state"]["sheet"] == []    # nothing uploaded
    assert cv.blits == []                               # drawn as color chips, not sprites
