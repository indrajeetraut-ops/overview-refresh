"""The F1 migration applies clean up and down (plan F1 "Verify: migration applies"), and the
cost-based-seats migration (c4e8a2f1d7b3) drops the token `allowances` table and restores it on
downgrade."""
from sqlalchemy import inspect, text

from alembic import command
from server.storage.dbm.operations import alembic_config
from server.storage.schema import Base

from .conftest import requires_db

_PRIOR_REVISION = "a1f4c7e29b05"       # before F1: neither budgets nor allowances
_BEFORE_DROP = "b7c31f9a4e02"          # F1 applied, allowances not yet dropped


def _tables(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


@requires_db
def test_budgets_migration_up_down(engine, storage_config):
    Base.metadata.drop_all(engine)
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        conn.commit()

    acfg = alembic_config(storage_config)
    try:
        command.upgrade(acfg, "head")
        assert "budgets" in _tables(engine)
        assert "allowances" not in _tables(engine)   # dropped at head

        command.downgrade(acfg, _BEFORE_DROP)
        assert {"budgets", "allowances"} <= _tables(engine)   # the drop is reversible

        command.downgrade(acfg, _PRIOR_REVISION)
        names = _tables(engine)
        assert not ({"budgets", "allowances"} & names)
        assert "teams" in names  # the prior revision's tables survive

        command.upgrade(acfg, "head")
        assert "budgets" in _tables(engine)
        assert "allowances" not in _tables(engine)
    finally:
        with engine.connect() as conn:
            conn.execute(text("DROP TABLE IF EXISTS allowances"))
            conn.commit()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
