"""The G1 migration applies clean up and down (plan G1 "Done when"). Skips without a DSN."""
from sqlalchemy import inspect, text

from alembic import command
from server.storage.dbm.operations import alembic_config
from server.storage.schema import Base
from tests.server.directory.conftest import requires_db

DIRECTORY_TABLES = {"identity_links", "teams", "team_members", "role_assignments"}
DIRECTORY_USER_COLS = {"display_name", "status", "primary_team_id", "source_provider"}
# the revision immediately below this module's -- the causal-graph migration.
_PRIOR_REVISION = "b7f2a9c14e30"


@requires_db
def test_directory_migration_up_down(engine, cfg):
    Base.metadata.drop_all(engine)
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        conn.commit()

    acfg = alembic_config(cfg)
    try:
        command.upgrade(acfg, "head")
        insp = inspect(engine)
        assert DIRECTORY_TABLES <= set(insp.get_table_names())
        assert DIRECTORY_USER_COLS <= {c["name"] for c in insp.get_columns("users")}

        # down one revision: the five directory objects and the user columns are gone,
        # and nothing the causal-graph baseline owns is touched.
        command.downgrade(acfg, _PRIOR_REVISION)
        insp = inspect(engine)
        names = set(insp.get_table_names())
        assert not (DIRECTORY_TABLES & names)
        assert "session_graph" in names  # the prior revision's table survives
        assert not (DIRECTORY_USER_COLS & {c["name"] for c in insp.get_columns("users")})

        command.upgrade(acfg, "head")
        assert DIRECTORY_TABLES <= set(inspect(engine).get_table_names())
    finally:
        # leave the DB in the plain create_all state every other directory test expects.
        command.downgrade(acfg, "base")
        with engine.connect() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            conn.commit()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
