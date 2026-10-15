"""P10: `synthetic_load.py` against a real database. Skipped unless `TALIUM_TEST_DSN` is set
AND its database name ends with `_synth` (plan §5 P10) -- the same posture as the D13 guard
itself, so this test file can never accidentally point at a shared database.
`small_org_dataset` comes from `tests/server/conftest.py` (shared with `test_synthetic_smoke.py`,
P11)."""
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from server.storage.dbm import operations
from server.storage.engine import dispose_engine, get_engine, session_scope
from server.storage.synthetic_load import SyntheticLoadError, load_dataset

TEST_DSN = os.environ.get("TALIUM_TEST_DSN")
_synth_ready = TEST_DSN is not None and (make_url(TEST_DSN).database or "").endswith("_synth")
requires_synth_db = pytest.mark.skipif(
    not _synth_ready, reason="set TALIUM_TEST_DSN to a *_synth database to run this test")


def _row_count(dataset_dir: Path, name: str) -> int:
    path = dataset_dir / name
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text().splitlines() if line.strip())


@requires_synth_db
def test_load_with_replace_matches_file_counts(small_org_dataset):
    operations.init(__import__("server.storage.config", fromlist=["StorageConfig"])
                    .StorageConfig(dsn=TEST_DSN), force=True)
    result = load_dataset(small_org_dataset, TEST_DSN, replace=True, dry_run=False)
    assert result.rollup_ok is True
    assert result.row_counts["git_artifacts"] == _row_count(small_org_dataset, "git_artifacts.jsonl")
    assert result.row_counts["sessions"] == _row_count(small_org_dataset, "sessions.jsonl")
    assert result.row_counts["session_info"] == _row_count(small_org_dataset, "session_info.jsonl")
    assert result.row_counts["session_git_links"] == _row_count(small_org_dataset, "session_git_links.jsonl")
    assert result.row_counts["pr_issue_links"] == _row_count(small_org_dataset, "pr_issue_links.jsonl")
    assert result.admin_viewer_id is not None


@requires_synth_db
def test_reload_without_replace_is_idempotent(small_org_dataset):
    first = load_dataset(small_org_dataset, TEST_DSN, replace=True, dry_run=False)
    second = load_dataset(small_org_dataset, TEST_DSN, replace=False, dry_run=False)
    assert first.row_counts == second.row_counts
    assert second.rollup_ok is True


@requires_synth_db
def test_pr_classification_one_row_per_pr_and_rework_set_on_merged(small_org_dataset):
    load_dataset(small_org_dataset, TEST_DSN, replace=True, dry_run=False)
    from server.storage.config import StorageConfig

    get_engine(StorageConfig(dsn=TEST_DSN))
    try:
        with session_scope() as s:
            pr_count = s.execute(text(
                "SELECT count(*) FROM git_artifacts WHERE kind = 'pr'")).scalar_one()
            class_count = s.execute(text("SELECT count(*) FROM pr_classification")).scalar_one()
            assert class_count == pr_count

            merged_with_rework = s.execute(text(
                "SELECT count(*) FROM git_artifacts a JOIN pr_classification c "
                "USING (artifact_id) WHERE a.kind = 'pr' AND a.state = 'merged' "
                "AND c.rework_ratio IS NOT NULL")).scalar_one()
            merged_total = s.execute(text(
                "SELECT count(*) FROM git_artifacts WHERE kind = 'pr' AND state = 'merged'"
            )).scalar_one()
            assert merged_with_rework == merged_total
            assert merged_total > 0
    finally:
        dispose_engine()


@requires_synth_db
def test_admin_viewer_id_resolves(small_org_dataset):
    result = load_dataset(small_org_dataset, TEST_DSN, replace=True, dry_run=False)
    from server.storage.config import StorageConfig

    get_engine(StorageConfig(dsn=TEST_DSN))
    try:
        with session_scope() as s:
            row = s.execute(text("SELECT user_id FROM users WHERE user_id = :uid"),
                           {"uid": result.admin_viewer_id}).first()
            assert row is not None
    finally:
        dispose_engine()


# --------------------------------------------------------------------------------------------
# --forecast-export (T5): the forecast rows ride in the same transaction as the load
# --------------------------------------------------------------------------------------------

def _fresh_database() -> None:
    from server.storage.config import StorageConfig
    operations.init(StorageConfig(dsn=TEST_DSN), force=True)
    dispose_engine()


def _forecast_export(small_org_dataset, tmp_path, name="forecast_export.json",
                     generated_at="2026-10-31T10:00:00Z"):
    from .forecast_export_builder import export_for_dataset, write_export
    doc = export_for_dataset(small_org_dataset, generated_at=generated_at)
    return doc, write_export(tmp_path / name, doc)


def _own_engine():
    from sqlalchemy import create_engine
    return create_engine(TEST_DSN)


def _table_counts(eng, *tables) -> dict:
    with eng.connect() as conn:
        return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in tables}


@requires_synth_db
def test_load_with_a_forecast_export_puts_the_rows_in(small_org_dataset, tmp_path):
    from .forecast_export_builder import dump_forecast_tables, source_payload
    _fresh_database()
    doc, export = _forecast_export(small_org_dataset, tmp_path)
    result = load_dataset(small_org_dataset, TEST_DSN, replace=True, dry_run=False,
                          forecast_export=export)
    n_scopes = len(doc["scopes"])
    assert result.rollup_ok is True
    assert n_scopes >= 3                                    # org + the dataset's teams + repos
    assert result.row_counts["forecast_runs"] == 1
    assert result.row_counts["forecast_scope_series"] == n_scopes * 2
    assert result.forecast_run_id is not None

    eng = _own_engine()
    try:
        dump = dump_forecast_tables(eng)
    finally:
        eng.dispose()
    assert [r[0] for r in dump["runs"]] == [result.forecast_run_id]
    assert len(dump["series"]) == n_scopes * 2
    for _, scope_type, scope_id, preset_id, status, payload in dump["series"]:
        assert status == "complete"
        assert payload == source_payload(doc, scope_type, scope_id, preset_id)


@requires_synth_db
def test_load_without_an_export_reports_zero_forecast_rows_and_adds_none(small_org_dataset):
    _fresh_database()
    result = load_dataset(small_org_dataset, TEST_DSN, replace=True, dry_run=False)
    assert result.row_counts["forecast_runs"] == 0
    assert result.row_counts["forecast_scope_series"] == 0
    assert result.forecast_run_id is None


@requires_synth_db
def test_a_load_without_an_export_leaves_an_existing_run_alone_and_replace_clears_it(
        small_org_dataset, tmp_path):
    _fresh_database()
    _, export = _forecast_export(small_org_dataset, tmp_path)
    loaded = load_dataset(small_org_dataset, TEST_DSN, replace=True, dry_run=False,
                          forecast_export=export)

    kept = load_dataset(small_org_dataset, TEST_DSN, replace=False, dry_run=False)
    assert kept.row_counts["forecast_runs"] == 1                          # untouched
    assert kept.row_counts["forecast_scope_series"] == loaded.row_counts["forecast_scope_series"]

    cleared = load_dataset(small_org_dataset, TEST_DSN, replace=True, dry_run=False)
    assert cleared.row_counts["forecast_runs"] == 0                       # --replace truncates it
    assert cleared.row_counts["forecast_scope_series"] == 0


@requires_synth_db
def test_reloading_with_a_second_export_leaves_exactly_one_run(small_org_dataset, tmp_path):
    from .forecast_export_builder import dump_forecast_tables
    _fresh_database()
    _, first = _forecast_export(small_org_dataset, tmp_path, "a.json")
    _, second = _forecast_export(small_org_dataset, tmp_path, "b.json",
                                 generated_at="2026-10-31T11:00:00Z")
    a = load_dataset(small_org_dataset, TEST_DSN, replace=True, dry_run=False, forecast_export=first)
    b = load_dataset(small_org_dataset, TEST_DSN, replace=False, dry_run=False, forecast_export=second)
    assert a.forecast_run_id != b.forecast_run_id
    assert b.row_counts["forecast_runs"] == 1
    eng = _own_engine()
    try:
        dump = dump_forecast_tables(eng)
    finally:
        eng.dispose()
    assert [r[0] for r in dump["runs"]] == [b.forecast_run_id]
    assert {r[0] for r in dump["series"]} == {b.forecast_run_id}


@requires_synth_db
def test_a_foreign_export_is_refused_and_the_database_is_untouched(small_org_dataset, tmp_path):
    from .forecast_export_builder import dump_forecast_tables, make_export, write_export
    _fresh_database()
    _, good = _forecast_export(small_org_dataset, tmp_path)
    load_dataset(small_org_dataset, TEST_DSN, replace=True, dry_run=False, forecast_export=good)
    eng = _own_engine()
    try:
        before_tables = _table_counts(eng, "sessions", "session_info", "git_artifacts", "budgets")
        before_forecast = dump_forecast_tables(eng)
        assert before_tables["sessions"] > 0

        foreign = write_export(tmp_path / "foreign.json", make_export(
            [("org", "default", "o"), ("team", "ghost-team", "Ghost")]))
        # replace=True would truncate everything -- the refusal must come before that
        with pytest.raises(SyntheticLoadError, match="team 'ghost-team'"):
            load_dataset(small_org_dataset, TEST_DSN, replace=True, dry_run=False,
                         forecast_export=foreign)
        assert _table_counts(eng, "sessions", "session_info", "git_artifacts",
                             "budgets") == before_tables
        assert dump_forecast_tables(eng) == before_forecast
    finally:
        eng.dispose()


@requires_synth_db
def test_a_rollup_failure_rolls_the_forecast_rows_back_with_the_rest_of_the_load(
        small_org_dataset, tmp_path, monkeypatch):
    """The forecast is written before the rollup check, in the same transaction: when the check
    fails on a fresh database, nothing at all is left -- no sessions, no budgets, no forecast."""
    from collections import Counter
    from server.storage.synthetic_load import RollupMismatchError
    _fresh_database()
    _, export = _forecast_export(small_org_dataset, tmp_path)
    monkeypatch.setattr("server.storage.synthetic_load._expected_rollup_tuples",
                        lambda rows: Counter({("never", "matches"): 1}))
    with pytest.raises(RollupMismatchError):
        load_dataset(small_org_dataset, TEST_DSN, replace=True, dry_run=False,
                     forecast_export=export)
    eng = _own_engine()
    try:
        counts = _table_counts(eng, "forecast_runs", "forecast_scope_series", "sessions",
                               "session_info", "git_artifacts", "budgets", "teams", "users")
    finally:
        eng.dispose()
    assert counts == {t: 0 for t in counts}


@requires_synth_db
def test_the_forecast_is_written_before_the_rollup_check(
        small_org_dataset, tmp_path, monkeypatch):
    """Plan order (T5): the forecast run is written, then the rollup check runs. Both rollback
    tests above pass in either order, so the order itself is pinned here. (The small dataset
    has no budgets, so the budgets step, which precedes both, is not observable here.)"""
    from server.storage import synthetic_load
    _fresh_database()
    _, export = _forecast_export(small_org_dataset, tmp_path)
    calls: list[str] = []

    def spy(name, real):
        def wrapper(*args, **kwargs):
            calls.append(name)
            return real(*args, **kwargs)
        return wrapper

    monkeypatch.setattr(synthetic_load, "replace_run", spy("forecast", synthetic_load.replace_run))
    monkeypatch.setattr(synthetic_load, "_actual_rollup_tuples",
                        spy("rollup", synthetic_load._actual_rollup_tuples))
    load_dataset(small_org_dataset, TEST_DSN, replace=True, dry_run=False, forecast_export=export)
    assert calls.count("forecast") == 1 and calls.count("rollup") == 1
    assert calls.index("forecast") < calls.index("rollup")


@requires_synth_db
def test_a_rollup_failure_keeps_the_previous_forecast_run(small_org_dataset, tmp_path, monkeypatch):
    from collections import Counter
    from .forecast_export_builder import dump_forecast_tables
    from server.storage.synthetic_load import RollupMismatchError
    _fresh_database()
    _, first = _forecast_export(small_org_dataset, tmp_path, "a.json")
    _, second = _forecast_export(small_org_dataset, tmp_path, "b.json",
                                 generated_at="2026-10-31T11:00:00Z")
    loaded = load_dataset(small_org_dataset, TEST_DSN, replace=True, dry_run=False,
                          forecast_export=first)
    eng = _own_engine()
    try:
        before = dump_forecast_tables(eng)
        monkeypatch.setattr("server.storage.synthetic_load._expected_rollup_tuples",
                            lambda rows: Counter({("never", "matches"): 1}))
        with pytest.raises(RollupMismatchError):
            load_dataset(small_org_dataset, TEST_DSN, replace=False, dry_run=False,
                         forecast_export=second)
        after = dump_forecast_tables(eng)
    finally:
        eng.dispose()
    assert after == before
    assert [r[0] for r in after["runs"]] == [loaded.forecast_run_id]
