"""HTTP-level: GET /v1/teams/{id} (A4) -- F2 gate (team-scoped, not just manager-scoped)."""
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.directory import DirectoryRecord, ExternalKey, RoleGrant, TeamMembership, TeamRecord, sync
from server.storage import session_scope

from .conftest import requires_db


def _seed(s, catalog):
    sync(
        s, catalog,
        [
            DirectoryRecord(external_keys=(ExternalKey("csv", "lead1"),), email="lead1@x.com",
                            teams=(TeamMembership("platform"),),
                            roles=(RoleGrant("lead", "team", "platform"),)),
        ],
        teams=[TeamRecord("platform", "default", "Platform"), TeamRecord("growth", "default", "Growth")],
    )


@requires_db
def test_a_lead_can_view_their_own_team(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/teams/platform", params={"period": "15d"},
                      headers={"x-talium-viewer": "lead1"})
    assert resp.status_code == 200
    assert resp.json()["data"]["team"]["team_id"] == "platform"


@requires_db
def test_a_lead_cannot_view_another_team(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/teams/growth", params={"period": "15d"},
                      headers={"x-talium-viewer": "lead1"})
    assert resp.status_code == 403
