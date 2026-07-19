"""The companion's own /api/panel/* controls for a Matrix wall — the overlay ticker,
transitions, the animation/font libraries and the boot splash. Thin, gated proxies to the
gateway; these pin the gating (a flap wall never sees them) and that each route proxies to
the right gateway endpoint.
"""

import pytest
from fastapi.testclient import TestClient

from app import device

DOC_2_1 = {
    "product": "Matrix Portal Gateway", "fw": "2.1.0", "api": "3.1.0",
    "features": ["cells", "colors", "canvas", "effects", "ticker"],
    "charset": {"uniform": True, "common": "ABC"},
    "canvas": {"formats": ["rgb888", "rgb565", "qoi"], "width": 128, "height": 64,
               "rect": True, "anim": True, "ticker": True, "readback": True,
               "ops": ["clear", "rect", "text", "sprite", "show"]},
    "effects": ["plasma", "fire"], "effectParams": ["hue", "density"], "motion": {"kind": "drawn"},
}


@pytest.fixture
def canvas_wall(monkeypatch):
    """The default display made into a 2.1 canvas wall, with the gateway stubbed. Returns
    ``(client, calls)`` where ``calls`` records the gateway requests the routes proxied."""
    from app import gateway, main
    monkeypatch.setattr(main.controller.transport, "caps",
                        device.from_capabilities(DOC_2_1), raising=False)
    main.config.update({"transport": {"gateway_url": "http://gw"}})

    calls = []

    class Resp:
        status_code = 200

        def json(self):
            return {"ok": True, "frames": 42, "fps": 12}

    monkeypatch.setattr(gateway, "_request",
                        lambda m, u, p, *, timeout, **kw: (calls.append((m, p, kw.get("json"))) or Resp()))

    async def _cfg(url, timeout=5.0):
        return {"bootAnim": "rainbow"}

    monkeypatch.setattr(gateway, "fetch_gateway_config", _cfg)
    return TestClient(main.app), calls


def test_caps_reports_the_2_1_surface(canvas_wall):
    client, _ = canvas_wall
    j = client.get("/api/panel/caps").json()
    assert j["width"] == 128 and j["fw"] == "2.1"
    assert j["readback"] and j["overlay"] and j["transition"] and j["gif"] and j["sprite"]


def test_overlay_proxies_with_overlay_true(canvas_wall):
    client, calls = canvas_wall
    r = client.post("/api/panel/overlay", json={"text": "NEWS", "color": [255, 0, 0], "speed": 3})
    assert r.status_code == 200 and r.json()["active"] is True
    sent = [b for m, p, b in calls if p == "/api/canvas/ticker"]
    assert sent and sent[0]["overlay"] is True and sent[0]["text"] == "NEWS"


def test_overlay_empty_text_clears(canvas_wall):
    client, calls = canvas_wall
    assert client.post("/api/panel/overlay", json={"text": ""}).json()["active"] is False


def test_transition_proxies(canvas_wall):
    client, calls = canvas_wall
    client.post("/api/panel/transition", json={"type": "wipe", "ms": 300})
    assert any(p == "/api/canvas/transition" and b["type"] == "wipe" for m, p, b in calls)


def test_boot_splash_sets_config(canvas_wall):
    client, calls = canvas_wall
    r = client.post("/api/panel/boot", json={"name": "rainbow"})
    assert r.status_code == 200 and r.json()["boot"] == "rainbow"
    assert any(p == "/api/config/settings" and b["bootAnim"] == "rainbow" for m, p, b in calls)


def test_library_reports_boot(canvas_wall):
    client, _ = canvas_wall
    assert client.get("/api/panel/library").json()["boot"] == "rainbow"


def test_gif_upload_proxies(canvas_wall):
    client, calls = canvas_wall
    r = client.put("/api/panel/gif", content=b"GIF89a....")
    assert r.status_code == 200 and r.json()["frames"] == 42
    assert any(p == "/api/canvas/gif" for m, p, b in calls)


def test_a_flap_wall_never_sees_the_panel_routes(monkeypatch):
    from app import main
    monkeypatch.setattr(main.controller.transport, "caps", device.SPLIT_FLAP, raising=False)
    client = TestClient(main.app)
    assert client.get("/api/panel/caps").status_code == 404
    assert client.post("/api/panel/overlay", json={"text": "x"}).status_code == 404
    assert client.post("/api/panel/transition", json={"type": "wipe"}).status_code == 404
