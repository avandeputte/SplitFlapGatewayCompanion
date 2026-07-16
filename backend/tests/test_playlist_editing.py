"""Editing a saved playlist should not mean retyping its name.

The editor used to be an anonymous scratch buffer. "Load" copied a playlist's entries in
and forgot where they came from, so "Save" had no choice but to ask — and you had to
reproduce the name EXACTLY. Get a character wrong and you did not update the playlist, you
silently made a second one next to it.

So the editor now has an identity (PL_NAME), and the name field is it.
"""
from pathlib import Path

APP_JS = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text("utf-8")
INDEX = (Path(__file__).resolve().parents[1] / "app" / "static" / "index.html").read_text("utf-8")


def _fn(name):
    body = APP_JS[APP_JS.index(f"function {name}("):]
    return body[:body.index("\n}") + 2]


def test_the_editor_knows_which_playlist_it_is_editing():
    assert "let PL_NAME" in APP_JS
    assert 'id="plName"' in INDEX


def test_saving_an_edit_does_not_prompt_for_the_name():
    """The whole complaint. Save writes to the name field — no prompt, no retyping."""
    body = _fn("savePlaylist")
    assert "prompt(" not in body, "saving still asks for the name"
    assert 'const name = $("plName").value.trim();' in body
    assert '"/api/playlists"' in body


def test_editing_loads_the_name_as_well_as_the_entries():
    body = _fn("plEdit")
    assert "PL_NAME = name;" in body
    assert '$("plName").value = name;' in body
    assert "PL_ENTRIES =" in body


def test_a_rename_does_not_leave_the_old_one_behind():
    """Save under a new name renames: the playlist you renamed away from must not linger
    as a stale duplicate of the one you just edited."""
    body = _fn("savePlaylist")
    assert "PL_NAME !== name" in body
    # the raw fetch became the shared del() helper (which is method: DELETE)
    assert 'await del("/api/playlists/"' in body


def test_a_rename_says_that_is_what_it_is():
    """Nobody should rename by accident while reaching for a copy."""
    body = _fn("plSaveLabel")
    assert 't("Rename & save")' in body
    assert 't("Save")' in body


def test_you_can_start_a_new_playlist_without_reloading():
    body = _fn("plNew")
    assert 'PL_NAME = "";' in body
    assert "PL_ENTRIES = [];" in body
    assert 'id="plNew"' in INDEX


def test_the_playlist_being_edited_is_marked_in_the_list():
    assert 'n === PL_NAME ? " editing" : ""' in APP_JS


def test_saving_is_refused_when_there_is_nothing_to_save():
    body = _fn("plSaveLabel")
    assert "btn.disabled = !typed || !PL_ENTRIES.length;" in body


def test_running_the_editor_reports_the_playlist_by_name():
    """It used to report "(unsaved)" even when you were editing a saved playlist, so the
    gateway and Home Assistant showed the wrong thing."""
    body = _fn("runPlaylistNow")
    assert 'PL_NAME || "(unsaved)"' in body
