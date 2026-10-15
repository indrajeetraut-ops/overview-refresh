"""HTTP-level: GET /v1/org/usage (A2) -- F2 gate + response_model validation."""
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.directory import DirectoryRecord, ExternalKey, RoleGrant, sync
from server.storage import session_scope

from .conftest import requires_db


@requires_db
def test_org_usage_requires_a_manager(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        sync(s, catalog, [DirectoryRecord(external_keys=(ExternalKey("csv", "dev1"),), email="d@x.com")])
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/org/usage", params={"period": "15d"}, headers={"x-talium-viewer": "dev1"})
    assert resp.status_code == 403


@requires_db
def test_org_usage_as_admin_validates_against_the_envelope(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        sync(s, catalog, [DirectoryRecord(external_keys=(ExternalKey("csv", "admin1"),),
                                          email="a@x.com", roles=(RoleGrant("admin", "org", "default"),))])
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/org/usage", params={"period": "30d"}, headers={"x-talium-viewer": "admin1"})
    assert resp.status_code == 200
    assert resp.json()["data"]["teams"] == []
