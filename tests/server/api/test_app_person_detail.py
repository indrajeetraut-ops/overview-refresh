"""HTTP-level: GET /v1/people/{id} (A5) -- F2 gate (lead-scoped to the person's own team)."""
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
            DirectoryRecord(external_keys=(ExternalKey("csv", "dev1"),), email="dev1@x.com",
                            teams=(TeamMembership("platform"),)),
            DirectoryRecord(external_keys=(ExternalKey("csv", "dev2"),), email="dev2@x.com",
                            teams=(TeamMembership("growth"),)),
        ],
        teams=[TeamRecord("platform", "default", "Platform"), TeamRecord("growth", "default", "Growth")],
    )


@requires_db
def test_lead_can_view_a_person_on_their_own_team(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/people/csv:dev1", params={"period": "15d"},
                      headers={"x-talium-viewer": "lead1"})
    assert resp.status_code == 200
    assert resp.json()["data"]["person"]["user_id"] == "csv:dev1"


@requires_db
def test_lead_cannot_view_a_person_on_another_team(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/people/csv:dev2", params={"period": "15d"},
                      headers={"x-talium-viewer": "lead1"})
    assert resp.status_code == 403


@requires_db
def test_unknown_person_is_404(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/people/ghost", params={"period": "15d"},
                      headers={"x-talium-viewer": "lead1"})
    assert resp.status_code == 404
