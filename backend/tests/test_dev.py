"""Developer mode: env-gated sim transport + grid override + resync."""

from fastapi.testclient import TestClient

from app.config import Config


def test_dev_mode_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("COMPANION_DEV_MODE", "1")
    assert Config(data_dir=tmp_path).dev_mode is True
    monkeypatch.delenv("COMPANION_DEV_MODE")
    assert Config(data_dir=tmp_path).dev_mode is False


def test_grid_override_only_applies_in_sim(tmp_path):
    c = Config(data_dir=tmp_path)
    assert c.grid["rows"] == 3 and c.grid["cols"] == 15
    # override without sim is ignored (real geometry is never touched)
    c.set_grid_override(6, 22)
    assert c.grid["rows"] == 3 and c.grid["cols"] == 15
    # in sim, the override applies
    c.set_sim_mode(True)
    c.set_grid_override(6, 22)
    assert c.grid == {"rows": 6, "cols": 22, "module_id_base": 0}
    assert c.dev_state()["grid_overridden"] is True
    # leaving sim reverts to the real geometry and clears the override
    c.set_sim_mode(False)
    assert c.grid["rows"] == 3 and c.grid["cols"] == 15
    assert c.dev_state()["grid_overridden"] is False


def test_only_simulation_is_dev_gated():
    """The ⚙ tools menu is permanent; COMPANION_DEV_MODE gates exactly one thing in it —
    simulation mode (and the grid override, which belongs to simulation)."""
    from app import main
    client = TestClient(main.app)
    assert main.config.dev_mode is False
    assert client.get("/api/dev").json()["enabled"] is False   # GET is always safe
    assert client.post("/api/dev/sim", json={"on": True}).status_code == 404
    assert client.post("/api/dev/grid", json={"rows": 5, "cols": 5}).status_code == 404
    # Everything else in the menu works without dev mode.
    assert client.post("/api/dev/resync").status_code == 200


def test_dev_endpoints_flow(monkeypatch):
    from app import main
    monkeypatch.setattr(main.config, "dev_mode", True)
    client = TestClient(main.app)
    try:
        assert client.get("/api/dev").json()["enabled"] is True
        # sim on -> the display transport becomes the no-op sim
        assert client.post("/api/dev/sim", json={"on": True}).json()["sim_mode"] is True
        # override the geometry while simulating -> the runtime sees it
        g = client.post("/api/dev/grid", json={"rows": 5, "cols": 20}).json()
        assert g["grid"]["rows"] == 5 and g["grid"]["cols"] == 20
        assert main.plugins.get_rows() == 5 and main.plugins.get_cols() == 20
        # sim off -> geometry reverts, override cleared
        off = client.post("/api/dev/sim", json={"on": False}).json()
        assert off["sim_mode"] is False and off["grid"]["rows"] == 3
        assert main.plugins.get_cols() == 15
        # can't override the grid unless simulating
        assert client.post("/api/dev/grid", json={"rows": 5, "cols": 5}).status_code == 400
    finally:
        main.config.set_sim_mode(False)


def test_debug_log_toggle_download_and_clear(tmp_path):
    """The wire log switch: off by default, downloadable while on, and clearable — all
    without dev mode (it's an ordinary Tools control, like resync)."""
    from app import debuglog, main
    debuglog.configure(tmp_path / "wire.log", False)   # pin a hermetic path for this test
    client = TestClient(main.app)
    try:
        assert client.get("/api/dev").json()["debug_log"] is False
        assert client.get("/api/dev/debug-log").json()["enabled"] is False

        # turn it on -> the switch and the module agree
        st = client.post("/api/dev/debug-log", json={"on": True}).json()
        assert st["debug_log"] is True and debuglog.is_enabled() is True

        debuglog.gw_send("POST", "http://gw", "/api/rs485/batch", b"hello")
        r = client.get("/api/dev/debug-log/download")
        assert r.status_code == 200
        assert 'filename="companion-wire.log"' in r.headers["content-disposition"]
        assert "GW POST gw/api/rs485/batch" in r.text and "hello" in r.text

        # clear empties it, download still works (returns the placeholder/marker)
        assert client.post("/api/dev/debug-log/clear").json()["enabled"] is True
        assert "hello" not in client.get("/api/dev/debug-log/download").text

        # turn it off
        assert client.post("/api/dev/debug-log", json={"on": False}).json()["debug_log"] is False
        assert debuglog.is_enabled() is False
    finally:
        debuglog.configure(tmp_path / "wire.log", False)
        main.config.set_debug_log(False)
