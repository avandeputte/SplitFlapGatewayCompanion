"""The OpenAPI document at its standard discovery locations.

FastAPI's native /openapi.json (+ /docs, /redoc) stays; the companion mirrors the
gateway's conventions on top — /openapi.yaml and the RFC 9727 /.well-known/api-catalog
— so the same discovery a client runs against the wall works against the companion.
"""

import yaml
from fastapi.testclient import TestClient

from app import main


def _client():
    return TestClient(main.app)


def test_openapi_json_serves_the_full_surface():
    r = _client().get("/openapi.json")
    assert r.status_code == 200
    doc = r.json()
    assert doc["info"]["title"] == "SplitFlap Gateway Companion"
    assert doc["info"]["version"] == main.__version__
    for p in ("/api/apps", "/api/message", "/api/playlists", "/api/game/input"):
        assert p in doc["paths"], p


def test_openapi_yaml_is_the_same_document():
    c = _client()
    y = c.get("/openapi.yaml")
    assert y.status_code == 200
    assert y.headers["content-type"].startswith("application/yaml")
    doc = yaml.safe_load(y.text)
    assert doc["info"] == c.get("/openapi.json").json()["info"]
    assert set(doc["paths"]) == set(c.get("/openapi.json").json()["paths"])


def test_api_catalog_points_at_both_flavors():
    r = _client().get("/.well-known/api-catalog")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/linkset+json")
    descs = r.json()["linkset"][0]["service-desc"]
    assert {d["href"] for d in descs} == {"/openapi.json", "/openapi.yaml"}


def test_docs_uis_are_reachable():
    c = _client()
    assert c.get("/docs").status_code == 200
    assert c.get("/redoc").status_code == 200


def test_operation_ids_are_unique_and_the_proxy_stays_out():
    """The /gw passthrough proxies the GATEWAY's surface — it must not appear in the
    companion's own document (it also used to emit duplicate operation ids)."""
    doc = _client().get("/openapi.json").json()
    assert not any(p == "/gw" or p.startswith("/gw/") for p in doc["paths"])
    seen = set()
    for p, methods in doc["paths"].items():
        for m, op in methods.items():
            oid = op.get("operationId")
            if oid:
                assert oid not in seen, f"duplicate operationId {oid} at {m.upper()} {p}"
                seen.add(oid)
