"""The second wave of panel apps: Pong and Invaders on the interactive framework
(same contract test_arcade_games pins for Snake/Flappy/Breakout), the Falling Sand
frame-push toy, and the Sensor Graph HA sampler.
"""

from conftest import canvas_surface, load_app, make_runtime
from test_canvas_ops35 import OPS35


def _cv(w=128, h=64):
    return canvas_surface("http://gw", w, h, ("rgb888",), (), ops=OPS35, ops_bin=True,
                          sprite=True)


class _Ctl:
    def __init__(self, dir_=None, events=(), taps=(), presses=0, engaged=True):
        self.dir = dir_
        self.events = list(events)
        self.taps = list(taps)
        self.presses = presses
        self._engaged = engaged

    def active(self, within=6.0):
        return self._engaged


# --- shared contract ---------------------------------------------------------

def test_catalog_flags_and_transport(gw_calls):
    rt = make_runtime(installed=["canvas-pong", "canvas-invaders", "canvas-sand",
                                 "canvas-sensor-graph"])
    cards = {a["id"]: a for a in rt.app_list()}
    for app_id in ("canvas-pong", "canvas-invaders", "canvas-sand"):
        assert cards[app_id]["interactive"] is True, app_id
    assert "interactive" not in cards["canvas-sensor-graph"] or \
        not cards["canvas-sensor-graph"]["interactive"]
    for app_id in cards:
        assert cards[app_id]["icon_svg"].startswith("data:image/svg+xml,"), app_id

    for app_id in ("canvas-pong", "canvas-invaders"):    # the games stream binary ops
        gw_calls.clear()
        load_app(app_id).fetch_matrix({"speed": "5"}, _cv())
        assert "/api/canvas/opsb" in [c[1] for c in gw_calls], app_id
    gw_calls.clear()                                     # the sand toy pushes frames
    load_app("canvas-sand").fetch_matrix({"speed": "5"}, _cv())
    assert any("/api/canvas/frame" in c[1] for c in gw_calls)


def test_games_are_silent_in_attract():
    for app_id in ("canvas-pong", "canvas-invaders", "canvas-sand"):
        app = load_app(app_id)
        cv = _cv()
        sounds = []
        for _ in range(60):
            app.fetch_matrix({"speed": "8"}, cv, controls=_Ctl(engaged=False),
                             play_sound=lambda **kw: sounds.append(kw))
        assert not sounds, app_id


# --- pong --------------------------------------------------------------------

def test_pong_attract_rallies_in_bounds():
    app = load_app("canvas-pong")
    cv = _cv()
    for _ in range(200):
        app.fetch_matrix({"speed": "8"}, cv)
        st = app._state._st
        assert -3 <= st["ball"]["x"] <= cv.width + 3
        assert 0 <= st["ball"]["y"] <= cv.height
    assert st["phase"] == "play"                         # a demo match never sticks


def test_pong_player_paddle_follows_held_direction():
    app = load_app("canvas-pong")
    cv = _cv()
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    y0 = st["ly"]
    for _ in range(3):
        app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl("down", presses=1))
    assert st["ly"] > y0
    for _ in range(6):
        app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl("up", presses=1))
    assert st["ly"] < y0


def test_pong_match_point_wins_and_restarts():
    app = load_app("canvas-pong")
    cv = _cv()
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    for _ in range(7):                                   # concede seven: teleport past the left paddle
        st["ball"].update(x=-3.0, vx=-2.0)
        app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(presses=1))
    assert st["phase"] == "gameover" and st["winner"] == "r" and st["sr"] == 7
    f0 = st["fade"]
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(presses=1))
    assert st["fade"] > f0                               # fading while frozen
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(presses=2))   # any key
    assert st["phase"] == "play" and st["sl"] == 0 and st["sr"] == 0


# --- invaders ----------------------------------------------------------------

def test_invaders_fleet_marches_and_turns():
    app = load_app("canvas-invaders")
    cv = _cv()
    dirs = set()
    for _ in range(300):
        app.fetch_matrix({"speed": "8"}, cv)
        st = app._state._st
        dirs.add(st["dir"])
    assert dirs == {1, -1}                               # it reached an edge and flipped
    assert st["oy"] > 9                                  # and stepped down


def test_invaders_tap_fires_one_bolt_at_a_time():
    app = load_app("canvas-invaders")
    cv = _cv()
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(taps=["up"], presses=2))
    assert st["bolt"] is not None
    y0 = st["bolt"]["y"]
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(taps=["up"], presses=3))
    assert st["bolt"]["y"] < y0                          # same bolt still flying, no re-fire


def test_invaders_bolt_kills_and_scores():
    app = load_app("canvas-invaders")
    cv = _cv()
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    r, c = min(st["alive"])
    x, y = app._alien_xy(st, r, c)
    st["bolt"] = {"x": x + 3.0, "y": y + 6.0}            # one step above the alien
    n0, s0 = len(st["alive"]), st["score"]
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(presses=1))
    assert len(st["alive"]) == n0 - 1 and st["score"] > s0


def test_invaders_bomb_costs_a_life_and_holds_ready():
    app = load_app("canvas-invaders")
    cv = _cv()
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    st["bombs"] = [{"x": st["cx"], "y": cv.height - 6.0}]
    lives = st["lives"]
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(presses=1))
    assert st["lives"] == lives - 1 and st["phase"] == "ready"
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(presses=1))    # arms the freeze
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(presses=2))    # fresh key resumes
    assert st["phase"] == "play"


def test_invaders_invasion_ends_the_game_outright():
    app = load_app("canvas-invaders")
    cv = _cv()
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    st["oy"] = float(cv.height)                          # the fleet reaches the cannon row
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(presses=1))
    assert st["phase"] == "gameover"                     # all three lives at once


def test_invaders_clearing_the_wave_levels_up():
    app = load_app("canvas-invaders")
    cv = _cv()
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    keep = min(st["alive"])
    st["alive"] = {keep}
    x, y = app._alien_xy(st, *keep)
    st["bolt"] = {"x": x + 3.0, "y": y + 6.0}
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(presses=1))
    assert st["level"] == 2 and len(st["alive"]) > 1


# --- falling sand ------------------------------------------------------------

def test_sand_pours_and_piles():
    app = load_app("canvas-sand")
    cv = _cv()
    for _ in range(150):
        app.fetch_matrix({"speed": "8"}, cv)
    st = app._state._st
    assert st["settled"] > 100                           # dunes are forming
    floor = st["grid"][(cv.height - 1) * cv.width:]      # and they sit on the floor
    assert any(floor)


def test_sand_player_steers_the_spout_and_bursts():
    app = load_app("canvas-sand")
    cv = _cv()
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(presses=1))
    st = app._state._st
    x0 = st["pour_x"]
    for _ in range(4):
        app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl("left", presses=1))
    assert st["pour_x"] < x0
    n0 = len(st["air"]) + st["settled"]
    app.fetch_matrix({"speed": "5"}, cv, controls=_Ctl(presses=2))   # fresh press: burst
    assert len(st["air"]) + st["settled"] >= n0 + 30


def test_sand_full_panel_fades_and_repours():
    app = load_app("canvas-sand")
    cv = _cv()
    app.fetch_matrix({"speed": "5"}, cv)
    st = app._state._st
    st["settled"] = cv.width * cv.height                 # pretend the pile swallowed it all
    app.fetch_matrix({"speed": "5"}, cv)
    assert st["phase"] == "fade"
    for _ in range(20):
        app.fetch_matrix({"speed": "5"}, cv)
    st = app._state._st
    assert st["phase"] == "pour" and st["settled"] == 0  # fresh floor, pour goes on


# --- sensor graph ------------------------------------------------------------

def _states(v, eid="sensor.office_temp", unit="°C"):
    return [{"entity_id": eid, "state": str(v),
             "attributes": {"friendly_name": "Office Temp", "unit_of_measurement": unit}}]


def test_sensor_graph_samples_and_frames(gw_calls):
    app = load_app("canvas-sensor-graph")
    cv = _cv()
    cfg = {"config": "sensor.office_temp | Office", "polling_rate": "30"}
    for v in (21.0, 21.4, 21.9):
        app.fetch_matrix(cfg, cv, get_ha_states=lambda v=v: _states(v))
    st = app.fetch_matrix._state
    assert [p[1] for p in st["hist"]["sensor.office_temp"]] == [21.0, 21.4, 21.9]
    assert any("/api/canvas/frame" in c[1] for c in gw_calls)


def test_sensor_graph_rotates_a_board():
    app = load_app("canvas-sensor-graph")
    cv = _cv()
    cfg = {"config": "sensor.a\nsensor.b"}
    both = [{"entity_id": "sensor.a", "state": "1", "attributes": {}},
            {"entity_id": "sensor.b", "state": "2", "attributes": {}}]
    hold = app.fetch_matrix(cfg, cv, get_ha_states=lambda: both)
    st = app.fetch_matrix._state
    assert st["idx"] == 1 and hold == 8.0                # dwell, then the next entity
    assert set(st["hist"]) == {"sensor.a", "sensor.b"}   # both sampled every call


def test_sensor_graph_waits_out_non_numeric_states(gw_calls):
    app = load_app("canvas-sensor-graph")
    cv = _cv()
    cfg = {"config": "binary_sensor.door | Door"}
    app.fetch_matrix(cfg, cv, get_ha_states=lambda: [
        {"entity_id": "binary_sensor.door", "state": "on", "attributes": {}}])
    st = app.fetch_matrix._state
    assert st["hist"].get("binary_sensor.door") in (None, [])
    assert any("/api/canvas/frame" in c[1] for c in gw_calls)   # the text card still shows


def test_sensor_graph_seeds_the_window_from_ha_history(gw_calls):
    # In a playlist the sampled window never fills — the first draw must arrive with
    # HA's own history already on the line, live samples appending after it.
    import time
    app = load_app("canvas-sensor-graph")
    cv = _cv()
    now = time.time()
    seed = [(now - (20 - i) * 60.0, 20.0 + i * 0.1) for i in range(20)]
    calls = []

    def history(eid, minutes):
        calls.append((eid, minutes))
        return list(seed)

    app.fetch_matrix({"config": "sensor.office_temp | Office", "window": "60"}, cv,
                     get_ha_states=lambda: _states(21.9), get_ha_history=history)
    st = app.fetch_matrix._state
    hist = st["hist"]["sensor.office_temp"]
    assert calls == [("sensor.office_temp", 60)]
    assert len(hist) >= 20 and hist[-1][1] == 21.9       # seeded + the live sample on top
    app.fetch_matrix({"config": "sensor.office_temp | Office", "window": "60"}, cv,
                     get_ha_states=lambda: _states(21.9), get_ha_history=history)
    assert len(calls) == 1                               # seeded once, not per fetch


def test_sensor_graph_survives_history_failure_and_reseeds_grown_windows(gw_calls):
    app = load_app("canvas-sensor-graph")
    cv = _cv()
    boom = lambda eid, minutes: (_ for _ in ()).throw(OSError("recorder down"))
    app.fetch_matrix({"config": "sensor.office_temp"}, cv,
                     get_ha_states=lambda: _states(21.0), get_ha_history=boom)
    st = app.fetch_matrix._state
    assert [p[1] for p in st["hist"]["sensor.office_temp"]] == [21.0]   # live-only fallback

    calls = []
    st["seeded"]["sensor.office_temp"] = [3600.0, 0.0]   # a seeded hour...
    app.fetch_matrix({"config": "sensor.office_temp", "window": "120"}, cv,
                     get_ha_states=lambda: _states(21.0),
                     get_ha_history=lambda e, m: calls.append((e, m)) or [])
    assert calls == [("sensor.office_temp", 120)]        # ...re-seeds for the grown window


def test_sensor_graph_threshold_polarity_colors_the_card(gw_calls):
    # CO2 rising to 900 under `<500,1000`: amber, not the old in-band green — and a
    # battery at 90 under `>20,80` is green even though 90 would sit "outside a band".
    app = load_app("canvas-sensor-graph")
    cv = _cv()
    assert app._parse_band("<500,1000") == ("low", 500.0, 1000.0)
    assert app._band_color(("low", 500, 1000), 300) == app._UP
    assert app._band_color(("low", 500, 1000), 900) == app._MID
    assert app._band_color(("low", 500, 1000), 1200) == app._DN
    assert app._band_color(("high", 20, 80), 90) == app._UP
    assert app._band_color(("high", 20, 80), 10) == app._DN
    assert app._band_color(("band", 60, 78), 72.4) == app._UP
    app.fetch_matrix({"config": "sensor.co2 | CO2 | <500,1000"}, cv,
                     get_ha_states=lambda: _states(900, eid="sensor.co2", unit="ppm"))
    assert any("/api/canvas/frame" in c[1] for c in gw_calls)   # renders through the mode


def test_sensor_graph_long_labels_ellipsize_instead_of_shrinking_to_blobs():
    # Below ~10px the bold face's counters close (A/X/Y render solid), so a long
    # friendly name must ellipsize at the legibility floor, never shrink past it.
    app = load_app("canvas-sensor-graph")
    cv = _cv()
    budget = int(cv.width * 0.72)
    t, m = app._fit_label(cv, "OFFICE AIR QUALITY MONITOR CARBON DIOXIDE",
                          cv.height * 0.18, budget)
    assert t.endswith("…") and m["font"].size >= 10 and m["w"] <= budget
    t2, _ = app._fit_label(cv, "OFFICE", cv.height * 0.18, budget)
    assert t2 == "OFFICE"                                # short labels pass through


def test_sensor_graph_entity_picker_searches_like_the_entity_board():
    # The entity_table widget only searches when the field carries searchUrl/resultKey
    # (the frontend wires the box to f.searchUrl) — dropping them leaves a dead search
    # input, which is exactly how it shipped in 2.10.9-beta.9.
    import json
    from conftest import APPS_DIR
    board = json.loads((APPS_DIR / "entity-board" / "manifest.json").read_text("utf-8"))
    graph = json.loads((APPS_DIR / "canvas-sensor-graph" / "manifest.json").read_text("utf-8"))
    pick = {s["key"]: s for s in board["settings"]}["config"]
    mine = {s["key"]: s for s in graph["settings"]}["config"]
    assert mine["searchUrl"] == pick["searchUrl"] == "/ha_entities"
    assert mine["resultKey"] == pick["resultKey"] == "results"


def test_sensor_graph_without_ha_shows_notice(gw_calls):
    app = load_app("canvas-sensor-graph")
    cv = _cv()
    app.fetch_matrix({"config": "sensor.x"}, cv)         # no get_ha_states at all
    assert any("/api/canvas/frame" in c[1] for c in gw_calls)
