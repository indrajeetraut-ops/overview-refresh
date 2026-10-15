"""HTTP-level: GET /v1/teams + /v1/projects (A3) -- F2 gate + response_model validation."""
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.directory import DirectoryRecord, ExternalKey, RoleGrant, sync
from server.storage import session_scope

from .conftest import requires_db


@requires_db
def test_teams_table_requires_a_manager(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        sync(s, catalog, [DirectoryRecord(external_keys=(ExternalKey("csv", "dev1"),), email="d@x.com")])
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/teams", params={"period": "15d"}, headers={"x-talium-viewer": "dev1"})
    assert resp.status_code == 403


@requires_db
def test_teams_and_projects_tables_validate_against_the_envelope(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        sync(s, catalog, [DirectoryRecord(external_keys=(ExternalKey("csv", "admin1"),), email="a@x.com",
                                          roles=(RoleGrant("admin", "org", "default"),))])
    client = TestClient(create_app(api_config, storage_config))

    teams_resp = client.get("/v1/teams", params={"period": "15d"}, headers={"x-talium-viewer": "admin1"})
    assert teams_resp.status_code == 200
    assert teams_resp.json()["data"] == {"items": [], "total": 0}

    projects_resp = client.get("/v1/projects", params={"period": "15d", "sort": "spend"},
                               headers={"x-talium-viewer": "admin1"})
    assert projects_resp.status_code == 200
    assert projects_resp.json()["data"] == {"items": [], "total": 0}


@requires_db
def test_teams_table_rejects_an_unknown_sort_key_over_http(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        sync(s, catalog, [DirectoryRecord(external_keys=(ExternalKey("csv", "admin1"),), email="a@x.com",
                                          roles=(RoleGrant("admin", "org", "default"),))])
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/teams", params={"period": "15d", "sort": "bogus"},
                      headers={"x-talium-viewer": "admin1"})
    assert resp.status_code == 400
