"""The `git_map` worker handler (git-source.md §4.1, §5.7, §11's test bar): mapping through to
real rows, idempotency, out-of-order convergence, merge-transition correlation in both arrival
orders, and the merge-preserving upsert (a `synchronize` resync must never reset an accumulator
`bump_artifact_stat` maintains)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from server.storage.engine import session_scope
from server.worker.git_map import git_map, prune_git_deliveries
from tests.server.worker.conftest import requires_db

FIXTURES = Path("tests/fixtures/github")


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _land(s, delivery_id: str, fixture_name: str) -> None:
    fx = _fixture(fixture_name)
    s.execute(text(
        "INSERT INTO git_deliveries "
        "(delivery_id, event, action, installation_id, repo_num, payload, status, attempts, "
        " received_at, mapped_at) "
        "VALUES (:id, :event, :action, 42, 41213765, CAST(:payload AS jsonb), 'pending', 0, now(), NULL)"
    ), {"id": delivery_id, "event": fx["event"], "action": fx["action"],
        "payload": json.dumps(fx["payload"])})


def _delivery_status(delivery_id: str) -> str:
    with session_scope() as s:
        return s.execute(
            text("SELECT status FROM git_deliveries WHERE delivery_id = :id"), {"id": delivery_id}
        ).scalar()


def _artifact(artifact_id: str):
    with session_scope() as s:
        return s.execute(
            text("SELECT * FROM git_artifacts WHERE artifact_id = :id"), {"id": artifact_id}
        ).mappings().first()


@requires_db
def test_unmapped_event_lands_unmapped_not_an_error(engine, catalog):
    with session_scope() as s:
        _land(s, "d-ping", "ping")
    with session_scope() as s:
        git_map(s, catalog, {"delivery_id": "d-ping"})   # must not raise
    assert _delivery_status("d-ping") == "unmapped"


@requires_db
def test_maps_pull_request_opened_to_an_artifact(engine, catalog):
    with session_scope() as s:
        _land(s, "d-open", "pull_request_opened")
    with session_scope() as s:
        git_map(s, catalog, {"delivery_id": "d-open"})
    assert _delivery_status("d-open") == "done"
    row = _artifact("github:41213765:pr:1184")
    assert row is not None
    assert row["state"] == "open"
    assert row["title"] == "Adapter/normalizer: pair tool calls by record id"


@requires_db
def test_same_delivery_mapped_twice_is_byte_identical(engine, catalog):
    with session_scope() as s:
        _land(s, "d-open2", "pull_request_opened")
    with session_scope() as s:
        git_map(s, catalog, {"delivery_id": "d-open2"})
    first = dict(_artifact("github:41213765:pr:1184"))
    with session_scope() as s:
        git_map(s, catalog, {"delivery_id": "d-open2"})   # re-run: e.g. a retried job
    second = dict(_artifact("github:41213765:pr:1184"))
    assert first == second


@requires_db
def test_closed_before_opened_converges_to_the_newer_merged_state(engine, catalog):
    # The merged fixture's updated_at (2026-07-26) is NEWER than opened's (2026-07-24) -- map
    # the newer one first, then the older, and the older must not win (§5.4 "ordering is not
    # assumed").
    with session_scope() as s:
        _land(s, "d-merged", "pull_request_closed_merged")
        _land(s, "d-opened-late", "pull_request_opened")
    with session_scope() as s:
        git_map(s, catalog, {"delivery_id": "d-merged"})
    with session_scope() as s:
        git_map(s, catalog, {"delivery_id": "d-opened-late"})   # arrives "after," but is OLDER

    row = _artifact("github:41213765:pr:1184")
    assert row["state"] == "merged"
    assert row["merged_at"] is not None


@requires_db
def test_review_bump_survives_a_later_pull_request_resync(engine, catalog):
    """Proves the upsert_artifact merge-preserve fix: a `pull_request` event never knows
    review_count, so re-mapping the PR after a review must not reset the accumulator to zero."""
    with session_scope() as s:
        _land(s, "d-open3", "pull_request_opened")
        _land(s, "d-review", "pull_request_review_submitted")
    with session_scope() as s:
        git_map(s, catalog, {"delivery_id": "d-open3"})
    with session_scope() as s:
        git_map(s, catalog, {"delivery_id": "d-review"})
    assert _artifact("github:41213765:pr:1184")["stats"]["review_count"] == 1

    # Re-map the SAME (opened) delivery again -- simulating a retry/replay, or a later
    # `synchronize` event's upsert_artifact call carrying no review_count of its own.
    with session_scope() as s:
        git_map(s, catalog, {"delivery_id": "d-open3"})
    assert _artifact("github:41213765:pr:1184")["stats"]["review_count"] == 1


@requires_db
def test_merge_transition_correlation_pr_arrives_then_push(engine, catalog):
    with session_scope() as s:
        _land(s, "d-merged2", "pull_request_closed_merged")
        _land(s, "d-push", "push_squash_merge")
    with session_scope() as s:
        git_map(s, catalog, {"delivery_id": "d-merged2"})
    pr = _artifact("github:41213765:pr:1184")
    assert pr["before_sha"] is None   # not yet correlated

    with session_scope() as s:
        git_map(s, catalog, {"delivery_id": "d-push"})
    pr = _artifact("github:41213765:pr:1184")
    assert pr["before_sha"] is not None
    assert pr["after_sha"] == pr["merge_commit_sha"]
    assert pr["integration_method"] == "squash"
    assert pr["commit_shas"] == [pr["merge_commit_sha"]]


@requires_db
def test_merge_transition_correlation_push_arrives_then_pr(engine, catalog):
    with session_scope() as s:
        _land(s, "d-push2", "push_squash_merge")
        _land(s, "d-merged3", "pull_request_closed_merged")
    with session_scope() as s:
        git_map(s, catalog, {"delivery_id": "d-push2"})   # the commit artifact lands first
    with session_scope() as s:
        git_map(s, catalog, {"delivery_id": "d-merged3"})   # PR arrives second, self-corrects

    pr = _artifact("github:41213765:pr:1184")
    assert pr["before_sha"] is not None
    assert pr["after_sha"] == pr["merge_commit_sha"]
    assert pr["integration_method"] == "squash"


@requires_db
def test_push_rings_a_git_commit_stats_job(engine, catalog):
    # A push's commit artifacts land with empty stats; apply.py rings git_commit_stats to fill
    # them (Landing 2), coalesced one-per-push-range.
    with session_scope() as s:
        _land(s, "d-push-stats", "push_squash_merge")
    with session_scope() as s:
        git_map(s, catalog, {"delivery_id": "d-push-stats"})
    with session_scope() as s:
        jobs = s.execute(text(
            "SELECT dedup_key, payload FROM queue_jobs WHERE topic = 'git_commit_stats'"
        )).mappings().all()
    assert len(jobs) == 1
    assert jobs[0]["payload"]["repo_id"] == "github:41213765"
    assert jobs[0]["payload"]["after"] == "5b1e9c0f" + "0" * 32
    assert ".." in jobs[0]["dedup_key"]


@requires_db
def test_prune_removes_only_done_and_unmapped_past_retention(engine, catalog):
    with session_scope() as s:
        _land(s, "d-prune-done", "pull_request_opened")
        _land(s, "d-prune-pending", "pull_request_opened")
        s.execute(text("UPDATE git_deliveries SET received_at = now() - interval '30 days'"))
    with session_scope() as s:
        git_map(s, catalog, {"delivery_id": "d-prune-done"})   # -> done
    # d-prune-pending stays 'pending' (never mapped)

    deleted = prune_git_deliveries(catalog, retention_days=14)
    assert deleted == 1
    with session_scope() as s:
        remaining = {r[0] for r in s.execute(text("SELECT delivery_id FROM git_deliveries"))}
    assert remaining == {"d-prune-pending"}
