"""Tests for the Vestaboard-compatible Local API (app/vestaboard.py + its endpoints).

Two things are being pinned here: the character codec (a published table — if we get a
code wrong, someone's message shows the wrong letter), and the compatibility contract a
real Vestaboard client relies on (auth header, payload shapes, 6x22 → this wall).
"""

import pytest
from fastapi.testclient import TestClient

from app import vestaboard as vb

ROWS, COLS = 3, 15   # the companion's default grid — and a Vestaboard Note


# --- the character table ------------------------------------------------------
def test_code_table_matches_the_published_one():
    assert vb.CODE_TO_CHAR[0] == " "
    assert vb.CODE_TO_CHAR[1] == "A" and vb.CODE_TO_CHAR[26] == "Z"
    assert vb.CODE_TO_CHAR[27] == "1" and vb.CODE_TO_CHAR[35] == "9"
    assert vb.CODE_TO_CHAR[36] == "0"          # zero comes AFTER the nines
    assert vb.CODE_TO_CHAR[37] == "!" and vb.CODE_TO_CHAR[60] == "?"
    assert vb.CODE_TO_CHAR[62] == "°"
    # colour chips -> the firmware's colour flaps; violet is `p`
    assert [vb.CODE_TO_CHAR[c] for c in range(63, 70)] == ["r", "o", "y", "g", "b", "p", "w"]


@pytest.mark.parametrize("code", [43, 45, 51, 57, 58, 61])
def test_codes_absent_from_the_table_decode_to_a_blank(code):
    """A real board has no flap for these, so neither do we — but they must not blow up."""
    assert vb.decode([[code]]) == [" "]


def test_black_and_filled_are_the_lossy_pair():
    assert vb.decode([[70]]) == [" "]     # no black flap; blank is the convention (⬛ -> " ")
    assert vb.decode([[71]]) == ["w"]     # `filled` is a solid tile -> white
    # ...and they do not round-trip, by construction:
    assert vb.encode([" "], 1, 1) == [[0]]
    assert vb.encode(["w"], 1, 1) == [[69]]


def test_every_valid_code_round_trips_except_the_lossy_aliases():
    for code in range(0, vb.MAX_CODE + 1):
        char = vb.decode([[code]])[0]
        back = vb.encode([char], 1, 1)[0][0]
        if code in (70, 71) or code not in vb.CODE_TO_CHAR:
            continue                       # documented one-way cases
        assert back == code, f"code {code} ({char!r}) came back as {back}"


def test_encode_maps_unrepresentable_characters_to_blank():
    # é has no Vestaboard code at all. The board can't say it, so it reads as blank.
    assert vb.encode(["é"], 1, 1) == [[0]]


def test_encode_does_not_mistake_a_colour_flap_for_a_letter():
    """`y` is a yellow tile and `Y` is the letter Y — the case rule must survive a read."""
    assert vb.encode(["y"], 1, 1) == [[65]]   # yellow chip
    assert vb.encode(["Y"], 1, 1) == [[25]]   # letter Y


# --- decode validation (a real board rejects these too) -----------------------
@pytest.mark.parametrize("bad", [
    [], "HELLO", [[]], [[0, 1], [0]],       # empty / not a matrix / ragged is fine? see below
    [[72]], [[-1]], [[999]],                # outside 0..71
    [["A"]], [[None]], [[True]],            # not integer codes
])
def test_decode_rejects_what_a_board_would_reject(bad):
    with pytest.raises(vb.VestaboardError):
        vb.decode(bad)


# --- geometry -----------------------------------------------------------------
def _matrix(rows: list[str], cols: int) -> list[list[int]]:
    """Text rows -> a character-code matrix, padded to `cols` (like a client would)."""
    return [[vb.CHAR_TO_CODE.get(ch, 0) for ch in r.ljust(cols)[:cols]] for r in rows]


def test_a_note_shaped_message_lands_cell_for_cell():
    """3x15 in, 3x15 out: no fitting, no cleverness — a Vestaboard Note IS this grid."""
    rows = ["HELLO WORLD    ", "  FROM THE     ", "  VESTABOARD   "]
    page = vb.fit(vb.decode(_matrix(rows, COLS)), ROWS, COLS)
    assert page == "".join(rows)
    assert len(page) == ROWS * COLS


def test_a_flagship_message_is_compacted_onto_the_wall():
    """A 6x22 client centres its text inside the board, so the payload is mostly blank
    padding. Cropping the top-left corner would show blank rows; compacting shows the
    message."""
    flagship = ["", "", "     HELLO WORLD", "     FROM VESTABOARD", "", ""]
    page = vb.fit(vb.decode(_matrix(flagship, 22)), ROWS, COLS)
    lines = [page[i * COLS:(i + 1) * COLS] for i in range(ROWS)]
    # Two content rows can't centre exactly in three, so they sit at the top.
    assert [l.strip() for l in lines] == ["HELLO WORLD", "FROM VESTABOARD", ""]
    assert len(page) == ROWS * COLS
    # The block moves as a block: only the shared margin is trimmed, so the sender's
    # relative alignment survives (lines are NOT re-centred one by one, which would
    # scramble something like a right-aligned column of numbers).
    assert lines[0] == "HELLO WORLD".ljust(COLS)


def test_a_narrow_block_is_centred_on_the_wall():
    page = vb.fit(vb.decode(_matrix(["", "         HI", ""], 22)), ROWS, COLS)
    assert page[COLS:COLS * 2] == "HI".center(COLS)


def test_an_all_blank_message_clears_the_board():
    page = vb.fit(vb.decode([[0] * 22 for _ in range(6)]), ROWS, COLS)
    assert page == " " * (ROWS * COLS)


def test_overlong_content_is_cropped_not_wrapped():
    page = vb.fit(vb.decode(_matrix(["X" * 30], 30)), ROWS, COLS)
    assert page[COLS:COLS * 2] == "X" * COLS      # centred vertically, cropped to width


# --- the {"text": ...} extension ---------------------------------------------
def test_text_is_wrapped_and_centred():
    page = vb.layout_text("HELLO WORLD FROM HOME ASSISTANT", ROWS, COLS)
    lines = [page[i * COLS:(i + 1) * COLS] for i in range(ROWS)]
    assert [l.strip() for l in lines] == ["HELLO WORLD", "FROM HOME", "ASSISTANT"]
    assert all(len(l) == COLS for l in lines)
    assert lines[0] == "HELLO WORLD".center(COLS)


def test_text_honours_explicit_newlines():
    page = vb.layout_text("ONE\nTWO", ROWS, COLS)
    lines = [page[i * COLS:(i + 1) * COLS] for i in range(ROWS)]
    assert [l.strip() for l in lines] == ["ONE", "TWO", ""]


def test_a_word_longer_than_the_wall_is_split_not_lost():
    page = vb.layout_text("SUPERCALIFRAGILISTIC", 1, 10)
    assert page == "SUPERCALIF"


def test_strategies_map_onto_real_transition_styles():
    from app import renderer
    for strategy, style in vb.STRATEGY_TO_STYLE.items():
        assert style in renderer.ALL_STYLES, f"{strategy} -> unknown style {style}"
    assert vb.style_for("edges-to-center", "ltr") == "outside_in"
    assert vb.style_for("nonsense", "ltr") == "ltr"      # unknown falls back
    assert vb.style_for(None, "spiral") == "spiral"


# --- the endpoints ------------------------------------------------------------
@pytest.fixture
def client(monkeypatch):
    """The app with the layer on, a known key, and a controller that records instead
    of driving the wall."""
    from app import main

    sent = {}

    def fake_send(text, style=None, speed=None, raw=False):
        sent.update(text=text, style=style, raw=raw)
        return text

    monkeypatch.setattr(main.config, "set_vestaboard", main.config.set_vestaboard)
    main.config.set_vestaboard(True)
    monkeypatch.setattr(main, "vestaboard_key", lambda: "test-key")
    monkeypatch.setattr(main.controller, "send_text_bg", fake_send)
    monkeypatch.setattr(main.ha, "publish_state", lambda: None)
    c = TestClient(main.app)
    c.sent = sent
    try:
        yield c
    finally:
        main.config.set_vestaboard(False)


AUTH = {"X-Vestaboard-Local-Api-Key": "test-key"}


def test_post_a_matrix_takes_over_the_display(client):
    rows = ["HELLO WORLD    ", "               ", "               "]
    r = client.post("/local-api/message", json=_matrix(rows, COLS), headers=AUTH)
    # 201, not 200: the real Local API returns 201 Created, and clients treat anything
    # else as a failed write (see the compat test below).
    assert r.status_code == 201 and r.json() == {"ok": True}
    # send_text_bg is the same call a compose push makes: it cancels any running app.
    assert client.sent["text"] == "".join(rows)
    assert client.sent["raw"] is True        # or every colour chip becomes a letter


def test_post_characters_with_a_strategy(client):
    body = {"characters": _matrix(["HI"], COLS), "strategy": "edges-to-center",
            "step_interval_ms": 3000, "step_size": 2}   # timing fields accepted + ignored
    assert client.post("/local-api/message", json=body, headers=AUTH).status_code == 201
    assert client.sent["style"] == "outside_in"


def test_post_text_the_home_assistant_way(client):
    r = client.post("/local-api/message", json={"text": "hello world"}, headers=AUTH)
    assert r.status_code == 201
    assert "HELLO WORLD" in client.sent["text"]     # uppercased: there are no lowercase flaps


def test_colour_chips_survive_as_lowercase_flaps(client):
    client.post("/local-api/message", json=[[63, 64, 65, 66, 67, 68, 69]], headers=AUTH)
    assert "roygbpw" in client.sent["text"]


def test_read_back_the_live_board(client, monkeypatch):
    from app import main
    monkeypatch.setattr(main.state, "current_chars", list("HI".ljust(ROWS * COLS)))
    body = client.get("/local-api/message", headers=AUTH).json()
    # Wrapped in {"message": [[...]]}, matching the real API — a client reads it as
    # response["message"]. A bare array made every client crash on .get("message").
    matrix = body["message"]
    assert len(matrix) == ROWS and len(matrix[0]) == COLS
    assert matrix[0][:2] == [8, 9]                  # H, I


def test_it_speaks_what_a_real_vestaboard_client_expects(client, monkeypatch):
    """Reproduce, exactly, what the reference integration's client (ha-vestaboard's
    VestaboardLocalClient) does — this is what the user could not get to work.

    Its read is `json.loads(text).get("message")` and its write success test is
    `status == 201`. A bare-array read crashes on `.get`, and a 200 write is treated as a
    failure; both were true of our endpoint, so the integration never set up."""
    from app import main
    monkeypatch.setattr(main.state, "current_chars", list("HI".ljust(ROWS * COLS)))

    # read_message(): the client does exactly this
    r = client.get("/local-api/message", headers=AUTH)
    payload = r.json()
    assert isinstance(payload, dict), "read must be an object, or .get('message') crashes"
    assert isinstance(payload.get("message"), list) and payload["message"][0][:2] == [8, 9]

    # write_message(): returns `resp.status == 201`
    r = client.post("/local-api/message", json={"characters": _matrix(["HI"], COLS)}, headers=AUTH)
    assert r.status_code == 201, "a non-201 write is a failure to every Vestaboard client"

    # a bad key: `resp.status == 401 and resp.text == "Invalid API key"` drives re-auth
    r = client.get("/local-api/message", headers={"X-Vestaboard-Local-Api-Key": "wrong"})
    assert r.status_code == 401 and r.text == "Invalid API key"


def test_a_bad_code_is_422(client):
    r = client.post("/local-api/message", json=[[999]], headers=AUTH)
    assert r.status_code == 422 and "999" in r.json()["detail"]


def test_a_body_that_is_neither_matrix_nor_text_is_422(client):
    assert client.post("/local-api/message", json={"nope": 1}, headers=AUTH).status_code == 422


def test_the_key_is_required(client):
    assert client.post("/local-api/message", json=[[1]]).status_code == 401
    assert client.post("/local-api/message", json=[[1]],
                       headers={"X-Vestaboard-Local-Api-Key": "wrong"}).status_code == 401
    assert client.get("/local-api/message").status_code == 401


def test_the_whole_surface_is_absent_when_the_layer_is_off(monkeypatch):
    from app import main
    main.config.set_vestaboard(False)
    c = TestClient(main.app)
    assert c.post("/local-api/message", json=[[1]], headers=AUTH).status_code == 404
    assert c.get("/local-api/message", headers=AUTH).status_code == 404
    assert c.post("/local-api/enablement").status_code == 404


# --- enablement handshake + the dev toggle ------------------------------------
def test_enablement_returns_the_key_for_the_right_token(client, monkeypatch):
    from app import main
    # The token normally comes from COMPANION_VESTABOARD_ENABLEMENT_TOKEN, which the
    # already-constructed Config read at import; set it on the effective tree instead.
    vbcfg = main.config._effective["vestaboard"]
    monkeypatch.setitem(vbcfg, "enablement_token", "tok")

    r = client.post("/local-api/enablement",
                    headers={"X-Vestaboard-Local-Api-Enablement-Token": "tok"})
    assert r.status_code == 200
    assert r.json() == {"message": "Local API enabled", "apiKey": "test-key"}
    assert client.post("/local-api/enablement",
                       headers={"X-Vestaboard-Local-Api-Enablement-Token": "no"}).status_code == 403
    assert client.post("/local-api/enablement").status_code == 403   # no token at all


def test_enablement_is_refused_when_no_token_is_configured(client):
    assert client.post("/local-api/enablement").status_code == 403


def test_dev_toggle_flips_the_layer(monkeypatch):
    from app import main
    monkeypatch.setattr(main.config, "dev_mode", True)
    c = TestClient(main.app)
    try:
        assert c.post("/api/dev/vestaboard", json={"on": True}).json()["vestaboard"] is True
        assert c.get("/api/dev").json()["vestaboard"] is True
        assert c.get("/api/dev/vestaboard").json()["key"]          # a key exists once on
        assert c.post("/api/dev/vestaboard", json={"on": False}).json()["vestaboard"] is False
    finally:
        main.config.set_vestaboard(False)


def test_the_toggle_works_without_dev_mode():
    """The ⚙ tools menu is permanent — the Vestaboard switch is an ordinary control,
    not a developer one. (The key it hands out guards only the /local-api routes, and
    anyone who can call this endpoint already has the whole unauthenticated API.)"""
    from app import main
    c = TestClient(main.app)                     # dev_mode off (no env var in tests)
    try:
        assert c.post("/api/dev/vestaboard", json={"on": True}).json()["vestaboard"] is True
        assert c.get("/api/dev/vestaboard").json()["enabled"] is True
    finally:
        main.config.set_vestaboard(False)


def test_the_generated_key_survives_a_restart(tmp_path):
    """The settings store drops any top-level key it doesn't know (see _META_KEYS), so
    this was silently regenerating the key on every boot — which would quietly break an
    already-configured Home Assistant. It must persist."""
    from app.plugin_settings import PluginSettings

    s = PluginSettings(tmp_path)
    s.set("vestaboard_api_key", "sekrit-key")

    assert PluginSettings(tmp_path).get("vestaboard_api_key") == "sekrit-key"
