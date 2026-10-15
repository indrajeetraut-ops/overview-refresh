"""A6 GET /v1/projects/{id} -- service-level (`server/api/manager/service.py::project_detail`).

`repo_id` here is the session-side identity (git_ident's `github:owner/name`); PR/issue data
lives under git_artifacts' own numeric scheme, so the fixture seeds a `git_repos` bridge row
(`aggregate.resolve_git_repo_id`) the same way a real GitHub App installation would.
"""
from datetime import datetime, timezone

from server.api.config import ApiConfig
from server.api.manager.service import project_detail
from server.directory import DirectoryRecord, ExternalKey, TeamMembership, TeamRecord, sync
from server.storage import session_scope
from server.storage.schema import GitArtifact, GitArtifactEvent, GitRepo, PrIssueLink, SessionGitLink
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo

from .conftest import requires_db

FRM = datetime(2026, 1, 1, tzinfo=timezone.utc)
TO = datetime(2026, 1, 31, tzinfo=timezone.utc)
MID = datetime(2026, 1, 15, tzinfo=timezone.utc)
REPO = "github:acme/api"     # session-side (git_ident) identity -- what A3/F3 hand callers
REPO_NUM = 555
GIT_REPO = f"github:{REPO_NUM}"  # git-source's own identity -- what git_artifacts is keyed on


def _seed(s, catalog):
    sync(s, catalog, [
        DirectoryRecord(external_keys=(ExternalKey("csv", "dev1"),), email="dev1@x.com",
                        display_name="Dev One", teams=(TeamMembership("platform"),)),
    ], teams=[TeamRecord("platform", "default", "Platform")])
    s.add(SessionRow(session_id="s1", producer_id="p", org_id="default", user_id="csv:dev1",
                     agent="claude_code", status="ended", started_at=MID, last_event_at=MID,
                     ended_at=MID, signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfo(session_id="s1", created_at=MID, updated_at=MID, models={}, tools={},
                      files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
                      long_turn_count=0, info={}, otel={}, datasource="both",
                      cost_effective_usd_micros=20_000_000, repo_id=REPO, repo_id_provenance="remote"))
    # the bridge: a real GitHub App installation would write this row (git-source.md sec 6.2)
    s.add(GitRepo(repo_id=GIT_REPO, repo_num=REPO_NUM, full_name="acme/api", owner="acme",
                 default_branch="main", private=False, installation_id=1, org_id="default",
                 backfill_state="done", first_seen_at=MID, updated_at=MID))
    pr_id = f"github:{REPO_NUM}:pr:1"
    s.add(GitArtifact(artifact_id=pr_id, kind="pr", repo_id=GIT_REPO, org_id="default",
                      number=1, user_id="csv:dev1", is_bot=False, state="merged",
                      created_at=MID, updated_at=MID, merged_at=MID,
                      stats={"additions": 120, "deletions": 40}))
    issue_id = f"github:{REPO_NUM}:issue:9"
    s.add(GitArtifact(artifact_id=issue_id, kind="issue", repo_id=GIT_REPO, org_id="default",
                      number=9, is_bot=False, title="Ship the thing", state="closed",
                      created_at=MID, updated_at=MID))
    s.add(PrIssueLink(pr_artifact_id=pr_id, tracker="github", project_key=str(REPO_NUM),
                      number=9, issue_artifact_id=issue_id, method="graphql", confidence=1,
                      created_at=MID))
    s.flush()


@requires_db
def test_project_detail_contributors_and_highlights(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        result = project_detail(s, catalog, ApiConfig(), REPO, FRM, TO)

    data = result["data"]
    assert data["project"]["repo_id"] == REPO
    assert data["project"]["merged_prs"] == 1
    assert data["contributors"][0]["user_id"] == "csv:dev1"
    assert data["contributors"][0]["merged_prs"] == 1
    assert data["highlights"]["lines_added"] == 120
    assert data["highlights"]["lines_removed"] == 40
    # A6-reopens: no transition rows captured for this repo's PRs -> honest None, never 0.
    assert data["highlights"]["reopened_prs"] is None


@requires_db
def test_project_detail_reopened_prs_counts_prs_with_a_reopen_event(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
        pr_id = f"github:{REPO_NUM}:pr:1"
        # PR #1 opened then reopened once; a captured-but-never-reopened PR would contribute 0.
        s.add(GitArtifactEvent(artifact_id=pr_id, action="opened", to_state="open",
                               occurred_at=MID, source_key=f"{pr_id}:opened:1", recorded_at=MID))
        s.add(GitArtifactEvent(artifact_id=pr_id, action="reopened", to_state="open",
                               occurred_at=MID, source_key=f"{pr_id}:reopened:1", recorded_at=MID))
        s.flush()
    with session_scope() as s:
        result = project_detail(s, catalog, ApiConfig(), REPO, FRM, TO)
    assert result["data"]["highlights"]["reopened_prs"] == 1


@requires_db
def test_project_detail_features_come_from_f4(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        result = project_detail(s, catalog, ApiConfig(), REPO, FRM, TO)

    features = result["data"]["features"]
    assert features == [{
        "id": f"github:{REPO_NUM}:issue:9",
        "issue_key": "#9", "title": "Ship the thing",
        "spend": {"usd_micros": 0, "cost_basis": "cost_effective", "cost_complete": None},
        "merged_prs": 1, "status": "shipped",
    }]


@requires_db
def test_project_detail_prs_lists_every_state_with_cost(engine, catalog):
    """G4 (mvp-gap-closure-plan): the flat "all pull requests" list -- every PR touching the
    repo in range regardless of state (not just merged, unlike `project.merged_prs`), with
    per-PR cost summed over whatever sessions actually linked to it."""
    with session_scope() as s:
        _seed(s, catalog)
        # a second, still-open PR with no linked session -- confirms "any state" and that an
        # unlinked PR's cost stays honestly None rather than 0.
        s.add(GitArtifact(artifact_id=f"github:{REPO_NUM}:pr:2", kind="pr", repo_id=GIT_REPO,
                          org_id="default", number=2, user_id="csv:dev1", is_bot=False,
                          state="open", title="WIP: still cooking", created_at=MID, updated_at=MID))
        s.add(SessionGitLink(session_id="s1", artifact_id=f"github:{REPO_NUM}:pr:1",
                             method="trailer", confidence=1, signals={}, created_at=MID))
    with session_scope() as s:
        result = project_detail(s, catalog, ApiConfig(), REPO, FRM, TO)

    by_id = {p["artifact_id"]: p for p in result["data"]["prs"]}
    assert len(by_id) == 2
    merged_pr = by_id[f"github:{REPO_NUM}:pr:1"]
    assert merged_pr["state"] == "merged"
    assert merged_pr["author_name"] == "Dev One"
    assert merged_pr["cost_usd_micros"] == 20_000_000  # s1's own cost, via session_git_links
    open_pr = by_id[f"github:{REPO_NUM}:pr:2"]
    assert open_pr["state"] == "open"
    assert open_pr["title"] == "WIP: still cooking"
    assert open_pr["cost_usd_micros"] is None          # no linked session -- never guessed 0


@requires_db
def test_project_detail_features_is_none_when_not_connected_to_github(engine, catalog):
    """No git_repos bridge row -- a repo that has sessions but no GitHub App installed."""
    with session_scope() as s:
        result = project_detail(s, catalog, ApiConfig(), "github:nobody/nothing", FRM, TO)
    assert result["data"]["features"] is None
    assert result["data"]["project"]["merged_prs"] == 0
    assert result["data"]["contributors"] == []
