import json
from datetime import datetime, timezone

from sqlalchemy import text

from server.storage.engine import session_scope
from server.storage.queries import QueryCatalog
from server.storage.schema import Base
from tests.server.storage.conftest import requires_db

QUERIES_DIR = "config/queries"


def test_catalog_loads_pr_class_queries():
    catalog = QueryCatalog.load(QUERIES_DIR)
    for name in [
        "upsert_session_git_link", "sessions_for_artifact", "links_for_session",
        "session_commit_shas", "commit_artifacts_for_shas", "owning_prs_for_shas",
        "upsert_pr_classification", "get_pr_classification", "attribution_rows_for_repo",
    ]:
        assert catalog.get("pr_class", name) is not None


@requires_db
def test_upsert_session_git_link_idempotent_on_replay(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    params = {
        "session_id": "s1", "artifact_id": "github:1:commit:" + "a" * 40,
        "method": "sha", "confidence": 1.0, "signals": json.dumps({"sha": "a" * 40}),
    }
    with session_scope() as s:
        s.execute(catalog.get("pr_class", "upsert_session_git_link"), params)
    with session_scope() as s:
        s.execute(catalog.get("pr_class", "upsert_session_git_link"), params)   # replay
    with session_scope() as s:
        rows = s.execute(text("SELECT count(*) FROM session_git_links")).scalar()
    assert rows == 1


@requires_db
def test_upsert_session_git_link_reclassifies_method_on_a_later_stronger_match(engine):
    """A logic fix / re-run can UPGRADE a link's method (e.g. heuristic -> sha once the plugin
    lands) -- plain replace-on-conflict, no COALESCE needed here since this table is entirely
    computed by this module (design §7)."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    base = {"session_id": "s1", "artifact_id": "github:1:commit:" + "a" * 40, "signals": json.dumps({})}
    with session_scope() as s:
        s.execute(catalog.get("pr_class", "upsert_session_git_link"),
                 {**base, "method": "heuristic", "confidence": 0.4})
    with session_scope() as s:
        s.execute(catalog.get("pr_class", "upsert_session_git_link"),
                 {**base, "method": "sha", "confidence": 1.0})
    with session_scope() as s:
        row = s.execute(text("SELECT method, confidence FROM session_git_links")).one()
    assert row.method == "sha"
    assert float(row.confidence) == 1.0


@requires_db
def test_sessions_for_artifact_and_links_for_session_are_reverse_views(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    artifact_id = "github:1:pr:5"
    with session_scope() as s:
        s.execute(catalog.get("pr_class", "upsert_session_git_link"), {
            "session_id": "s1", "artifact_id": artifact_id, "method": "sha",
            "confidence": 1.0, "signals": json.dumps({}),
        })
        s.execute(catalog.get("pr_class", "upsert_session_git_link"), {
            "session_id": "s2", "artifact_id": artifact_id, "method": "sha",
            "confidence": 1.0, "signals": json.dumps({}),
        })
    with session_scope() as s:
        sessions = {r.session_id for r in s.execute(
            catalog.get("pr_class", "sessions_for_artifact"), {"artifact_id": artifact_id}
        )}
        links = list(s.execute(catalog.get("pr_class", "links_for_session"), {"session_id": "s1"}))
    assert sessions == {"s1", "s2"}
    assert len(links) == 1
    assert links[0].artifact_id == artifact_id


def _artifact_row(sha: str, commit_shas: list[str], repo_id="github:1", merged=True):
    now = datetime.now(timezone.utc)
    return {
        "artifact_id": f"{repo_id}:pr:1", "kind": "pr", "repo_id": repo_id, "org_id": "org",
        "number": 1, "sha": sha, "author_login": None, "author_email": None, "user_id": None,
        "is_bot": False, "state": "merged" if merged else "open", "title": "t",
        "branch": "b", "base_branch": "main", "created_at": now.isoformat(),
        "updated_at": now.isoformat(), "closed_at": now.isoformat() if merged else None,
        "merged_at": now.isoformat() if merged else None, "stats": None, "files": None,
        "parent_artifact_id": None, "labels": json.dumps([]), "milestone": None,
        "linked_issue": None, "merge_commit_sha": None, "before_sha": None, "after_sha": None,
        "integration_method": None, "reverts_sha": None,
        "commit_shas": json.dumps(commit_shas), "info": json.dumps({}),
    }


@requires_db
def test_owning_prs_for_shas_finds_prs_by_commit_membership(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    sha = "a" * 40
    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_artifact"), _artifact_row(sha, [sha]))
    with session_scope() as s:
        rows = list(s.execute(catalog.get("pr_class", "owning_prs_for_shas"), {"shas": [sha]}))
    assert len(rows) == 1
    assert rows[0].artifact_id == "github:1:pr:1"


@requires_db
def test_owning_prs_for_shas_excludes_unmerged_prs(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    sha = "b" * 40
    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_artifact"), _artifact_row(sha, [sha], merged=False))
    with session_scope() as s:
        rows = list(s.execute(catalog.get("pr_class", "owning_prs_for_shas"), {"shas": [sha]}))
    assert rows == []


@requires_db
def test_upsert_pr_classification_preserves_rework_ratio_set_by_sibling_module(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    artifact_id = "github:1:pr:1"
    params = {
        "artifact_id": artifact_id, "change_type": "feature", "change_confidence": 0.9,
        "change_tier": 0, "artifact_class": "coding", "is_revert": False, "is_recall": False,
        "recall_reason": None, "recall_confidence": None, "module": None, "rework_ratio": None,
        "features_digest": json.dumps({}), "analyzer": "heuristic",
    }
    with session_scope() as s:
        s.execute(catalog.get("pr_class", "upsert_pr_classification"), params)
    # The sibling rework module writes rework_ratio directly (this module never computes it).
    with session_scope() as s:
        s.execute(text("UPDATE pr_classification SET rework_ratio = 0.35 WHERE artifact_id = :id"),
                 {"id": artifact_id})
    # A classifier re-run (e.g. a logic fix) must not wipe out that value.
    with session_scope() as s:
        s.execute(catalog.get("pr_class", "upsert_pr_classification"),
                 {**params, "change_confidence": 0.95})
    with session_scope() as s:
        row = s.execute(catalog.get("pr_class", "get_pr_classification"), {"artifact_id": artifact_id}).one()
    assert float(row.rework_ratio) == 0.35
    assert float(row.change_confidence) == 0.95


@requires_db
def test_upsert_pr_classification_idempotent_on_replay(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    params = {
        "artifact_id": "github:1:pr:2", "change_type": "docs", "change_confidence": 1.0,
        "change_tier": 0, "artifact_class": "documentation", "is_revert": False,
        "is_recall": False, "recall_reason": None, "recall_confidence": None, "module": None,
        "rework_ratio": None, "features_digest": json.dumps({"doc_fraction": 1.0}),
        "analyzer": "heuristic",
    }
    with session_scope() as s:
        s.execute(catalog.get("pr_class", "upsert_pr_classification"), params)
    with session_scope() as s:
        s.execute(catalog.get("pr_class", "upsert_pr_classification"), params)
    with session_scope() as s:
        count = s.execute(text("SELECT count(*) FROM pr_classification")).scalar()
        row = s.execute(catalog.get("pr_class", "get_pr_classification"),
                        {"artifact_id": "github:1:pr:2"}).one()
    assert count == 1
    assert row.change_type == "docs"
    assert row.features_digest == {"doc_fraction": 1.0}
