"""HTTP-level: /v1/budgets/{scope_type}/{scope_id} -- F1 write surface + F2 gate."""
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.directory import DirectoryRecord, ExternalKey, RoleGrant, TeamMembership, TeamRecord, sync
from server.storage import session_scope

from .conftest import requires_db


def _seed(s, catalog):
    sync(
        s, catalog,
        [
            DirectoryRecord(external_keys=(ExternalKey("csv", "dev1"),), email="dev1@x.com",
                            teams=(TeamMembership("platform"),)),
            DirectoryRecord(external_keys=(ExternalKey("csv", "admin1"),), email="admin1@x.com",
                            roles=(RoleGrant("admin", "org", "default"),)),
        ],
        teams=[TeamRecord("platform", "default", "Platform")],
    )


@requires_db
def test_get_budget_without_a_viewer_header_is_401(api_config, storage_config, engine, catalog):
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/budgets/org/default")
    assert resp.status_code == 401


@requires_db
def test_put_budget_as_dev_is_403(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.put("/v1/budgets/org/default", json={"amount_usd": "15000"},
                      headers={"x-talium-viewer": "dev1"})
    assert resp.status_code == 403


@requires_db
def test_put_then_get_budget_as_admin_round_trips(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    put_resp = client.put("/v1/budgets/team/platform", json={"amount_usd": "4000"},
                          headers={"x-talium-viewer": "admin1"})
    assert put_resp.status_code == 200
    assert put_resp.json()["amount_usd"] == "4000.00"

    get_resp = client.get("/v1/budgets/team/platform", headers={"x-talium-viewer": "admin1"})
    assert get_resp.status_code == 200
    assert get_resp.json()["amount_usd"] == "4000.00"


@requires_db
def test_get_budget_on_an_unknown_team_is_403_from_the_gate(api_config, storage_config, engine, catalog):
    """The F2 gate runs before the F1 scope-existence check: a team (or repo) that doesn't
    exist is never in ANY viewer's `visible_teams`/`visible_repos` (both are derived from the
    real roster/session data), so this reads as "not visible", not "not found" -- even for an
    admin. `_validate_scope`'s own 404 for an unknown scope has no HTTP path that reaches it
    (org scope never checks existence either) -- it fires for a caller that skips the gate,
    e.g. `cli_budgets.py` (`test_manager_budgets.py` covers it directly)."""
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/budgets/team/ghost-team", headers={"x-talium-viewer": "admin1"})
    assert resp.status_code == 403


@requires_db
def test_get_budget_on_an_unknown_repo_is_403_from_the_gate(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/budgets/repo/ghost-repo", headers={"x-talium-viewer": "admin1"})
    assert resp.status_code == 403
