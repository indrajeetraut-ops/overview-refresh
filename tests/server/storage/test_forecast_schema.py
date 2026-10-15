"""T5: the forecast tables (plan §3.5): models, migration `231990ad3837` up and down, and no
model drift after `migrate`."""
import pytest
from sqlalchemy import Date, DateTime, String, inspect, text
from sqlalchemy.dialects.postgresql import JSONB

from alembic import command
from server.storage import cli_db
from server.storage.dbm import operations
from server.storage.dbm.operations import alembic_config
from server.storage.engine import dispose_engine
from server.storage.schema import Base, ForecastRun, ForecastScopeSeries

from .conftest import requires_db

_PREVIOUS_HEAD = "e1a4c9f83b26"      # session_info scalar columns -- the head before this revision
_THIS_REVISION = "231990ad3837"
_TABLES = {"forecast_runs", "forecast_scope_series"}


def _cols(table):
    return {c.name: c for c in Base.metadata.tables[table].columns}


# ----------------------------------------------------------------------------- metadata (no DB)

def test_models_are_registered_on_the_shared_base():
    assert ForecastRun.__table__ is Base.metadata.tables["forecast_runs"]
    assert ForecastScopeSeries.__table__ is Base.metadata.tables["forecast_scope_series"]


def test_forecast_runs_columns_types_and_key():
    cols = _cols("forecast_runs")
    assert list(cols) == ["run_id", "origin", "data_through", "generated_at", "meta", "loaded_at"]
    assert isinstance(cols["run_id"].type, String)
    assert isinstance(cols["origin"].type, Date)
    assert isinstance(cols["data_through"].type, Date)
    for name in ("generated_at", "loaded_at"):
        assert isinstance(cols[name].type, DateTime) and cols[name].type.timezone is True
    assert isinstance(cols["meta"].type, JSONB)
    assert all(c.nullable is False for c in cols.values())
    assert [c.name for c in Base.metadata.tables["forecast_runs"].primary_key.columns] == ["run_id"]


def test_forecast_scope_series_columns_types_and_composite_key():
    cols = _cols("forecast_scope_series")
    assert list(cols) == ["run_id", "scope_type", "scope_id", "preset_id", "status", "payload"]
    for name in ("run_id", "scope_type", "scope_id", "preset_id", "status"):
        assert isinstance(cols[name].type, String)
    assert isinstance(cols["payload"].type, JSONB)
    assert all(c.nullable is False for c in cols.values())
    pk = [c.name for c in Base.metadata.tables["forecast_scope_series"].primary_key.columns]
    assert pk == ["run_id", "scope_type", "scope_id", "preset_id"]


def test_forecast_tables_have_no_foreign_keys_or_extra_indexes():
    for name in _TABLES:
        table = Base.metadata.tables[name]
        assert not table.foreign_keys
        assert not table.indexes


def _head(cfg) -> str:
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(alembic_config(cfg)).get_current_head()


def test_migration_follows_the_previous_head_and_the_history_has_one_head():
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(alembic_config(
        __import__("server.storage.config", fromlist=["StorageConfig"]).StorageConfig(dsn="x")))
    assert script.get_revision(_THIS_REVISION).down_revision == _PREVIOUS_HEAD
    heads = script.get_heads()
    assert len(heads) == 1                       # a later revision may sit on top of this one
    assert _THIS_REVISION in {r.revision for r in script.walk_revisions()}


# --------------------------------------------------------------------------------- database

def _tables(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _reset_to_nothing(engine) -> None:
    Base.metadata.drop_all(engine)
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        conn.commit()


@requires_db
def test_migration_applies_from_the_previous_head_and_downgrades_cleanly(engine, cfg):
    _reset_to_nothing(engine)
    acfg = alembic_config(cfg)
    try:
        command.upgrade(acfg, _PREVIOUS_HEAD)
        assert not (_TABLES & _tables(engine))

        command.upgrade(acfg, "head")
        assert _TABLES <= _tables(engine)
        insp = inspect(engine)
        runs = {c["name"]: c for c in insp.get_columns("forecast_runs")}
        assert set(runs) == {"run_id", "origin", "data_through", "generated_at", "meta", "loaded_at"}
        assert str(runs["origin"]["type"]) == "DATE"
        assert runs["generated_at"]["type"].timezone is True      # timestamptz
        assert runs["loaded_at"]["type"].timezone is True
        assert str(runs["meta"]["type"]) == "JSONB"
        assert insp.get_pk_constraint("forecast_runs")["constrained_columns"] == ["run_id"]
        series = {c["name"]: c for c in insp.get_columns("forecast_scope_series")}
        assert set(series) == {"run_id", "scope_type", "scope_id", "preset_id", "status", "payload"}
        assert str(series["payload"]["type"]) == "JSONB"
        assert all(c["nullable"] is False for c in [*runs.values(), *series.values()])
        assert insp.get_pk_constraint("forecast_scope_series")["constrained_columns"] == [
            "run_id", "scope_type", "scope_id", "preset_id"]

        command.downgrade(acfg, _PREVIOUS_HEAD)
        remaining = _tables(engine)
        assert not (_TABLES & remaining)
        assert "session_info" in remaining and "alembic_version" in remaining

        command.upgrade(acfg, "head")
        assert _TABLES <= _tables(engine)
    finally:
        operations.init(cfg, force=True)      # leave the database at head, tables and stamp


@requires_db
def test_no_model_drift_after_migrate(engine, cfg):
    _reset_to_nothing(engine)
    try:
        assert operations.migrate(cfg) == "migrated"
        status = operations.status(cfg)
        assert status["revision"] == status["head"]
        assert status["drift"] == []
    finally:
        operations.init(cfg, force=True)


@requires_db
def test_cli_db_status_reports_no_drift_after_migrate(engine, cfg, tmp_path, capsys):
    _reset_to_nothing(engine)
    config = tmp_path / "storage.yaml"
    config.write_text(f"storage:\n  dsn: {cfg.dsn}\n", encoding="utf-8")
    dispose_engine()
    try:
        assert cli_db.main(["--config", str(config), "migrate"]) == 0
        capsys.readouterr()
        assert cli_db.main(["--config", str(config), "status"]) == 0
        out = capsys.readouterr().out
        assert "'drift': []" in out
        assert _head(cfg) in out
    finally:
        dispose_engine()
        operations.init(cfg, force=True)
        dispose_engine()
