"""The `pr_classify` topic handler: ties B3 (change-type) + B4 (artifact_class, recall/revert,
module) + B5 (integration) together into one `pr_classification` row, and is idempotent under
replay (design §7: "a logic fix is a re-run, byte-identical result")."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text

from server.storage.engine import session_scope
from server.storage.schema import Base
from server.worker.pr_class.config import PrClassConfig, ModuleMapEntry
from server.worker.pr_class.pr_classify import pr_classify
from tests.server.storage.conftest import requires_db

REPO_ID = "github:1"


def _config(**overrides):
    data = {"eval_scope": {"base_branches": ["main"]}}
    data.update(overrides)
    return PrClassConfig.model_validate(data)


def _pr_artifact(number, title="t", labels=None, files=None, state="merged", merged_at="2026-01-01T00:00:00Z",
                 commit_shas=None, reverts_sha=None, is_revert=False, base_branch="main", branch="feature"):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "artifact_id": f"{REPO_ID}:pr:{number}", "kind": "pr", "repo_id": REPO_ID, "org_id": "org",
        "number": number, "sha": "a" * 40, "author_login": None, "author_email": None,
        "user_id": None, "is_bot": False, "state": state, "title": title,
        "branch": branch, "base_branch": base_branch, "created_at": now, "updated_at": now,
        "closed_at": now if state != "open" else None, "merged_at": merged_at,
        "stats": None, "files": json.dumps(files) if files is not None else None,
        "parent_artifact_id": None, "labels": json.dumps(labels or []), "milestone": None,
        "linked_issue": None, "merge_commit_sha": None, "before_sha": None, "after_sha": None,
        "integration_method": None, "reverts_sha": reverts_sha,
        "commit_shas": json.dumps(commit_shas or []),
        "info": json.dumps({"github.is_revert": is_revert}),
    }


@requires_db
def test_pr_classify_writes_a_full_classification_row(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(
            1, title="feat: add widget", labels=["enhancement"],
            files=[{"path": "src/widget.py", "op": "added", "additions": 50, "deletions": 0}],
        ))
    with session_scope() as s:
        pr_classify(s, catalog, _config(), {"artifact_id": f"{REPO_ID}:pr:1"})
    with session_scope() as s:
        row = s.execute(catalog.get("pr_class", "get_pr_classification"),
                        {"artifact_id": f"{REPO_ID}:pr:1"}).one()
    assert row.change_type == "feature"
    assert row.change_tier == 0
    assert float(row.change_confidence) == 1.0
    assert row.artifact_class == "coding"
    assert row.is_revert is False
    assert row.is_recall is False
    assert row.analyzer == "heuristic"


@requires_db
def test_pr_classify_flags_recall_for_closed_unmerged_pr(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_artifact"),
                 _pr_artifact(2, title="Abandoned idea", state="closed", merged_at=None))
    with session_scope() as s:
        pr_classify(s, catalog, _config(), {"artifact_id": f"{REPO_ID}:pr:2"})
    with session_scope() as s:
        row = s.execute(catalog.get("pr_class", "get_pr_classification"),
                        {"artifact_id": f"{REPO_ID}:pr:2"}).one()
    assert row.is_recall is True


@requires_db
def test_pr_classify_integration_overrides_change_type_and_skips_tier01(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    sha = "b" * 40
    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_artifact"),
                 _pr_artifact(10, title="feat: this looks like a feature", commit_shas=[sha],
                             base_branch="sprint", merged_at="2026-01-01T00:00:00Z"))
        s.execute(catalog.get("git", "upsert_artifact"),
                 _pr_artifact(99, title="Merge sprint into main", commit_shas=[sha],
                             base_branch="main", branch="sprint", merged_at="2026-01-10T00:00:00Z"))
    config = _config(eval_scope={"base_branches": ["sprint", "main"]},
                     integration={"already_owned_ratio_threshold": 0.8})
    with session_scope() as s:
        pr_classify(s, catalog, config, {"artifact_id": f"{REPO_ID}:pr:99"})
    with session_scope() as s:
        row = s.execute(catalog.get("pr_class", "get_pr_classification"),
                        {"artifact_id": f"{REPO_ID}:pr:99"}).one()
    assert row.change_type == "integration"   # NOT "feature", despite the title
    assert float(row.change_confidence) == 1.0
    assert row.change_tier == 0


@requires_db
def test_pr_classify_is_idempotent_on_replay(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(
            3, title="docs: update readme",
            files=[{"path": "README.md", "op": "modified", "additions": 5, "deletions": 1}],
        ))
    with session_scope() as s:
        pr_classify(s, catalog, _config(), {"artifact_id": f"{REPO_ID}:pr:3"})
    with session_scope() as s:
        pr_classify(s, catalog, _config(), {"artifact_id": f"{REPO_ID}:pr:3"})   # replay
    with session_scope() as s:
        count = s.execute(text("SELECT count(*) FROM pr_classification")).scalar()
        row = s.execute(catalog.get("pr_class", "get_pr_classification"),
                        {"artifact_id": f"{REPO_ID}:pr:3"}).one()
    assert count == 1
    assert row.change_type == "docs"


@requires_db
def test_pr_classify_records_module_dimension(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(
            4, title="fix: bug in worker",
            files=[{"path": "src/server/worker/x.py", "op": "modified", "additions": 5, "deletions": 2}],
        ))
    config = _config(modules=[{"name": "server", "path_globs": ["src/server/**"]}])
    with session_scope() as s:
        pr_classify(s, catalog, config, {"artifact_id": f"{REPO_ID}:pr:4"})
    with session_scope() as s:
        row = s.execute(catalog.get("pr_class", "get_pr_classification"),
                        {"artifact_id": f"{REPO_ID}:pr:4"}).one()
    assert row.module == "server"
    assert "module" in row.features_digest
