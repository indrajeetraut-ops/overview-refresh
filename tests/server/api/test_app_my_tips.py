"""HTTP-level: GET /v1/users/{id}/tips (A7) -- ungated, same posture as the other shipped
single-user routes (ground rules section 2)."""
from fastapi.testclient import TestClient

from server.api.app import create_app

from .conftest import requires_db


@requires_db
def test_my_tips_route_empty_range(api_config, storage_config, engine, catalog):
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/users/u1/tips", params={
        "from": "2026-01-01T00:00:00Z", "to": "2026-01-02T00:00:00Z"})
    assert resp.status_code == 200
    assert resp.json() == {
        "data": {"tips": []},
        "mode": {"modes_present": [], "sessions_by_mode": {}},
        "unavailable": None,
    }


@requires_db
def test_my_tips_route_accepts_a_period(api_config, storage_config, engine, catalog):
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/users/u1/tips", params={"period": "30d"})
    assert resp.status_code == 200
