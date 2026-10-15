"""HTTP-level: GET /v1/features/{id} (D3) -- F2 gate (repo-scoped), same posture as D2's PR
route: `resolve_feature_visibility_repo_id` bridges the feature's git-source repo back to the
session-side scheme `Viewer.visible_repos` holds."""
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
REPO = "github:acme/api"
REPO_NUM = 222
GIT_REPO = f"github:{REPO_NUM}"
ISSUE_ID = f"github:{REPO_NUM}:issue:7"


def _seed(s, catalog):
    sync(
        s, catalog,
        [DirectoryRecord(external_keys=(ExternalKey("csv", "lead1"),), email="lead1@x.com",
                         teams=(TeamMembership("platform"),),
                         roles=(RoleGrant("lead", "team", "platform"),))],
        teams=[TeamRecord("platform", "default", "Platform")],
    )
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
    s.add(GitArtifact(artifact_id=ISSUE_ID, kind="issue", repo_id=GIT_REPO, org_id="default",
                      number=7, is_bot=False, title="Ship the thing", state="open",
                      created_at=MID, updated_at=MID))
    s.flush()


@requires_db
def test_lead_can_view_a_feature_on_a_repo_their_team_touches(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get(f"/v1/features/{ISSUE_ID}", headers={"x-talium-viewer": "lead1"})
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == ISSUE_ID


@requires_db
def test_lead_cannot_view_a_feature_on_an_unrelated_repo(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
        s.add(GitRepo(repo_id="github:999", repo_num=999, full_name="other/repo", owner="other",
                     default_branch="main", private=False, installation_id=1, org_id="default",
                     backfill_state="done", first_seen_at=MID, updated_at=MID))
        s.add(GitArtifact(artifact_id="github:999:issue:1", kind="issue", repo_id="github:999",
                          org_id="default", number=1, is_bot=False, title="Other work",
                          state="open", created_at=MID, updated_at=MID))
        s.flush()
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/features/github:999:issue:1", headers={"x-talium-viewer": "lead1"})
    assert resp.status_code == 403


@requires_db
def test_unknown_feature_404s(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/features/github:1:issue:99999", headers={"x-talium-viewer": "lead1"})
    assert resp.status_code == 404


@requires_db
def test_branch_clustered_feature_id_with_a_slash_is_routable(api_config, storage_config, engine, catalog):
    """H5 branch-clustered feature ids embed a real branch name (`branch:<repo_num>:<branch>`),
    which commonly contains a `/` (e.g. `feat/foo`) -- the route must not truncate at it."""
    with session_scope() as s:
        _seed(s, catalog)
        s.add(GitArtifact(artifact_id=f"{GIT_REPO}:pr:1", kind="pr", repo_id=GIT_REPO,
                          org_id="default", number=1, is_bot=False, title="Add the widget",
                          state="merged", branch="feat/widget", created_at=MID, updated_at=MID))
        s.flush()
    client = TestClient(create_app(api_config, storage_config))
    feature_id = f"branch:{REPO_NUM}:feat/widget"
    resp = client.get(f"/v1/features/{feature_id}", headers={"x-talium-viewer": "lead1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["id"] == feature_id
