"""D3 GET /v1/features/{id} -- service-level (`server/api/manager/service.py::feature_detail`).

`feature_id` is the routable id `quality._feature_id` hands out (`github:<repo_num>:issue:<n>`
or `pr:<pr_artifact_id>`), not the display `issue_key` -- see `test_manager_quality.py`'s own
round-trip test for that pairing.
"""
from datetime import datetime, timezone

import pytest

from server.api.config import ApiConfig
from server.api.errors import NotFoundError
from server.api.manager.service import feature_detail
from server.storage import session_scope
from server.storage.schema import GitArtifact, GitRepo, PrIssueLink, SessionGitLink
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo

from .conftest import requires_db

MID = datetime(2026, 9, 1, tzinfo=timezone.utc)
OPENED = datetime(2026, 9, 2, tzinfo=timezone.utc)
MERGED = datetime(2026, 9, 5, tzinfo=timezone.utc)
REPO_NUM = 314
GIT_REPO = f"github:{REPO_NUM}"
ISSUE_ID = f"github:{REPO_NUM}:issue:214"
PR_ID = f"github:{REPO_NUM}:pr:502"


def _seed_repo(s):
    s.add(GitRepo(repo_id=GIT_REPO, repo_num=REPO_NUM, full_name="acme/auth-service",
                 owner="acme", default_branch="main", private=False, installation_id=1,
                 org_id="default", backfill_state="done", first_seen_at=MID, updated_at=MID))


@requires_db
def test_unknown_feature_id_raises_not_found(engine, catalog):
    with session_scope() as s:
        with pytest.raises(NotFoundError):
            feature_detail(s, catalog, ApiConfig(), "github:1:issue:9999")


@requires_db
def test_bogus_id_shape_raises_not_found(engine, catalog):
    with session_scope() as s:
        with pytest.raises(NotFoundError):
            feature_detail(s, catalog, ApiConfig(), "PLAT-214")


@requires_db
def test_issue_keyed_feature_with_a_merged_pr_and_a_session(engine, catalog):
    with session_scope() as s:
        _seed_repo(s)
        s.add(GitArtifact(artifact_id=ISSUE_ID, kind="issue", repo_id=GIT_REPO, org_id="default",
                          number=214, is_bot=False, title="Make the auth middleware async",
                          state="closed", created_at=MID, updated_at=MERGED))
        s.add(GitArtifact(artifact_id=PR_ID, kind="pr", repo_id=GIT_REPO, org_id="default",
                          number=502, user_id="csv:mchen", is_bot=False, state="merged",
                          title="Make it async", created_at=OPENED, updated_at=MERGED,
                          merged_at=MERGED, stats={}))
        s.add(PrIssueLink(pr_artifact_id=PR_ID, tracker="github", project_key=str(REPO_NUM),
                          number=214, issue_artifact_id=ISSUE_ID, method="graphql",
                          confidence=1, created_at=MID))
        s.add(SessionRow(session_id="s2", producer_id="p", org_id="default", user_id="csv:mchen",
                         agent="claude_code", status="ended", started_at=MID, last_event_at=MID,
                         ended_at=MID, signal_blob={}, info_blob={}, subagents=[]))
        s.add(SessionInfo(session_id="s2", created_at=MID, updated_at=MID, models={}, tools={},
                          files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
                          long_turn_count=0, info={}, otel={}, datasource="both",
                          cost_effective_usd_micros=148_000_000))
        s.add(SessionGitLink(session_id="s2", artifact_id=PR_ID, method="trailer",
                             confidence=1, signals={}, created_at=MID))
        s.flush()
    with session_scope() as s:
        result = feature_detail(s, catalog, ApiConfig(), ISSUE_ID)

    data = result["data"]
    assert data["id"] == ISSUE_ID
    assert data["issue_key"] == "#214"
    assert data["title"] == "Make the auth middleware async"
    assert data["status"] == "shipped"
    assert data["repo_id"] == GIT_REPO
    assert data["spend"]["usd_micros"] == 148_000_000     # Σ member-session cost
    assert len(data["member_prs"]) == 1
    assert data["member_prs"][0]["artifact_id"] == PR_ID
    assert data["member_prs"][0]["cost"]["usd_micros"] == 148_000_000
    assert data["contributors"] == [{"user_id": "csv:mchen", "name": "csv:mchen"}]
    assert data["lifecycle"]["first_pr_opened_at"] is not None
    assert data["lifecycle"]["last_merged_at"] is not None
    # this feature's own single merged PR -> 0 open PRs -> a real zero, not cold-start
    assert data["cost_to_finish"]["open_prs"] == 0
    assert data["cost_to_finish"]["basis"] == "No open PRs remain."
    assert data["benchmark"] is None    # cold-start: a one-feature repo has no peers to rank against


@requires_db
def test_single_pr_fallback_feature_has_no_issue_but_still_resolves(engine, catalog):
    with session_scope() as s:
        _seed_repo(s)
        s.add(GitArtifact(artifact_id=PR_ID, kind="pr", repo_id=GIT_REPO, org_id="default",
                          number=502, user_id="csv:mchen", is_bot=False, state="open",
                          title="A standalone PR", created_at=OPENED, updated_at=OPENED,
                          stats={}))
        s.flush()
    with session_scope() as s:
        result = feature_detail(s, catalog, ApiConfig(), f"pr:{PR_ID}")

    data = result["data"]
    assert data["id"] == f"pr:{PR_ID}"
    assert data["issue_key"] == "#502"
    assert data["status"] == "in-progress"
    assert data["member_prs"][0]["artifact_id"] == PR_ID
    # no linked sessions at all -> cold-start spend, not a real zero
    assert data["spend"]["usd_micros"] is None
    assert data["channels"] is None
    # one open PR, but no priced session history -> no envelope to base an estimate on
    assert data["cost_to_finish"] is None


def _seed_feature(s, n: int, cost_micros: int, *, merged: bool = True, open_pr: bool = False):
    """One issue-keyed feature: an issue, a merged (or open) PR linked to it, and a linked
    session carrying `cost_micros`. Used to give a repo enough priced peers/PRs to exercise the
    repo-wide envelope (D3-envelope-scope) and the cross-feature benchmark (D3-benchmark)."""
    issue_id = f"github:{REPO_NUM}:issue:{n}"
    pr_id = f"github:{REPO_NUM}:pr:{600 + n}"
    sess = f"sf{n}"
    s.add(GitArtifact(artifact_id=issue_id, kind="issue", repo_id=GIT_REPO, org_id="default",
                      number=n, is_bot=False, title=f"feature {n}", state="closed",
                      created_at=MID, updated_at=MERGED))
    s.add(GitArtifact(artifact_id=pr_id, kind="pr", repo_id=GIT_REPO, org_id="default",
                      number=600 + n, user_id="csv:dev", is_bot=False,
                      state="open" if open_pr else "merged", title=f"pr {n}",
                      created_at=OPENED, updated_at=MERGED,
                      merged_at=None if open_pr else MERGED, stats={}))
    s.add(PrIssueLink(pr_artifact_id=pr_id, tracker="github", project_key=str(REPO_NUM),
                      number=n, issue_artifact_id=issue_id, method="graphql", confidence=1,
                      created_at=MID))
    s.add(SessionRow(session_id=sess, producer_id="p", org_id="default", user_id="csv:dev",
                     agent="claude_code", status="ended", started_at=MID, last_event_at=MID,
                     ended_at=MID, signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfo(session_id=sess, created_at=MID, updated_at=MID, models={}, tools={},
                      files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
                      long_turn_count=0, info={}, otel={}, datasource="both",
                      cost_effective_usd_micros=cost_micros))
    s.add(SessionGitLink(session_id=sess, artifact_id=pr_id, method="trailer", confidence=1,
                         signals={}, created_at=MID))
    return f"github:{REPO_NUM}:issue:{n}"


@requires_db
def test_benchmark_and_repo_wide_envelope_light_up_with_enough_priced_peers(engine, catalog):
    """D3-benchmark + D3-envelope-scope: a repo with several priced features ranks the subject
    against its peers, and the cost-to-finish envelope is drawn from the repo's trailing merged
    PRs -- not just the subject's single member PR."""
    costs = [10_000_000, 20_000_000, 30_000_000, 40_000_000, 50_000_000, 60_000_000]
    fids = []
    with session_scope() as s:
        _seed_repo(s)
        for i, c in enumerate(costs):
            fids.append(_seed_feature(s, 200 + i, c, open_pr=(i == 0)))  # feature 0's PR is OPEN
        s.flush()

    # D3-benchmark: a mid-priced feature (index 2, $30) is more expensive than 2 of its 5 priced
    # peers -> 40th percentile.
    with session_scope() as s:
        bench = feature_detail(s, catalog, ApiConfig(), fids[2])["data"]["benchmark"]
    assert bench is not None
    assert bench["subject_cost_usd_micros"] == 30_000_000
    assert bench["peer_count"] == 5
    assert bench["cost_percentile"] == 40
    assert bench["cost_dist"]["min"] == 10 and bench["cost_dist"]["max"] == 60   # dollars

    # D3-envelope-scope: feature 0's single OPEN PR is estimated from the REPO's 5 trailing merged
    # PRs (20/30/40/50/60 -> median $40), NOT its own $10 member -- proving the envelope widened.
    with session_scope() as s:
        ctf = feature_detail(s, catalog, ApiConfig(), fids[0])["data"]["cost_to_finish"]
    assert ctf is not None
    assert ctf["open_prs"] == 1
    assert ctf["mid_usd_micros"] == 40_000_000   # 1 open PR x repo median $40, not feature-local $10
