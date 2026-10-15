"""HTTP-level: GET /v1/projects/{id} (A6) -- F2 gate (repo-scoped)."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from server.api.app import create_app
from server.directory import DirectoryRecord, ExternalKey, RoleGrant, TeamMembership, TeamRecord, sync
from server.storage import session_scope
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo

from .conftest import requires_db

MID = datetime(2026, 1, 15, tzinfo=timezone.utc)
REPO = "github:acme/api"


def _seed(s, catalog):
    sync(
        s, catalog,
        [
            DirectoryRecord(external_keys=(ExternalKey("csv", "lead1"),), email="lead1@x.com",
                            teams=(TeamMembership("platform"),),
                            roles=(RoleGrant("lead", "team", "platform"),)),
            DirectoryRecord(external_keys=(ExternalKey("csv", "dev1"),), email="dev1@x.com",
                            teams=(TeamMembership("platform"),)),
        ],
        teams=[TeamRecord("platform", "default", "Platform")],
    )
    s.add(SessionRow(session_id="s1", producer_id="p", org_id="default", user_id="csv:dev1",
                     agent="claude_code", status="ended", started_at=MID, last_event_at=MID,
                     ended_at=MID, signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfo(session_id="s1", created_at=MID, updated_at=MID, models={}, tools={},
                      files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
                      long_turn_count=0, info={}, otel={}, datasource="both",
                      repo_id=REPO, repo_id_provenance="remote"))


@requires_db
def test_lead_can_view_a_repo_their_team_touches(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get(f"/v1/projects/{REPO}", params={"period": "30d"},
                      headers={"x-talium-viewer": "lead1"})
    assert resp.status_code == 200
    assert resp.json()["data"]["project"]["repo_id"] == REPO


@requires_db
def test_lead_cannot_view_an_unrelated_repo(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/projects/github:other/repo", params={"period": "30d"},
                      headers={"x-talium-viewer": "lead1"})
    assert resp.status_code == 403
