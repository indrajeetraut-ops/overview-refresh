"""HTTP-level: GET /v1/economics (E1) -- F2 gate via the same `_gate_scope` helper the
budgets routes already use."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from server.api.app import create_app
from server.directory import DirectoryRecord, ExternalKey, RoleGrant, TeamMembership, TeamRecord, sync
from server.storage import session_scope

from .conftest import requires_db

MID = datetime(2026, 1, 15, tzinfo=timezone.utc)


def _seed(s, catalog):
    sync(
        s, catalog,
        [DirectoryRecord(external_keys=(ExternalKey("csv", "lead1"),), email="lead1@x.com",
                         teams=(TeamMembership("platform"),),
                         roles=(RoleGrant("lead", "team", "platform"),))],
        teams=[TeamRecord("platform", "default", "Platform")],
    )


@requires_db
def test_lead_can_view_economics_for_their_own_team(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/economics", params={
        "scope_type": "team", "scope_id": "platform", "period": "30d"},
        headers={"x-talium-viewer": "lead1"})
    assert resp.status_code == 200
    assert resp.json()["data"]["scope_id"] == "platform"


@requires_db
def test_lead_cannot_view_economics_for_an_unrelated_team(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
        sync(s, catalog, [], teams=[TeamRecord("other-team", "default", "Other")])
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/economics", params={
        "scope_type": "team", "scope_id": "other-team", "period": "30d"},
        headers={"x-talium-viewer": "lead1"})
    assert resp.status_code == 403


@requires_db
def test_dev_cannot_view_org_economics(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        sync(s, catalog, [
            DirectoryRecord(external_keys=(ExternalKey("csv", "dev1"),), email="dev1@x.com",
                            teams=(TeamMembership("platform"),)),
        ], teams=[TeamRecord("platform", "default", "Platform")])
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/economics", params={
        "scope_type": "org", "scope_id": "default", "period": "30d"},
        headers={"x-talium-viewer": "dev1"})
    assert resp.status_code == 403


@requires_db
def test_unknown_scope_type_is_a_bad_request(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/economics", params={
        "scope_type": "bogus", "scope_id": "x", "period": "30d"},
        headers={"x-talium-viewer": "lead1"})
    assert resp.status_code == 400
