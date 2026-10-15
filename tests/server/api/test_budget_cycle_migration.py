"""The budget_cycle migration (f3a8d1c6b947) applies and reverses cleanly, and leaves `budgets`
alone either way."""
from sqlalchemy import inspect, text

from alembic import command
from server.storage.dbm.operations import alembic_config
from server.storage.schema import Base

from .conftest import requires_db

_PRIOR_REVISION = "231990ad3837"       # before budget_cycle


def _tables(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


@requires_db
def test_budget_cycle_migration_up_down(engine, storage_config):
    Base.metadata.drop_all(engine)
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        conn.commit()

    acfg = alembic_config(storage_config)
    try:
        command.upgrade(acfg, _PRIOR_REVISION)
        assert "budget_cycle" not in _tables(engine)
        assert "budgets" in _tables(engine)

        command.upgrade(acfg, "head")
        assert "budget_cycle" in _tables(engine)
        cols = {c["name"]: c for c in inspect(engine).get_columns("budget_cycle")}
        assert set(cols) == {"org_id", "kind", "recurrence", "sprint_weeks", "anchor_date",
                             "anchor_number", "custom_end", "updated_at"}
        assert not cols["org_id"]["nullable"] and not cols["kind"]["nullable"]
        assert not cols["recurrence"]["nullable"] and not cols["updated_at"]["nullable"]
        assert cols["sprint_weeks"]["nullable"] and cols["custom_end"]["nullable"]
        assert inspect(engine).get_pk_constraint("budget_cycle")["constrained_columns"] == ["org_id"]

        command.downgrade(acfg, _PRIOR_REVISION)
        assert "budget_cycle" not in _tables(engine)
        assert "budgets" in _tables(engine)       # untouched by this revision

        command.upgrade(acfg, "head")
        assert "budget_cycle" in _tables(engine)
    finally:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
