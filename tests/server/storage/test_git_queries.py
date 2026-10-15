import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from server.storage.engine import session_scope
from server.storage.queries import QueryCatalog
from server.storage.schema import Base
from tests.server.storage.conftest import requires_db

QUERIES_DIR = Path("config/queries")


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _artifact_params(**overrides) -> dict:
    now = datetime.now(timezone.utc)
    params = {
        "artifact_id": "github:1:pr:1", "kind": "pr", "repo_id": "github:1", "org_id": "org1",
        "number": 1, "sha": "a" * 40, "author_login": "octocat", "author_email": None,
        "user_id": None, "is_bot": False, "state": "open", "title": "t", "branch": "b",
        "base_branch": "main", "created_at": _iso(now), "updated_at": _iso(now),
        "closed_at": None, "merged_at": None, "stats": json.dumps({}), "files": json.dumps([]),
        "parent_artifact_id": None, "labels": json.dumps([]), "milestone": None,
        "linked_issue": None, "merge_commit_sha": None, "before_sha": None, "after_sha": None,
        "integration_method": None, "reverts_sha": None, "commit_shas": json.dumps([]),
        "info": json.dumps({}),
    }
    params.update(overrides)
    return params


def test_catalog_loads_git_queries():
    catalog = QueryCatalog.load(QUERIES_DIR)
    for name in [
        "land_delivery", "claim_delivery", "upsert_artifact", "upsert_repo",
        "upsert_installation", "prune_deliveries", "coverage_counts",
    ]:
        assert catalog.get("git", name) is not None


@requires_db
def test_land_delivery_dedups_on_delivery_id(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    params = {
        "delivery_id": "d1", "event": "pull_request", "action": "opened",
        "installation_id": 42, "repo_num": 1, "payload": json.dumps({"a": 1}),
    }
    with session_scope() as s:
        first = s.execute(catalog.get("git", "land_delivery"), params).fetchall()
        second = s.execute(catalog.get("git", "land_delivery"), params).fetchall()
    assert len(first) == 1        # newly landed
    assert len(second) == 0       # already had it -- redelivery is a no-op
    with session_scope() as s:
        rows = s.execute(text("SELECT count(*) FROM git_deliveries WHERE delivery_id = 'd1'")).scalar()
        assert rows == 1


@requires_db
def test_upsert_artifact_idempotent_same_payload_twice(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    params = _artifact_params()
    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_artifact"), params)
    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_artifact"), params)
    with session_scope() as s:
        rows = s.execute(
            text("SELECT title, state FROM git_artifacts WHERE artifact_id = :id"),
            {"id": params["artifact_id"]},
        ).all()
        assert len(rows) == 1
        assert rows[0].title == "t"
        assert rows[0].state == "open"


@requires_db
def test_upsert_artifact_out_of_order_older_update_never_wins(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    older = datetime.now(timezone.utc)
    newer = older + timedelta(minutes=5)

    newer_params = _artifact_params(state="merged", title="newer", updated_at=_iso(newer))
    older_params = _artifact_params(state="open", title="older", updated_at=_iso(older))

    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_artifact"), newer_params)   # newer arrives first
    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_artifact"), older_params)  # older arrives second (backfill)

    with session_scope() as s:
        row = s.execute(
            text("SELECT title, state FROM git_artifacts WHERE artifact_id = :id"),
            {"id": newer_params["artifact_id"]},
        ).one()
        # The older, out-of-order write must never overwrite the newer state.
        assert row.title == "newer"
        assert row.state == "merged"


@requires_db
def test_upsert_repo_and_installation_upsert_on_conflict(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    repo_params = {
        "repo_id": "github:1", "repo_num": 1, "full_name": "acme/repo", "owner": "acme",
        "default_branch": "main", "private": False, "installation_id": 42, "org_id": "org1",
        "info": json.dumps({}),
    }
    inst_params = {
        "installation_id": 42, "org_id": "org1", "account_login": "acme",
        "account_type": "Organization", "repo_selection": "all", "state": "active",
        "info": json.dumps({}),
    }
    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_repo"), repo_params)
        s.execute(catalog.get("git", "upsert_installation"), inst_params)
    # Second call is a metadata refresh, e.g. a rename -- must update in place, not duplicate.
    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_repo"), {**repo_params, "full_name": "acme/renamed"})
        s.execute(catalog.get("git", "upsert_installation"), {**inst_params, "state": "suspended"})
    with session_scope() as s:
        repo = s.execute(text("SELECT full_name FROM git_repos WHERE repo_id = 'github:1'")).one()
        inst = s.execute(text("SELECT state FROM git_installations WHERE installation_id = 42")).one()
        assert repo.full_name == "acme/renamed"
        assert inst.state == "suspended"
        count = s.execute(text("SELECT count(*) FROM git_repos")).scalar()
        assert count == 1


@requires_db
def test_prune_deliveries_only_removes_done_and_unmapped(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    old = datetime.now(timezone.utc) - timedelta(days=20)
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)

    def _land(delivery_id, status):
        with session_scope() as s:
            s.execute(text(
                "INSERT INTO git_deliveries "
                "(delivery_id, event, action, installation_id, repo_num, payload, status, "
                " attempts, received_at, mapped_at) "
                "VALUES (:id, 'push', NULL, NULL, NULL, '{}'::jsonb, :status, 0, :received_at, NULL)"
            ), {"id": delivery_id, "status": status, "received_at": old})

    _land("done-1", "done")
    _land("unmapped-1", "unmapped")
    _land("pending-1", "pending")   # must survive -- not yet processed
    _land("failed-1", "failed")     # must survive -- needs a mapper fix-and-replay

    with session_scope() as s:
        s.execute(catalog.get("git", "prune_deliveries"), {"cutoff": _iso(cutoff)})

    with session_scope() as s:
        remaining = {r.delivery_id for r in s.execute(text("SELECT delivery_id FROM git_deliveries"))}
    assert remaining == {"pending-1", "failed-1"}


@requires_db
def test_coverage_counts_reports_attributed_and_bot_prs(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_artifact"), _artifact_params(
            artifact_id="github:1:pr:1", user_id="u1", is_bot=False,
        ))
        s.execute(catalog.get("git", "upsert_artifact"), _artifact_params(
            artifact_id="github:1:pr:2", user_id=None, is_bot=False,
        ))
        s.execute(catalog.get("git", "upsert_artifact"), _artifact_params(
            artifact_id="github:1:pr:3", user_id=None, is_bot=True, author_login="dependabot[bot]",
        ))
    with session_scope() as s:
        row = s.execute(catalog.get("git", "coverage_counts")).one()
    assert row.repo_id == "github:1"
    assert row.pr_count == 3
    assert row.pr_attributed == 1
    assert row.pr_bot == 1
