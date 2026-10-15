"""Attribution / rollup (docs/plan/2026-08-25-git-integration-implementation.md B6, design §6):
rollup dedups across multi-PR membership; the rework join nets correctly; grouping GROUP BYs
(repo/user/module/label) return the expected splits; re-running is idempotent."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text

from server.storage.engine import session_scope
from server.storage.schema import Base, Session as SessionRow
from server.worker.pr_class.rollup import (
    coverage_by_confidence_band,
    refresh_all_session_pr_lists,
    rework_cost_total,
    rollup_by,
)
from tests.server.storage.conftest import requires_db

REPO_ID = "github:1"


def _session(session_id: str, user_id: str | None = None) -> SessionRow:
    now = datetime.now(timezone.utc)
    return SessionRow(
        session_id=session_id, producer_id="p", org_id=None, user_id=user_id, agent="claude_code",
        agent_version=None, status="ended", started_at=now, last_event_at=now, ended_at=now,
        signal_blob={"events": []}, info_blob={}, parent_session_id=None, subagents=[],
    )


def _insert_session_info(s, session_id: str, cost_usd: float | None) -> None:
    # `cost_effective_usd_micros`, not `cost_usd` -- the rollup queries read otel_trueup.py's
    # ledger column (populated for every session including jsonl_only), not the dead `cost_usd`
    # column the JSONL fold declares but never writes (fold/driver.py's own comment).
    micros = None if cost_usd is None else round(cost_usd * 1_000_000)
    s.execute(text(
        "INSERT INTO session_info (session_id, cost_effective_usd_micros, datasource, models, "
        "tools, files, skills, subagents, pr_list, turns, turn_count, long_turn_count, "
        "tokens_total, compaction_count, subagent_count, error_count, info, otel, repo_id, "
        "repo_id_provenance, created_at, updated_at) VALUES "
        "(:session_id, :micros, 'jsonl_only', '{}', '{}', '{}', '[]', '{}', '[]', '[]', 0, 0, 0, "
        "0, 0, 0, '{}', '{}', :repo_id, 'remote', now(), now())"
    ), {"session_id": session_id, "micros": micros, "repo_id": REPO_ID})


def _pr_artifact(number: int, labels: list[str] | None = None, repo_id: str = REPO_ID) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "artifact_id": f"{repo_id}:pr:{number}", "kind": "pr", "repo_id": repo_id, "org_id": "org",
        "number": number, "sha": None, "author_login": None, "author_email": None,
        "user_id": None, "is_bot": False, "state": "merged", "title": "t", "branch": "feature",
        "base_branch": "main", "created_at": now, "updated_at": now, "closed_at": now,
        "merged_at": now, "stats": None, "files": None, "parent_artifact_id": None,
        "labels": json.dumps(labels or []), "milestone": None, "linked_issue": None,
        "merge_commit_sha": None, "before_sha": None, "after_sha": None,
        "integration_method": None, "reverts_sha": None, "commit_shas": json.dumps([]),
        "info": json.dumps({}),
    }


def _insert_classification(s, catalog, artifact_id: str, change_type: str = "fix",
                           artifact_class: str = "coding", module: str | None = None,
                           rework_ratio: float | None = None) -> None:
    s.execute(catalog.get("pr_class", "upsert_pr_classification"), {
        "artifact_id": artifact_id, "change_type": change_type, "change_confidence": 1.0,
        "change_tier": 0, "artifact_class": artifact_class, "is_revert": False,
        "is_recall": False, "recall_reason": None, "recall_confidence": None, "module": module,
        "rework_ratio": rework_ratio, "features_digest": json.dumps({}), "analyzer": "heuristic",
    })


def _link(s, catalog, session_id: str, artifact_id: str, confidence: float = 1.0,
         method: str = "sha") -> None:
    s.execute(catalog.get("pr_class", "upsert_session_git_link"), {
        "session_id": session_id, "artifact_id": artifact_id, "method": method,
        "confidence": confidence, "signals": json.dumps({}),
    })


@requires_db
def test_rollup_by_change_type_dedups_across_multi_pr_membership(engine, catalog):
    """One session linked to TWO different "fix"-labeled PRs must contribute its cost to the
    "fix" bucket exactly once, not once per PR."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        s.add(_session("s1"))
        _insert_session_info(s, "s1", 10.0)
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(1))
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(2))
        _insert_classification(s, catalog, f"{REPO_ID}:pr:1", change_type="fix")
        _insert_classification(s, catalog, f"{REPO_ID}:pr:2", change_type="fix")
        _link(s, catalog, "s1", f"{REPO_ID}:pr:1")
        _link(s, catalog, "s1", f"{REPO_ID}:pr:2")
    with session_scope() as s:
        rows = rollup_by(s, catalog, "change_type")
    assert rows == [{"value": "fix", "session_count": 1, "total_cost_usd": 10}]


@requires_db
def test_rollup_grouping_dimensions_return_expected_splits(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        s.add(_session("s1", user_id="u1"))
        s.add(_session("s2", user_id="u2"))
        _insert_session_info(s, "s1", 10.0)
        _insert_session_info(s, "s2", 20.0)
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(1, labels=["enhancement"]))
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(2, labels=["bug"]))
        _insert_classification(s, catalog, f"{REPO_ID}:pr:1", change_type="feature",
                               artifact_class="coding", module="server")
        _insert_classification(s, catalog, f"{REPO_ID}:pr:2", change_type="fix",
                               artifact_class="coding", module="worker")
        _link(s, catalog, "s1", f"{REPO_ID}:pr:1")
        _link(s, catalog, "s2", f"{REPO_ID}:pr:2")
    with session_scope() as s:
        by_change_type = rollup_by(s, catalog, "change_type")
        by_module = rollup_by(s, catalog, "module")
        by_repo = rollup_by(s, catalog, "repo")
        by_user = rollup_by(s, catalog, "user")
        by_label = rollup_by(s, catalog, "label")

    assert by_change_type == [
        {"value": "feature", "session_count": 1, "total_cost_usd": 10},
        {"value": "fix", "session_count": 1, "total_cost_usd": 20},
    ]
    assert by_module == [
        {"value": "server", "session_count": 1, "total_cost_usd": 10},
        {"value": "worker", "session_count": 1, "total_cost_usd": 20},
    ]
    assert by_repo == [{"value": REPO_ID, "session_count": 2, "total_cost_usd": 30}]
    assert {r["value"]: r["total_cost_usd"] for r in by_user} == {"u1": 10, "u2": 20}
    assert {r["value"]: r["total_cost_usd"] for r in by_label} == {"enhancement": 10, "bug": 20}


@requires_db
def test_rework_cost_total_is_none_until_computed_then_nets_correctly(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        s.add(_session("s1"))
        s.add(_session("s2"))
        _insert_session_info(s, "s1", 10.0)
        _insert_session_info(s, "s2", 10.0)
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(1))
        # No rework_ratio anywhere yet.
        _insert_classification(s, catalog, f"{REPO_ID}:pr:1", rework_ratio=None)
        _link(s, catalog, "s1", f"{REPO_ID}:pr:1")
        _link(s, catalog, "s2", f"{REPO_ID}:pr:1")
    with session_scope() as s:
        assert rework_cost_total(s, catalog) is None   # not yet computed, not zero

    with session_scope() as s:
        # The sibling rework module joins a ratio in later -- the classify pass must never
        # have clobbered it, so this simulates that module's own write.
        _insert_classification(s, catalog, f"{REPO_ID}:pr:1", rework_ratio=0.5)
    with session_scope() as s:
        total = rework_cost_total(s, catalog)
    # Two sessions x $10 attributed to pr:1 = $20 attributed cost; 50% rework = $10.
    assert float(total) == 10.0


@requires_db
def test_coverage_by_confidence_band(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        s.add(_session("s1"))
        s.add(_session("s2"))
        s.add(_session("s3"))
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(1))
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(2))
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(3))
        _link(s, catalog, "s1", f"{REPO_ID}:pr:1", confidence=1.0, method="sha")
        _link(s, catalog, "s2", f"{REPO_ID}:pr:2", confidence=1.0, method="trailer")
        _link(s, catalog, "s3", f"{REPO_ID}:pr:3", confidence=0.5, method="heuristic")
    with session_scope() as s:
        bands = coverage_by_confidence_band(s, catalog)
    assert bands == {"exact": 2, "low": 1}


@requires_db
def test_rollup_by_unknown_dimension_raises(engine, catalog):
    with session_scope() as s:
        try:
            rollup_by(s, catalog, "bogus")
            assert False, "expected ValueError"
        except ValueError:
            pass


@requires_db
def test_rollup_is_idempotent_on_rerun(engine, catalog):
    """Design §7: a re-run of a purely-computed rollup is byte-identical -- no accumulation,
    no state carried between runs."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        s.add(_session("s1"))
        _insert_session_info(s, "s1", 10.0)
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(1))
        _insert_classification(s, catalog, f"{REPO_ID}:pr:1", change_type="docs")
        _link(s, catalog, "s1", f"{REPO_ID}:pr:1")
    with session_scope() as s:
        first = rollup_by(s, catalog, "change_type")
    with session_scope() as s:
        second = rollup_by(s, catalog, "change_type")
    assert first == second == [{"value": "docs", "session_count": 1, "total_cost_usd": 10}]


@requires_db
def test_refresh_all_session_pr_lists_backfills_every_linked_session(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        s.add(_session("s1"))
        s.add(_session("s2"))
        _insert_session_info(s, "s1", 5.0)
        _insert_session_info(s, "s2", 5.0)
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(1))
        _insert_classification(s, catalog, f"{REPO_ID}:pr:1", change_type="chore")
        _link(s, catalog, "s1", f"{REPO_ID}:pr:1")
        # s2 has no link at all -- must not appear, and must not error.
    with session_scope() as s:
        touched = refresh_all_session_pr_lists(s, catalog)
    assert touched == 1
    with session_scope() as s:
        pr_list = s.execute(text(
            "SELECT pr_list FROM session_info WHERE session_id = 's1'"
        )).scalar()
        s2_list = s.execute(text(
            "SELECT pr_list FROM session_info WHERE session_id = 's2'"
        )).scalar()
    assert pr_list == [{
        "artifact_id": f"{REPO_ID}:pr:1", "method": "sha", "confidence": 1.0,
        "change_type": "chore", "repo_id": REPO_ID, "number": 1,
    }]
    assert s2_list == []   # never linked -- untouched, stays the empty default
