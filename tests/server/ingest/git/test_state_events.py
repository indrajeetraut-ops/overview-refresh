"""PR state-transition capture (Track churn/reopen, Landing 1): `git_artifact_events` is the
append-only history a reopen COUNT reads, since `git_artifacts.state` is current-state-only.
Idempotent under delivery replay via the `source_key` unique constraint. DB-backed; skips
without `TALIUM_TEST_DSN`."""
from __future__ import annotations

from sqlalchemy import text

from server.api.manager.quality import pr_acceptance_metrics
from server.ingest.git.apply import apply_map_result
from server.ingest.git.map_github import map_delivery
from server.storage.engine import session_scope
from tests.server.ingest.conftest import requires_db

ORG_ID = "org_test"
REPO_NUM = 41213765
PR_ID = f"github:{REPO_NUM}:pr:70"


def _pr(action: str, *, merged=False, state="open", updated_at: str) -> dict:
    return {
        "action": action,
        "pull_request": {
            "number": 70, "title": "feat: x", "state": state, "merged": merged,
            "user": {"login": "akhuteta"}, "head": {"ref": "f", "sha": "a" * 40},
            "base": {"ref": "main"},
            "created_at": "2026-08-01T00:00:00Z", "updated_at": updated_at,
            "closed_at": None, "merged_at": None, "labels": [],
        },
        "repository": {"id": REPO_NUM},
    }


#: opened -> closed -> reopened -> closed(merged): one reopen, one merge.
_LIFECYCLE = [
    _pr("opened", updated_at="2026-08-01T00:00:00Z"),
    _pr("closed", state="closed", updated_at="2026-08-02T00:00:00Z"),
    _pr("reopened", updated_at="2026-08-03T00:00:00Z"),
    _pr("closed", merged=True, state="closed", updated_at="2026-08-04T00:00:00Z"),
]


def _apply_lifecycle(catalog):
    with session_scope() as s:
        for p in _LIFECYCLE:
            apply_map_result(s, catalog, map_delivery("pull_request", p["action"], p, ORG_ID))


@requires_db
def test_lifecycle_records_one_event_per_transition(engine, catalog):
    _apply_lifecycle(catalog)
    with session_scope() as s:
        rows = s.execute(text(
            "SELECT action, to_state FROM git_artifact_events WHERE artifact_id = :a "
            "ORDER BY occurred_at"), {"a": PR_ID}).all()
    assert [(r.action, r.to_state) for r in rows] == [
        ("opened", "open"), ("closed", "closed"), ("reopened", "open"), ("merged", "merged"),
    ]


@requires_db
def test_replay_of_the_same_deliveries_adds_no_events(engine, catalog):
    _apply_lifecycle(catalog)
    _apply_lifecycle(catalog)  # same (artifact, action, updated_at) tuples -> source_key conflict
    with session_scope() as s:
        n = s.execute(text("SELECT COUNT(*) FROM git_artifact_events WHERE artifact_id = :a"),
                      {"a": PR_ID}).scalar()
    assert n == 4


@requires_db
def test_reopen_count_surfaces_via_acceptance_metrics(engine, catalog):
    _apply_lifecycle(catalog)
    with session_scope() as s:
        metrics = pr_acceptance_metrics(s, catalog, [PR_ID])
    assert metrics[PR_ID]["reopens"] == 1


@requires_db
def test_reopens_is_none_for_a_pr_with_no_captured_transitions(engine, catalog):
    # A PR that exists but has no event rows (ingested before the log existed): honest None,
    # never a guessed 0.
    with session_scope() as s:
        s.execute(text(
            "INSERT INTO git_artifacts (artifact_id, kind, repo_id, org_id, number, is_bot, "
            "state, created_at, updated_at, stats, files, labels, commit_shas, info) VALUES "
            "(:a, 'pr', :r, :o, 71, false, 'open', now(), now(), '{}', '[]', '[]', '[]', '{}')"),
            {"a": f"github:{REPO_NUM}:pr:71", "r": f"github:{REPO_NUM}", "o": ORG_ID})
    with session_scope() as s:
        metrics = pr_acceptance_metrics(s, catalog, [f"github:{REPO_NUM}:pr:71"])
    assert metrics[f"github:{REPO_NUM}:pr:71"]["reopens"] is None
