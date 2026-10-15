from sqlalchemy import inspect, text

from alembic import command
from server.storage.dbm.operations import alembic_config
from server.storage.schema import Base
from tests.server.storage.conftest import requires_db

GIT_TABLES = {"git_installations", "git_repos", "git_artifacts", "git_deliveries"}
ALL_GIT_TABLES = GIT_TABLES | {"git_pending_installs", "git_artifact_events"}
# The revision immediately above the pre-git baseline (A1) -- downgrading to this exact id,
# rather than a relative "-N", stays correct regardless of how many git-source migrations
# (A1's four tables, A7's git_pending_installs, ...) accumulate on top of it.
_BASELINE_REVISION = "ee112ee5c076"


def test_git_tables_declared_with_expected_columns():
    tables = Base.metadata.tables
    assert GIT_TABLES <= set(tables)

    assert set(tables["git_installations"].columns.keys()) == {
        "installation_id", "org_id", "account_login", "account_type", "repo_selection",
        "state", "installed_at", "updated_at", "info",
    }
    assert set(tables["git_repos"].columns.keys()) == {
        "repo_id", "repo_num", "full_name", "owner", "default_branch", "private",
        "installation_id", "org_id", "backfill_cursor", "backfill_state", "first_seen_at",
        "updated_at", "info",
    }
    assert set(tables["git_artifacts"].columns.keys()) == {
        "artifact_id", "kind", "repo_id", "org_id", "number", "sha", "author_login",
        "author_email", "user_id", "is_bot", "state", "title", "branch", "base_branch",
        "created_at", "updated_at", "closed_at", "merged_at", "stats", "files",
        "parent_artifact_id", "labels", "milestone", "linked_issue", "merge_commit_sha",
        "before_sha", "after_sha", "integration_method", "reverts_sha", "commit_shas", "info",
    }
    assert set(tables["git_deliveries"].columns.keys()) == {
        "delivery_id", "event", "action", "installation_id", "repo_num", "payload", "status",
        "attempts", "received_at", "mapped_at",
    }
    # Track churn/reopen: the append-only PR transition log (a reopen COUNT's only source,
    # since git_artifacts.state is current-state-only).
    assert set(tables["git_artifact_events"].columns.keys()) == {
        "event_id", "artifact_id", "action", "to_state", "occurred_at", "source_key",
        "recorded_at",
    }


def test_git_installations_id_is_not_autoincrement():
    # GitHub's own numeric installation id, supplied by the mapper -- a single-column integer
    # PK defaults to autoincrement otherwise, which would silently ignore the caller's id.
    col = Base.metadata.tables["git_installations"].columns["installation_id"]
    assert col.autoincrement is False


def test_git_pending_installs_declared_with_expected_columns():
    cols = Base.metadata.tables["git_pending_installs"].columns
    assert set(cols.keys()) == {"installation_id", "org_id", "created_at"}
    assert cols["installation_id"].autoincrement is False


def test_git_artifacts_and_deliveries_indexes_per_spec():
    art_idx = {ix.name for ix in Base.metadata.tables["git_artifacts"].indexes}
    assert {
        "ix_git_artifacts_sha", "ix_git_artifacts_author_login", "ix_git_artifacts_user_id",
        "ix_git_artifacts_merged_at", "ix_git_artifacts_parent_artifact_id",
        "ix_git_artifacts_merge_commit_sha", "ix_git_artifacts_reverts_sha",
        "ix_git_artifacts_repo_kind_updated",
    } <= art_idx

    composite = next(
        ix for ix in Base.metadata.tables["git_artifacts"].indexes
        if ix.name == "ix_git_artifacts_repo_kind_updated"
    )
    assert [c.name for c in composite.columns] == ["repo_id", "kind", "updated_at"]

    del_idx = {ix.name for ix in Base.metadata.tables["git_deliveries"].indexes}
    assert "ix_git_deliveries_status_received" in del_idx
    composite = next(
        ix for ix in Base.metadata.tables["git_deliveries"].indexes
        if ix.name == "ix_git_deliveries_status_received"
    )
    assert [c.name for c in composite.columns] == ["status", "received_at"]


@requires_db
def test_migration_up_down_creates_and_drops_exactly_the_git_tables(engine, cfg):
    Base.metadata.drop_all(engine)
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        conn.commit()

    acfg = alembic_config(cfg)
    try:
        command.upgrade(acfg, "head")
        names = set(inspect(engine).get_table_names())
        assert ALL_GIT_TABLES <= names

        # Downgrading to the exact pre-git baseline must remove every git-source table (A1's
        # four plus A7's git_pending_installs) and nothing the baseline itself created.
        command.downgrade(acfg, _BASELINE_REVISION)
        names = set(inspect(engine).get_table_names())
        assert not (ALL_GIT_TABLES & names)
        assert "sessions" in names  # baseline's tables survive

        command.upgrade(acfg, "head")
        names = set(inspect(engine).get_table_names())
        assert ALL_GIT_TABLES <= names
    finally:
        # Leave the DB in the plain create_all state every other storage test expects.
        command.downgrade(acfg, "base")
        with engine.connect() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            conn.commit()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
