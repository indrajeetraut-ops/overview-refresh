"""P11: API smoke test against a loaded synthetic org dataset (plan §5 P11). Same DB gate as
P10 -- skipped unless `TALIUM_TEST_DSN` is set and its database name ends with `_synth`.
`small_org_dataset` comes from `tests/server/conftest.py` (shared with P10's
`test_synthetic_load.py`)."""
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from server.api.app import create_app
from server.api.config import ApiConfig
from server.storage.config import StorageConfig
from server.storage.engine import dispose_engine, get_engine, session_scope
from server.storage.synthetic_load import load_dataset

TEST_DSN = os.environ.get("TALIUM_TEST_DSN")
_synth_ready = TEST_DSN is not None and (make_url(TEST_DSN).database or "").endswith("_synth")
requires_synth_db = pytest.mark.skipif(
    not _synth_ready, reason="set TALIUM_TEST_DSN to a *_synth database to run this test")

# The small_org_dataset fixture (tests/server/conftest.py) spans 2026-01-05 .. 2026-01-19
# (start_date + 14 days); a window wide enough to cover it with margin on both sides.
_FROM = "2026-01-01T00:00:00Z"
_TO = "2026-02-01T00:00:00Z"


@pytest.fixture
def api_config() -> ApiConfig:
    return ApiConfig(webui_dist="/nonexistent/webui/dist")


@pytest.fixture
def loaded(small_org_dataset):
    """Loads the small org dataset with --replace and returns (LoadResult, ids), where ids
    are the first-of-each-kind rows the plan's endpoint walk needs, found directly in the
    just-loaded tables rather than guessed."""
    result = load_dataset(small_org_dataset, TEST_DSN, replace=True, dry_run=False)
    get_engine(StorageConfig(dsn=TEST_DSN))
    try:
        with session_scope() as s:
            team_id = s.execute(text("SELECT team_id FROM teams ORDER BY team_id LIMIT 1")).scalar_one()
            repo_id = s.execute(text(
                "SELECT repo_id FROM repositories ORDER BY repo_id LIMIT 1")).scalar_one()
            pr_id = s.execute(text(
                "SELECT artifact_id FROM git_artifacts WHERE kind = 'pr' AND state = 'merged' "
                "ORDER BY artifact_id LIMIT 1")).scalar_one()
            feature_id = s.execute(text(
                "SELECT artifact_id FROM git_artifacts WHERE kind = 'issue' "
                "ORDER BY artifact_id LIMIT 1")).scalar_one()
    finally:
        dispose_engine()
    ids = {"team_id": team_id, "repo_id": repo_id, "pr_id": pr_id, "feature_id": feature_id}
    return result, ids


@requires_synth_db
def test_admin_viewer_walks_every_manager_route(loaded, api_config):
    result, ids = loaded
    admin = result.admin_viewer_id
    assert admin is not None
    client = TestClient(create_app(api_config, StorageConfig(dsn=TEST_DSN)))
    headers = {"x-talium-viewer": admin}

    resp = client.get("/v1/org/overview", params={"from": _FROM, "to": _TO}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["headline"]["spend"]["usd_micros"] > 0
    assert len(body["data"]["teams"]) > 0

    resp = client.get("/v1/org/usage", params={"from": _FROM, "to": _TO}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["header"]["sessions"] > 0

    resp = client.get("/v1/teams", params={"from": _FROM, "to": _TO}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["data"]["items"]) > 0

    resp = client.get("/v1/projects", params={"from": _FROM, "to": _TO}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["data"]["items"]) > 0

    resp = client.get(f"/v1/teams/{ids['team_id']}", params={"from": _FROM, "to": _TO}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["team"]["team_id"] == ids["team_id"]

    resp = client.get(f"/v1/projects/{ids['repo_id']}", params={"from": _FROM, "to": _TO}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["project"]["repo_id"] == ids["repo_id"]

    resp = client.get(f"/v1/prs/{ids['pr_id']}", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["pr"]["artifact_id"] == ids["pr_id"]

    resp = client.get(f"/v1/features/{ids['feature_id']}", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["id"] == ids["feature_id"]

    resp = client.get(f"/v1/people/{result.sample_dev_viewer_id}",
                      params={"from": _FROM, "to": _TO}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["person"]["user_id"] == result.sample_dev_viewer_id

    resp = client.get("/v1/economics", params={"scope_type": "org", "scope_id": "default",
                                                "from": _FROM, "to": _TO}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["scope_type"] == "org"


@requires_synth_db
def test_one_developer_own_routes(loaded, api_config):
    result, _ids = loaded
    dev_id = result.sample_dev_viewer_id
    assert dev_id is not None
    client = TestClient(create_app(api_config, StorageConfig(dsn=TEST_DSN)))

    resp = client.get(f"/v1/users/{dev_id}/overview", params={"from": _FROM, "to": _TO})
    assert resp.status_code == 200, resp.text
    assert resp.json()["z1"]["sessions"] > 0

    resp = client.get(f"/v1/users/{dev_id}/sessions", params={"from": _FROM, "to": _TO})
    assert resp.status_code == 200, resp.text
    session_rows = resp.json()["sessions"]
    assert len(session_rows) > 0
    first_session_id = session_rows[0]["session_id"]

    resp = client.get(f"/v1/users/{dev_id}/insights", params={"from": _FROM, "to": _TO})
    assert resp.status_code == 200, resp.text

    resp = client.get(f"/v1/users/{dev_id}/repos", params={"from": _FROM, "to": _TO})
    assert resp.status_code == 200, resp.text
    repos_field = resp.json()["repos"]
    assert isinstance(repos_field, list) and len(repos_field) > 0

    resp = client.get(f"/v1/sessions/{first_session_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()


@requires_synth_db
def test_plain_dev_rejected_from_org_overview(loaded, api_config):
    result, _ids = loaded
    dev_id = result.sample_dev_viewer_id
    assert dev_id is not None
    client = TestClient(create_app(api_config, StorageConfig(dsn=TEST_DSN)))
    resp = client.get("/v1/org/overview", params={"from": _FROM, "to": _TO},
                      headers={"x-talium-viewer": dev_id})
    assert resp.status_code == 403, resp.text
