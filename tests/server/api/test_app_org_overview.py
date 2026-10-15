"""HTTP-level: GET /v1/org/overview (A1) -- F2 gate + response_model validation."""
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.directory import DirectoryRecord, ExternalKey, RoleGrant, sync
from server.storage import session_scope

from .conftest import requires_db


def _seed_admin(s, catalog):
    sync(s, catalog, [
        DirectoryRecord(external_keys=(ExternalKey("csv", "dev1"),), email="dev1@x.com"),
        DirectoryRecord(external_keys=(ExternalKey("csv", "admin1"),), email="admin1@x.com",
                        roles=(RoleGrant("admin", "org", "default"),)),
    ])


@requires_db
def test_org_overview_requires_a_viewer_header(api_config, storage_config, engine, catalog):
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/org/overview", params={"period": "15d"})
    assert resp.status_code == 401


@requires_db
def test_org_overview_rejects_a_plain_dev(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed_admin(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/org/overview", params={"period": "15d"},
                      headers={"x-talium-viewer": "dev1"})
    assert resp.status_code == 403


@requires_db
def test_org_overview_as_admin_validates_against_the_envelope(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed_admin(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/org/overview", params={"period": "15d"},
                      headers={"x-talium-viewer": "admin1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["teams"] == []
    assert "modes_present" in body["mode"]
    # daily_spend: the window's elapsed UTC days, zero-filled, days as ISO dates on the wire
    daily = body["data"]["daily_spend"]
    assert 15 <= len(daily) <= 16 and all(p["spend_usd_micros"] == 0 for p in daily)
    assert daily[0]["day"] < daily[-1]["day"] and len(daily[0]["day"]) == 10
    assert "monthly_spend" not in body["data"] and "initiatives_trend" not in body["data"]
    assert body["data"]["agent_task_mix"] == []


@requires_db
def test_org_overview_rejects_an_unknown_period(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed_admin(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/org/overview", params={"period": "9d"},
                      headers={"x-talium-viewer": "admin1"})
    assert resp.status_code == 400


@requires_db
def test_org_overview_rejects_no_timeframe_at_all(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed_admin(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/org/overview", headers={"x-talium-viewer": "admin1"})
    assert resp.status_code == 400
