"""If the gateway 404s the cells endpoint, believe the gateway.

A physical Split-Flap Gateway advertises the `index` feature — which is
POST /api/flap/index {"id":5,"index":3}, "turn ONE module to a flap by number". The companion
read that as the bulk index-addressed page API (POST /api/display/cells), which only a Matrix
Portal has, and posted every page to an endpoint that was not there. 404 on every write. The
wall went dark and the UI said "offline" while the gateway sat answering /api/status,
/api/config and its whole proxied web UI perfectly.

device.py no longer misreads the feature list. But a wall in someone's hallway should not go
dark because a capability document and an endpoint disagree, so the transport now believes the
ENDPOINT: a 404 from /api/display/cells means the gateway does not have it, whatever it said,
and the page goes out on the legacy wire instead.

A 500 is NOT the same thing and must not downgrade: the endpoint exists and something behind it
failed, and silently changing wire format would hide it.
"""

from __future__ import annotations

import httpx
import pytest

from app import device
from app.transport.rest import RestTransport

MATRIX_CAPS = {
    "features": ["cells", "colors", "index", "lowercase", "pictographs"],
    "charset": {"uniform": True, "common": " ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
}


def _transport(handler) -> RestTransport:
    t = RestTransport("http://gw.invalid")
    t._client = httpx.AsyncClient(base_url="http://gw.invalid",
                                  transport=httpx.MockTransport(handler))
    t.caps = device.from_capabilities(MATRIX_CAPS)
    assert t.caps.indexed, "the fixture must start out believing it has the cells API"
    return t


@pytest.mark.asyncio
async def test_a_404_from_cells_falls_back_to_the_legacy_wire(caplog):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/display/cells":
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json={"ok": True})

    t = _transport(handler)
    await t.send_batch([(0, "A"), (1, "B")], step_ms=10)

    assert "/api/display/cells" in seen, "it tried the advertised endpoint first"
    assert "/api/rs485/batch" in seen, "and the page still reached the wall"
    assert t.caps.indexed is False, "downgraded for the life of this transport"
    assert t.connected, "a wall that took the page is not offline"


@pytest.mark.asyncio
async def test_it_does_not_keep_retrying_the_missing_endpoint():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/display/cells":
            return httpx.Response(404)
        return httpx.Response(200, json={"ok": True})

    t = _transport(handler)
    await t.send_batch([(0, "A")], step_ms=10)
    await t.send_batch([(0, "B")], step_ms=10)

    assert seen.count("/api/display/cells") == 1, "asked once, told once, remembered"
    assert seen.count("/api/rs485/batch") == 2


@pytest.mark.asyncio
async def test_a_500_is_a_real_fault_and_is_not_downgraded():
    """The endpoint EXISTS and something behind it broke. Changing wire format would hide it."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    t = _transport(handler)
    with pytest.raises(Exception):
        await t.send_batch([(0, "A")], step_ms=10)

    assert t.caps.indexed is True, "still a Matrix Portal; the fault is not a missing endpoint"
    assert not t.connected
