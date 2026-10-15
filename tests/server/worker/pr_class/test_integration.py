"""Integration-PR detection (docs/modules/pr-work-classification.md §5.8, §11's test bar): an
aggregate (sprint→main) PR is flagged `integration` from commit ownership alone. "Its diff is
never fetched" holds trivially in v1 -- there is no diff-fetch code path at all yet (tier 2 is
v1.5, config-gated off); "zero incremental cost" is proven at the rollup level (B6), since B2's
owning-PR dedup means an aggregate never gets a session link to begin with."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from server.storage.engine import session_scope
from server.storage.schema import Base
from server.worker.pr_class.config import PrClassConfig
from server.worker.pr_class.integration import already_owned_ratio, detect_integration
from tests.server.storage.conftest import requires_db

REPO_ID = "github:1"


def _read_pr(s, artifact_id: str) -> dict:
    """`already_owned_ratio`/`detect_integration` expect a DESERIALIZED artifact dict (as a
    real caller would have after reading `git_artifacts`, where JSONB comes back as native
    Python types) -- not the JSON-string-encoded params `upsert_artifact` itself takes."""
    row = s.execute(text(
        "SELECT artifact_id, repo_id, base_branch, branch, merged_at, commit_shas "
        "FROM git_artifacts WHERE artifact_id = :id"
    ), {"id": artifact_id}).mappings().one()
    return dict(row)


def _config(threshold=0.8):
    return PrClassConfig.model_validate({
        "eval_scope": {"base_branches": ["sprint", "main"]},
        "integration": {"already_owned_ratio_threshold": threshold},
    })


def _pr_artifact(number: int, sha_list: list[str], merged_at: str, base_branch="main", branch="sprint",
                 created_at: str | None = None):
    now = datetime.now(timezone.utc).isoformat()
    created_at = created_at or now
    return {
        "artifact_id": f"{REPO_ID}:pr:{number}", "kind": "pr", "repo_id": REPO_ID, "org_id": "org",
        "number": number, "sha": sha_list[-1] if sha_list else None, "author_login": None,
        "author_email": None, "user_id": None, "is_bot": False, "state": "merged", "title": "t",
        "branch": branch, "base_branch": base_branch, "created_at": created_at, "updated_at": now,
        "closed_at": merged_at, "merged_at": merged_at, "stats": None, "files": None,
        "parent_artifact_id": None, "labels": json.dumps([]), "milestone": None,
        "linked_issue": None, "merge_commit_sha": None, "before_sha": None, "after_sha": None,
        "integration_method": None, "reverts_sha": None,
        "commit_shas": json.dumps(sha_list), "info": json.dumps({}),
    }


@requires_db
def test_aggregate_pr_flagged_integration_from_commit_ownership(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    shas = ["a" * 40, "b" * 40, "c" * 40]
    with session_scope() as s:
        # Three feature PRs, each owning one commit, all merged into `sprint` earlier.
        for i, sha in enumerate(shas):
            s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(
                i + 1, [sha], f"2026-01-0{i+1}T00:00:00Z", base_branch="sprint", branch=f"feature-{i}",
            ))
        # The aggregate: sprint -> main, re-including all three commits, merges last.
        s.execute(catalog.get("git", "upsert_artifact"),
                 _pr_artifact(99, shas, "2026-01-10T00:00:00Z", base_branch="main", branch="sprint"))

    config = _config(threshold=0.8)
    with session_scope() as s:
        aggregate = _read_pr(s, f"{REPO_ID}:pr:99")
        ratio, digest = already_owned_ratio(s, catalog, config, aggregate)
        result = detect_integration(s, catalog, config, aggregate)
    assert ratio == 1.0
    assert digest["already_owned_commits"] == 3
    assert result is not None
    assert result["label"] == "integration"
    assert result["confidence"] == 1.0


@requires_db
@pytest.mark.parametrize("child_merges_first", [True, False])
def test_stacked_child_merged_first_does_not_mis_flag_parent_integration(engine, catalog, child_merges_first):
    """Regression (issue #49): #2 is stacked on #1 (carries #1's commit X plus its own Y). Even
    when #2 merges BEFORE #1, ownership keys on earliest-OPENED, so #1 still owns X and must NOT
    be flagged `integration` with zero cost; #2's already_owned_ratio reflects only X."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    sha_x, sha_y = "a" * 40, "b" * 40
    pr1_created, pr2_created = "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"
    pr1_merged, pr2_merged = ("2026-01-09T00:00:00Z", "2026-01-05T00:00:00Z") if child_merges_first \
        else ("2026-01-05T00:00:00Z", "2026-01-09T00:00:00Z")
    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(
            1, [sha_x], pr1_merged, base_branch="main", branch="feature-a", created_at=pr1_created))
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(
            2, [sha_x, sha_y], pr2_merged, base_branch="main", branch="feature-b", created_at=pr2_created))
    config = _config(threshold=0.8)
    with session_scope() as s:
        pr1, pr2 = _read_pr(s, f"{REPO_ID}:pr:1"), _read_pr(s, f"{REPO_ID}:pr:2")
        ratio1, _ = already_owned_ratio(s, catalog, config, pr1)
        ratio2, _ = already_owned_ratio(s, catalog, config, pr2)
        assert detect_integration(s, catalog, config, pr1) is None
    assert ratio1 == 0.0                       # #1 owns its own commit X
    assert abs(ratio2 - 0.5) < 1e-9            # #2 owns Y; X belongs to the earlier-opened #1


@requires_db
def test_pr_that_owns_all_its_own_commits_is_not_integration(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    sha = "d" * 40
    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_artifact"),
                 _pr_artifact(1, [sha], "2026-01-01T00:00:00Z", base_branch="sprint", branch="feature"))
    config = _config()
    with session_scope() as s:
        pr = _read_pr(s, f"{REPO_ID}:pr:1")
        ratio, _ = already_owned_ratio(s, catalog, config, pr)
        result = detect_integration(s, catalog, config, pr)
    assert ratio == 0.0
    assert result is None


@requires_db
def test_partial_aggregate_below_threshold_is_not_flagged(engine, catalog):
    """A sprint PR with ONE genuinely new commit alongside two already-owned ones stays below
    a strict threshold -- it did real work too, not purely an aggregate."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    owned_shas = ["e" * 40, "f" * 40]
    new_sha = "9" * 40
    with session_scope() as s:
        for i, sha in enumerate(owned_shas):
            s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(
                i + 1, [sha], f"2026-01-0{i+1}T00:00:00Z", base_branch="sprint", branch=f"feature-{i}",
            ))
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(
            50, owned_shas + [new_sha], "2026-01-10T00:00:00Z", base_branch="main", branch="sprint",
        ))
    config = _config(threshold=0.8)
    with session_scope() as s:
        mixed = _read_pr(s, f"{REPO_ID}:pr:50")
        ratio, _ = already_owned_ratio(s, catalog, config, mixed)
        result = detect_integration(s, catalog, config, mixed)
    assert abs(ratio - (2 / 3)) < 1e-9
    assert result is None   # below the 0.8 threshold


@requires_db
def test_no_commits_is_not_integration(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    # Short-circuits before any query -- a plain dict with a real (empty) Python list is enough,
    # no DB row needed.
    pr = {"artifact_id": f"{REPO_ID}:pr:1", "repo_id": REPO_ID, "commit_shas": []}
    config = _config()
    with session_scope() as s:
        ratio, digest = already_owned_ratio(s, catalog, config, pr)
        result = detect_integration(s, catalog, config, pr)
    assert ratio == 0.0
    assert digest["total_commits"] == 0
    assert result is None
