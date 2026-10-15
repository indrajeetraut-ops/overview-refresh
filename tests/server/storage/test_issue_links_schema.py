from sqlalchemy import inspect, text

from alembic import command
from server.storage.dbm.operations import alembic_config
from server.storage.schema import Base
from tests.server.storage.conftest import requires_db

# The revision immediately below this module's own (H3's, on top of the directory G1 revision).
_PRE_H3_REVISION = "c4e1a9f27d3b"


def test_pr_issue_links_declared_with_expected_columns():
    cols = Base.metadata.tables["pr_issue_links"].columns
    assert set(cols.keys()) == {
        "pr_artifact_id", "tracker", "project_key", "number", "issue_artifact_id",
        "method", "confidence", "created_at",
    }


def test_pr_issue_links_composite_primary_key_is_the_tracker_agnostic_issue_key_plus_pr():
    pk_cols = {c.name for c in Base.metadata.tables["pr_issue_links"].primary_key.columns}
    assert pk_cols == {"pr_artifact_id", "tracker", "project_key", "number"}


def test_pr_issue_links_reverse_index_per_spec():
    idx = {ix.name: [c.name for c in ix.columns]
           for ix in Base.metadata.tables["pr_issue_links"].indexes}
    assert idx["ix_pr_issue_links_issue"] == ["tracker", "project_key", "number"]
    assert "ix_pr_issue_links_issue_artifact_id" in idx


def test_no_foreign_keys():
    assert not Base.metadata.tables["pr_issue_links"].foreign_keys


@requires_db
def test_migration_up_down_creates_and_drops_pr_issue_links(engine, cfg):
    Base.metadata.drop_all(engine)
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        conn.commit()

    acfg = alembic_config(cfg)
    try:
        command.upgrade(acfg, "head")
        assert "pr_issue_links" in set(inspect(engine).get_table_names())

        command.downgrade(acfg, _PRE_H3_REVISION)
        names = set(inspect(engine).get_table_names())
        assert "pr_issue_links" not in names
        assert "git_artifacts" in names   # the layer below survives

        command.upgrade(acfg, "head")
        assert "pr_issue_links" in set(inspect(engine).get_table_names())
    finally:
        command.downgrade(acfg, "base")
        with engine.connect() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            conn.commit()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
