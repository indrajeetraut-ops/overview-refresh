"""HTTP-level: GET /v1/prs/{id} (D2) -- F2 gate (repo-scoped), same posture as A6's project
route. The gate itself needs `resolve_pr_visibility_repo_id` to bridge the PR's git-source
`repo_id` back to the session-side scheme `Viewer.visible_repos` is populated in -- this is
the one thing `test_manager_service_pr_detail.py` can't cover (it calls `pr_detail` directly,
below the gate)."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from server.api.app import create_app
from server.directory import DirectoryRecord, ExternalKey, RoleGrant, TeamMembership, TeamRecord, sync
from server.storage import session_scope
from server.storage.schema import GitArtifact, GitRepo
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo

from .conftest import requires_db

MID = datetime(2026, 1, 15, tzinfo=timezone.utc)
REPO = "github:acme/api"        # session-side (git_ident) identity, what visible_repos holds
REPO_NUM = 888
GIT_REPO = f"github:{REPO_NUM}"  # git-source identity, what the PR's own repo_id carries
PR_ID = f"github:{REPO_NUM}:pr:12"


def _seed(s, catalog):
    sync(
        s, catalog,
        [
            DirectoryRecord(external_keys=(ExternalKey("csv", "lead1"),), email="lead1@x.com",
                            teams=(TeamMembership("platform"),),
                            roles=(RoleGrant("lead", "team", "platform"),)),
        ],
        teams=[TeamRecord("platform", "default", "Platform")],
    )
    # A session that ties "platform" to REPO (the session-side scheme), the same way A6's own
    # test derives visible_repos -- and to GIT_REPO/PR_ID via the git_repos bridge.
    s.add(SessionRow(session_id="s1", producer_id="p", org_id="default", user_id="csv:lead1",
                     agent="claude_code", status="ended", started_at=MID, last_event_at=MID,
                     ended_at=MID, signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfo(session_id="s1", created_at=MID, updated_at=MID, models={}, tools={},
                      files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
                      long_turn_count=0, info={}, otel={}, datasource="both",
                      repo_id=REPO, repo_id_provenance="remote"))
    s.add(GitRepo(repo_id=GIT_REPO, repo_num=REPO_NUM, full_name="acme/api", owner="acme",
                 default_branch="main", private=False, installation_id=1, org_id="default",
                 backfill_state="done", first_seen_at=MID, updated_at=MID))
    s.add(GitArtifact(artifact_id=PR_ID, kind="pr", repo_id=GIT_REPO, org_id="default",
                      number=12, user_id="csv:lead1", is_bot=False, state="merged",
                      created_at=MID, updated_at=MID, merged_at=MID,
                      stats={"additions": 10, "deletions": 2}))
    s.flush()


@requires_db
def test_lead_can_view_a_pr_on_a_repo_their_team_touches(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get(f"/v1/prs/{PR_ID}", headers={"x-talium-viewer": "lead1"})
    assert resp.status_code == 200
    assert resp.json()["data"]["pr"]["artifact_id"] == PR_ID


@requires_db
def test_lead_cannot_view_a_pr_on_an_unrelated_repo(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
        s.add(GitRepo(repo_id="github:999", repo_num=999, full_name="other/repo", owner="other",
                     default_branch="main", private=False, installation_id=1, org_id="default",
                     backfill_state="done", first_seen_at=MID, updated_at=MID))
        s.add(GitArtifact(artifact_id="github:999:pr:1", kind="pr", repo_id="github:999",
                          org_id="default", number=1, user_id="csv:other", is_bot=False,
                          state="merged", created_at=MID, updated_at=MID, merged_at=MID,
                          stats={}))
        s.flush()
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/prs/github:999:pr:1", headers={"x-talium-viewer": "lead1"})
    assert resp.status_code == 403


@requires_db
def test_unknown_pr_404s(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/prs/github:1:pr:99999", headers={"x-talium-viewer": "lead1"})
    assert resp.status_code == 404


@requires_db
def test_no_viewer_header_is_unauthorized(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get(f"/v1/prs/{PR_ID}")
    assert resp.status_code == 401
