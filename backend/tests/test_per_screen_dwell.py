"""Per-screen dwell for the apps with distinct predefined screens (formula1, trivia).

Each screen returns its page as {"text": ..., "seconds": N}; the engine holds each page that
long (the engine side is pinned in test_engine_interrupts). Defaults equal the old per-app
loop_delay, so nothing changes until a user customizes. Dashboard's own per-screen tests live
in test_tall_wall_apps.
"""
from conftest import load_app


def _fmt(*lines, **kw):
    return list(lines)


# --- formula1: Next race + Championship standings ---------------------------
def _f1(monkeypatch):
    f1 = load_app("formula1")
    monkeypatch.setattr(f1, "_next_race", lambda: {"raceName": "Monaco Grand Prix"})
    monkeypatch.setattr(f1, "_race_start", lambda r: None)      # no countdown -> plain page
    monkeypatch.setattr(f1, "_driver_standings",
                        lambda: [{"Driver": {"familyName": "Verstappen"}, "points": "310"}])
    return f1


def test_formula1_race_and_standings_carry_their_own_dwell(monkeypatch):
    f1 = _f1(monkeypatch)
    pages = f1.fetch({"secs_race": "9", "secs_standings": "21"}, _fmt, lambda: 3, lambda: 15)
    assert [p["seconds"] for p in pages] == [9, 21]            # [race, standings]


def test_formula1_dwell_defaults_preserve_old_delay(monkeypatch):
    f1 = _f1(monkeypatch)
    pages = f1.fetch({}, _fmt, lambda: 3, lambda: 15)
    assert [p["seconds"] for p in pages] == [6, 6]             # was the uniform loop_delay 6


# --- trivia: Question then Answer -------------------------------------------
def test_trivia_question_and_answer_carry_their_own_dwell(monkeypatch):
    tr = load_app("trivia")
    monkeypatch.setattr(tr, "_fetch_qa", lambda: ("Who painted the Mona Lisa?", "Da Vinci"))
    pages = tr.fetch({"secs_question": "15", "secs_answer": "6"}, _fmt, lambda: 3, lambda: 20)
    assert pages[0]["seconds"] == 15                           # the question leads
    assert pages[-1]["seconds"] == 6                           # the answer reveals last
    assert any("Answer:" in "".join(p["text"]) for p in pages)


def test_trivia_dwell_defaults_preserve_old_delay(monkeypatch):
    tr = load_app("trivia")
    monkeypatch.setattr(tr, "_fetch_qa", lambda: ("Q?", "A"))
    pages = tr.fetch({}, _fmt, lambda: 3, lambda: 20)
    assert all(p["seconds"] == 10 for p in pages)             # was the uniform loop_delay 10
