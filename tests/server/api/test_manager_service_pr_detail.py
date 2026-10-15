"""D2 GET /v1/prs/{id} -- service-level (`server/api/manager/service.py::pr_detail`).

`pr_id` is git-source's own scheme (`github:<repo_num>:pr:<number>`) throughout -- unlike A6,
D2 never needs the session-side repo_id at all for the data itself (only the *route*'s gate
does, via `resolve_pr_visibility_repo_id`, covered in `test_app_pr_detail.py`).
"""
from datetime import datetime, timezone

import pytest

from server.api.config import ApiConfig
from server.api.errors import NotFoundError
from server.api.manager.service import pr_detail
from server.storage import session_scope
from server.storage.schema import GitArtifact, GitArtifactEvent, GitRepo, SessionGitLink
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo

from .conftest import requires_db

MID = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
OPENED = datetime(2026, 9, 4, 13, 22, tzinfo=timezone.utc)
REVIEWED = datetime(2026, 9, 5, 7, 40, tzinfo=timezone.utc)
MERGED = datetime(2026, 9, 6, 11, 15, tzinfo=timezone.utc)
REPO_NUM = 777
GIT_REPO = f"github:{REPO_NUM}"
PR_ID = f"github:{REPO_NUM}:pr:502"


def _seed_repo(s):
    s.add(GitRepo(repo_id=GIT_REPO, repo_num=REPO_NUM, full_name="acme/auth-service",
                 owner="acme", default_branch="main", private=False, installation_id=1,
                 org_id="default", backfill_state="done", first_seen_at=MID, updated_at=MID))


def _seed_pr(s, **kw):
    defaults = dict(
        artifact_id=PR_ID, kind="pr", repo_id=GIT_REPO, org_id="default", number=502,
        user_id="csv:mchen", author_login="mchen", is_bot=False, state="merged", title="Make it async",
        branch="refactor/async-mw", base_branch="main", created_at=OPENED, updated_at=MERGED,
        merged_at=MERGED, labels=["refactor", "auth"], linked_issue=None,
        stats={"additions": 424, "deletions": 280, "changed_files": 6, "commits": 9},
    )
    defaults.update(kw)
    s.add(GitArtifact(**defaults))


def _seed_session(s, session_id, started_at, cost, info=None):
    s.add(SessionRow(session_id=session_id, producer_id="p", org_id="default", user_id="csv:mchen",
                     agent="claude_code", status="ended", started_at=started_at, last_event_at=started_at,
                     ended_at=started_at, signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfo(session_id=session_id, created_at=started_at, updated_at=started_at,
                      models={}, tools={}, files={}, skills=[], subagents={}, pr_list=[], turns=[],
                      turn_count=0, long_turn_count=0, info=info or {}, otel={}, datasource="both",
                      cost_effective_usd_micros=cost))
    s.add(SessionGitLink(session_id=session_id, artifact_id=PR_ID, method="trailer",
                         confidence=1, signals={}, created_at=started_at))


@requires_db
def test_unknown_pr_raises_not_found(engine, catalog):
    with session_scope() as s:
        with pytest.raises(NotFoundError):
            pr_detail(s, catalog, ApiConfig(), "github:1:pr:9999")


@requires_db
def test_pr_summary_fields_from_git_artifacts(engine, catalog):
    with session_scope() as s:
        _seed_repo(s)
        _seed_pr(s)
        s.flush()
    with session_scope() as s:
        result = pr_detail(s, catalog, ApiConfig(), PR_ID)
    pr = result["data"]["pr"]
    assert pr["artifact_id"] == PR_ID
    assert pr["number"] == 502
    assert pr["title"] == "Make it async"
    assert pr["author_user_id"] == "csv:mchen"
    assert pr["labels"] == ["refactor", "auth"]
    assert pr["lines_added"] == 424
    assert pr["lines_removed"] == 280
    assert pr["files_changed"] == 6
    assert pr["commits"] == 9
    assert pr["merged_at"] is not None


@requires_db
def test_pr_with_no_linked_sessions_is_a_real_empty_not_an_error(engine, catalog):
    with session_scope() as s:
        _seed_repo(s)
        _seed_pr(s)
        s.flush()
    with session_scope() as s:
        result = pr_detail(s, catalog, ApiConfig(), PR_ID)
    assert result["data"]["sessions"] == []
    assert result["data"]["cost"]["channels"] is None       # cold-start, no session data at all
    assert result["data"]["cost"]["pre_open"] is None
    assert result["data"]["review"]["review_rounds"] == 0
    assert result["data"]["quality"] is None                # can't classify with no linked sessions


@requires_db
def test_pre_post_open_cost_split_and_channels_from_linked_sessions(engine, catalog):
    with session_scope() as s:
        _seed_repo(s)
        _seed_pr(s)
        pre_info = {"channel_cost": {"output": {"usd_micros": 100_000_000}, "recache": {"usd_micros": 0},
                                     "input": {"usd_micros": 0}}, "carry": {"carry_usd_micros": 0}}
        post_info = {"channel_cost": {"output": {"usd_micros": 40_000_000}, "recache": {"usd_micros": 10_000_000},
                                      "input": {"usd_micros": 0}}, "carry": {"carry_usd_micros": 16_000_000}}
        _seed_session(s, "s-pre", MID, 100_000_000, info=pre_info)                 # before PR opened
        _seed_session(s, "s-post", OPENED, 66_000_000, info=post_info)             # at/after PR opened
        s.flush()
    with session_scope() as s:
        result = pr_detail(s, catalog, ApiConfig(), PR_ID)

    cost = result["data"]["cost"]
    assert cost["pre_open"]["usd_micros"] == 100_000_000
    assert cost["post_open"]["usd_micros"] == 66_000_000
    assert cost["spend"]["usd_micros"] == 166_000_000
    channels = cost["channels"]
    assert channels["output_usd_micros"] == 140_000_000
    assert channels["recache_usd_micros"] == 10_000_000
    assert channels["carry_usd_micros"] == 16_000_000
    assert channels["complete"] is True


@requires_db
def test_session_title_carries_from_the_causal_graph_fold(engine, catalog):
    """The causal graph fold already lifts the vendor's ai_title into
    `session_info.info.title` (`PRSessionRef`'s own docstring has the full path); this was
    never read into a session reference before. A session the vendor never auto-titled stays
    `None`, not guessed."""
    with session_scope() as s:
        _seed_repo(s)
        _seed_pr(s)
        _seed_session(s, "s-titled", MID, 10_000_000, info={"title": "Add rate limiting"})
        _seed_session(s, "s-untitled", MID, 5_000_000, info={})
        s.flush()
    with session_scope() as s:
        result = pr_detail(s, catalog, ApiConfig(), PR_ID)

    by_id = {r["session_id"]: r for r in result["data"]["sessions"]}
    assert by_id["s-titled"]["title"] == "Add rate limiting"
    assert by_id["s-untitled"]["title"] is None


@requires_db
def test_review_rounds_and_quality_label_and_flags(engine, catalog):
    with session_scope() as s:
        _seed_repo(s)
        _seed_pr(s)
        _seed_session(s, "s2", MID, 1_000_000_000)
        s.add(GitArtifact(artifact_id=f"github:{REPO_NUM}:review:1", kind="review",
                          repo_id=GIT_REPO, org_id="default", parent_artifact_id=PR_ID,
                          user_id="csv:pnair", author_login="pnair", is_bot=False,
                          state="CHANGES_REQUESTED", created_at=REVIEWED, updated_at=REVIEWED))
        s.flush()
    # a one-PR repo: loosen the min-PR floor so this PR is ranked (it is its own p75 -> high)
    config = ApiConfig.model_validate({"quality": {"making_cost_min_prs": 1}})
    with session_scope() as s:
        result = pr_detail(s, catalog, config, PR_ID)

    review = result["data"]["review"]
    assert review["review_rounds"] == 1
    assert review["reviews"][0]["state"] == "CHANGES_REQUESTED"
    assert review["reviews"][0]["reviewer_user_id"] == "csv:pnair"
    assert result["data"]["quality"] == "Costly rework"
    labels = {f["label"] for f in result["data"]["flags"]}
    assert "Multiple review rounds" in labels
    assert "Costly rework" in labels
    # TODO(X1-churn): not computable until the git_commit_stats worker (Landing 2) fills
    # post-open line stats -- cold-start here.
    assert result["data"]["rework_ratio"] is None
    assert result["data"]["churn_post_usd_micros"] is None
    # X1-reopen IS captured now, but this PR has no git_artifact_events rows -> honest None
    # (the transition log never saw it), never a guessed 0.
    assert result["data"]["reopens"] is None


PR_BRANCH = "refactor/async-mw"   # _seed_pr's default head branch


def _seed_commit(s, sha, created_at, *, stats, branch=PR_BRANCH):
    s.add(GitArtifact(artifact_id=f"{GIT_REPO}:commit:{sha}", kind="commit", repo_id=GIT_REPO,
                      org_id="default", sha=sha, branch=branch, is_bot=False, state="pushed",
                      created_at=created_at, updated_at=created_at, stats=stats))


@requires_db
def test_post_open_churn_and_rework_ratio_from_head_branch_commits(engine, catalog):
    with session_scope() as s:
        _seed_repo(s)
        _seed_pr(s)   # branch refactor/async-mw, opened OPENED, merged MERGED; final 424+280=704
        _seed_commit(s, "preopen", OPENED, stats={"additions": 5, "deletions": 5})     # not > OPENED
        _seed_commit(s, "postopen", REVIEWED, stats={"additions": 30, "deletions": 10})  # after OPENED
        s.flush()
    with session_scope() as s:
        result = pr_detail(s, catalog, ApiConfig(), PR_ID)
    # only the post-open head-branch commit's 40 lines count.
    assert result["data"]["churn_post_lines"] == 40
    assert result["data"]["rework_ratio"] == round(40 / 704, 4)
    assert result["data"]["churn_post_usd_micros"] is None   # no lines->$ conversion (Landing 3)


@requires_db
def test_squash_merge_commit_on_base_branch_does_not_count_as_churn(engine, catalog):
    # The design reason for branch-linkage over commit_shas: a squash/rebase collapses the PR
    # into ONE base-branch commit created post-open. It must NOT read as ~100% churn.
    with session_scope() as s:
        _seed_repo(s)
        _seed_pr(s)
        _seed_commit(s, "headwork", OPENED, stats={"additions": 5, "deletions": 5})   # head branch, pre-open
        _seed_commit(s, "squash", MERGED, stats={"additions": 700, "deletions": 4}, branch="main")
        s.flush()
    with session_scope() as s:
        result = pr_detail(s, catalog, ApiConfig(), PR_ID)
    assert result["data"]["churn_post_lines"] == 0        # squash on main excluded; head pre-open excluded
    assert result["data"]["rework_ratio"] == 0.0


@requires_db
def test_post_open_churn_is_none_when_post_open_commits_have_no_stats(engine, catalog):
    # A post-open head-branch commit exists but carries no captured stats (pre-Landing-2 /
    # pre-backfill): churn is unknown, not zero.
    with session_scope() as s:
        _seed_repo(s)
        _seed_pr(s)
        _seed_commit(s, "postopen", REVIEWED, stats={})
        s.flush()
    with session_scope() as s:
        result = pr_detail(s, catalog, ApiConfig(), PR_ID)
    assert result["data"]["churn_post_lines"] is None
    assert result["data"]["rework_ratio"] is None


@requires_db
def test_post_open_churn_is_none_when_no_commit_history_captured(engine, catalog):
    # The default seeded PR has no commit artifacts at all -> we can't see its pushes -> unknown,
    # not a false 0.
    with session_scope() as s:
        _seed_repo(s)
        _seed_pr(s)
        s.flush()
    with session_scope() as s:
        result = pr_detail(s, catalog, ApiConfig(), PR_ID)
    assert result["data"]["churn_post_lines"] is None
    assert result["data"]["rework_ratio"] is None


@requires_db
def test_reopen_count_and_flag_surface_from_the_transition_log(engine, catalog):
    with session_scope() as s:
        _seed_repo(s)
        _seed_pr(s)
        # Two captured transitions incl. one reopen (what the events table would hold).
        s.add(GitArtifactEvent(artifact_id=PR_ID, action="opened", to_state="open",
                               occurred_at=OPENED, source_key=f"{PR_ID}:opened:1", recorded_at=OPENED))
        s.add(GitArtifactEvent(artifact_id=PR_ID, action="reopened", to_state="open",
                               occurred_at=REVIEWED, source_key=f"{PR_ID}:reopened:1", recorded_at=REVIEWED))
        s.flush()
    with session_scope() as s:
        result = pr_detail(s, catalog, ApiConfig(), PR_ID)
    assert result["data"]["reopens"] == 1
    assert "Reopened" in {f["label"] for f in result["data"]["flags"]}
