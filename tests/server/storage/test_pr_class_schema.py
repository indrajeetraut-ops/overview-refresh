from sqlalchemy import inspect, text

from alembic import command
from server.storage.dbm.operations import alembic_config
from server.storage.schema import Base
from tests.server.storage.conftest import requires_db

PR_CLASS_TABLES = {"session_git_links", "pr_classification"}
# The revision immediately below this module's own (B1's, on top of A7's git_pending_installs)
# -- downgrading to this exact id, not a relative "-N", stays correct regardless of what lands
# on top of it later.
_PRE_B1_REVISION = "2e4db06ce3e2"


def test_tables_declared_with_expected_columns():
    tables = Base.metadata.tables
    assert PR_CLASS_TABLES <= set(tables)

    assert set(tables["session_git_links"].columns.keys()) == {
        "session_id", "artifact_id", "method", "confidence", "signals", "created_at",
    }
    assert set(tables["pr_classification"].columns.keys()) == {
        "artifact_id", "change_type", "change_confidence", "change_tier", "artifact_class",
        "is_revert", "is_recall", "recall_reason", "recall_confidence", "module",
        "rework_ratio", "features_digest", "analyzer", "classified_at",
    }


def test_session_git_links_composite_primary_key():
    pk_cols = {c.name for c in Base.metadata.tables["session_git_links"].primary_key.columns}
    assert pk_cols == {"session_id", "artifact_id"}


def test_indexes_per_spec():
    link_idx = {ix.name for ix in Base.metadata.tables["session_git_links"].indexes}
    assert "ix_session_git_links_artifact_id" in link_idx

    class_idx = {ix.name for ix in Base.metadata.tables["pr_classification"].indexes}
    assert {
        "ix_pr_classification_change_type", "ix_pr_classification_artifact_class",
        "ix_pr_classification_module",
    } <= class_idx


def test_no_foreign_keys():
    assert not Base.metadata.tables["session_git_links"].foreign_keys
    assert not Base.metadata.tables["pr_classification"].foreign_keys


@requires_db
def test_migration_up_down_creates_and_drops_exactly_these_tables(engine, cfg):
    Base.metadata.drop_all(engine)
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        conn.commit()

    acfg = alembic_config(cfg)
    try:
        command.upgrade(acfg, "head")
        names = set(inspect(engine).get_table_names())
        assert PR_CLASS_TABLES <= names

        command.downgrade(acfg, _PRE_B1_REVISION)
        names = set(inspect(engine).get_table_names())
        assert not (PR_CLASS_TABLES & names)
        assert "git_artifacts" in names   # the layer below survives

        command.upgrade(acfg, "head")
        names = set(inspect(engine).get_table_names())
        assert PR_CLASS_TABLES <= names
    finally:
        command.downgrade(acfg, "base")
        with engine.connect() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            conn.commit()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
