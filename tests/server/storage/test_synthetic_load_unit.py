"""P10: `synthetic_load.py` unit tests -- no database (plan §5 P10)."""
import json
from pathlib import Path

import pytest

from server.storage.synthetic_load import (
    SyntheticLoadError, _guard_dsn, _normalize_segment_turns, _verify_manifest, load_dataset,
)


# --------------------------------------------------------------------------------------------
# D13 guard
# --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("dsn", [
    "postgresql+psycopg://user@localhost:5433/econ",
    "postgresql+psycopg://user@localhost:5433/econ_synth_x",
    "postgresql+psycopg://user@localhost:5433/",
])
def test_guard_rejects_non_synth_names(dsn):
    with pytest.raises(SyntheticLoadError, match="_synth"):
        _guard_dsn(dsn)


@pytest.mark.parametrize("dsn", [
    "postgresql+psycopg://user@localhost:5433/econ_synth",
    "postgresql+psycopg://user@localhost:5433/talium_test_synth",
])
def test_guard_accepts_synth_names(dsn):
    _guard_dsn(dsn)  # must not raise


def test_guard_rejects_unparseable_dsn():
    with pytest.raises(SyntheticLoadError):
        _guard_dsn("not-a-dsn-at-all")


# --------------------------------------------------------------------------------------------
# Manifest tamper detection
# --------------------------------------------------------------------------------------------

@pytest.fixture
def fake_dataset(tmp_path):
    dataset = tmp_path / "org"
    dataset.mkdir()
    (dataset / "sessions.jsonl").write_text('{"a": 1}\n')
    manifest = {
        "schema_version": 2, "operation": "org", "status": "complete",
        "directory_org_id": "default",
        "files": {"sessions.jsonl": {"rows": 1, "sha256": _sha256_of(dataset / "sessions.jsonl")}},
    }
    (dataset / "manifest.json").write_text(json.dumps(manifest))
    return dataset


def _sha256_of(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_tamper_detected(fake_dataset):
    (fake_dataset / "sessions.jsonl").write_text('{"a": 2}\n')  # content changed, hash now stale
    with pytest.raises(SyntheticLoadError, match="sha256 mismatch"):
        _verify_manifest(fake_dataset)


def test_manifest_schema_1_is_rejected_with_a_regenerate_message(fake_dataset):
    manifest = json.loads((fake_dataset / "manifest.json").read_text())
    manifest["schema_version"] = 1
    (fake_dataset / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(SyntheticLoadError, match="dataset uses schema 1; regenerate with this version"):
        _verify_manifest(fake_dataset)


def test_manifest_unknown_schema_is_rejected(fake_dataset):
    manifest = json.loads((fake_dataset / "manifest.json").read_text())
    manifest["schema_version"] = 3
    (fake_dataset / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(SyntheticLoadError, match="schema_version must be 2"):
        _verify_manifest(fake_dataset)


def test_manifest_wrong_operation_detected(fake_dataset):
    manifest = json.loads((fake_dataset / "manifest.json").read_text())
    manifest["operation"] = "series"
    (fake_dataset / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(SyntheticLoadError, match="operation"):
        _verify_manifest(fake_dataset)


def test_manifest_incomplete_status_detected(fake_dataset):
    manifest = json.loads((fake_dataset / "manifest.json").read_text())
    manifest["status"] = "running"
    (fake_dataset / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(SyntheticLoadError, match="status"):
        _verify_manifest(fake_dataset)


def test_manifest_missing_file_detected(fake_dataset):
    manifest = json.loads((fake_dataset / "manifest.json").read_text())
    manifest["files"]["ghost.jsonl"] = {"rows": 0, "sha256": "x" * 64}
    (fake_dataset / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(SyntheticLoadError, match="missing"):
        _verify_manifest(fake_dataset)


# --------------------------------------------------------------------------------------------
# Org-id mismatch
# --------------------------------------------------------------------------------------------

def test_org_id_mismatch_detected(fake_dataset, tmp_path):
    directory_yaml = tmp_path / "directory.yaml"
    directory_yaml.write_text("directory:\n  default_org_id: some-other-org\n")
    manifest = json.loads((fake_dataset / "manifest.json").read_text())
    with pytest.raises(SyntheticLoadError, match="directory_org_id"):
        load_dataset(fake_dataset, "postgresql+psycopg://u@h:5433/x_synth", replace=False,
                     dry_run=True, directory_config_path=str(directory_yaml))


def test_org_id_match_passes_through_to_dry_run(fake_dataset, tmp_path, monkeypatch):
    directory_yaml = tmp_path / "directory.yaml"
    directory_yaml.write_text("directory:\n  default_org_id: default\n")
    result = load_dataset(fake_dataset, "postgresql+psycopg://u@h:5433/x_synth", replace=False,
                          dry_run=True, directory_config_path=str(directory_yaml))
    assert result.dry_run is True


# --------------------------------------------------------------------------------------------
# Dry-run makes zero DB connections
# --------------------------------------------------------------------------------------------

def test_dry_run_never_calls_get_engine(fake_dataset, tmp_path, monkeypatch):
    directory_yaml = tmp_path / "directory.yaml"
    directory_yaml.write_text("directory:\n  default_org_id: default\n")

    def _boom(*args, **kwargs):
        raise AssertionError("get_engine must not be called during --dry-run")

    monkeypatch.setattr("server.storage.synthetic_load.get_engine", _boom)
    result = load_dataset(fake_dataset, "postgresql+psycopg://u@h:5433/x_synth", replace=False,
                          dry_run=True, directory_config_path=str(directory_yaml))
    assert result.row_counts["sessions.jsonl"] == 1


# --------------------------------------------------------------------------------------------
# Token allowances were removed (docs/plan/2026-09-26-cost-based-seat-utilization.md D3)
# --------------------------------------------------------------------------------------------

# --------------------------------------------------------------------------------------------
# Segment `turns` is a count (api-latency plan D8/Phase 0)
# --------------------------------------------------------------------------------------------

def test_list_turns_normalizes_to_its_length():
    """An already-generated dataset still has the pre-fix list shape; the loader must not
    require regenerating it."""
    row = {"info": {"segments": [{"label": "build", "turns": [3, 5, 7]},
                                 {"label": "review", "turns": 2}]}}
    _normalize_segment_turns(row)
    assert row["info"]["segments"][0]["turns"] == 3
    assert row["info"]["segments"][1]["turns"] == 2  # already a count: untouched


def test_normalize_segment_turns_tolerates_missing_shapes():
    for row in ({}, {"info": {}}, {"info": {"segments": []}}, {"info": None}):
        _normalize_segment_turns(row)  # must not raise


def test_a_pre_removal_dataset_listing_allowances_still_verifies_and_is_not_an_input(
        fake_dataset, tmp_path):
    """Datasets generated before the removal ship (and list) allowances.jsonl. They must keep
    loading, and the loader must no longer treat the file as something it writes."""
    allowances = fake_dataset / "allowances.jsonl"
    allowances.write_text('{"scope_type": "team", "scope_id": "qa", "tokens": 1000}\n')
    manifest = json.loads((fake_dataset / "manifest.json").read_text())
    manifest["files"]["allowances.jsonl"] = {"rows": 1, "sha256": _sha256_of(allowances)}
    (fake_dataset / "manifest.json").write_text(json.dumps(manifest))
    directory_yaml = tmp_path / "directory.yaml"
    directory_yaml.write_text("directory:\n  default_org_id: default\n")

    _verify_manifest(fake_dataset)  # listed + present + hash matches: fine
    result = load_dataset(fake_dataset, "postgresql+psycopg://u@h:5433/x_synth", replace=False,
                          dry_run=True, directory_config_path=str(directory_yaml))
    assert "allowances.jsonl" not in result.row_counts


def test_generated_dataset_is_schema_2_and_rollup_rows_feed_the_per_type_check(small_org_dataset):
    """No DB: the dataset the loader consumes is version 2, and its `expected_rollup.jsonl`
    rows carry every field the loader's per-type tuple reads."""
    manifest = _verify_manifest(small_org_dataset)
    assert manifest["schema_version"] == 2
    rows = [json.loads(line) for line in
            (small_org_dataset / "expected_rollup.jsonl").read_text().splitlines() if line.strip()]
    assert rows
    for row in rows[:50]:
        (row["input"], row["cache_write_5m"] + row["cache_write_1h"], row["cache_read"],
         row["output"])
        assert "input_combined" not in row


def test_daily_rollup_query_selects_input_and_cache_write_separately():
    from pathlib import Path
    import yaml
    path = Path(__file__).resolve().parents[3] / "config" / "queries" / "synthetic.yaml"
    sql = yaml.safe_load(path.read_text())["daily_rollup"]
    assert "AS input," in sql and "AS cache_write," in sql and "input_combined" not in sql


def _rollup_pair():
    from datetime import date
    from types import SimpleNamespace
    from server.storage.synthetic_load import _actual_rollup_tuples, _expected_rollup_tuples

    expected_rows = [{"date": "2026-09-01", "user_id": "github:a", "repo_id": "github:o/r",
                      "agent": "claude_code", "model": "m", "input": 10, "cache_write_5m": 20,
                      "cache_write_1h": 30, "cache_read": 400, "output": 5}]
    actual = {"date": date(2026, 9, 1), "user_id": "github:a", "repo_id": "github:o/r",
              "agent": "claude_code", "model": "m", "input": 10, "cache_write": 50,
              "cache_read": 400, "output": 5}

    def build(**changes):
        return _actual_rollup_tuples([SimpleNamespace(**{**actual, **changes})])
    return _expected_rollup_tuples(expected_rows), build


def test_rollup_tuples_match_when_the_writes_sum_to_the_database_cache_write():
    expected, build = _rollup_pair()
    assert build() == expected


@pytest.mark.parametrize("changes", [
    {"input": 11}, {"cache_write": 51}, {"cache_read": 401}, {"output": 6},
    {"input": 11, "cache_write": 49},  # offsetting errors between two types still differ
    {"input": 9, "cache_read": 401},
])
def test_rollup_tuples_detect_a_per_type_mismatch(changes):
    expected, build = _rollup_pair()
    assert build(**changes) != expected


# --------------------------------------------------------------------------------------------
# --forecast-export (T5): parsed and checked against the dataset before any database work
# --------------------------------------------------------------------------------------------

def _forecast_dataset(fake_dataset, tmp_path, *, team_ids=("development",),
                      repo_ids=("github:acme/auth-service",)):
    """The fake dataset plus the two files the export is checked against, a directory config
    whose org matches the manifest, and an export built from the default (org default, team
    development, repo github:acme/auth-service) scopes. Returns (directory_yaml, export_path)."""
    from tests.server.storage.forecast_export_builder import make_export, write_export

    (fake_dataset / "directory_teams.jsonl").write_text(
        "".join(json.dumps({"team_id": t, "org_id": "default"}) + "\n" for t in team_ids))
    (fake_dataset / "repositories.jsonl").write_text(
        "".join(json.dumps({"repo_id": r}) + "\n" for r in repo_ids))
    directory_yaml = tmp_path / "directory.yaml"
    directory_yaml.write_text("directory:\n  default_org_id: default\n")
    return directory_yaml, write_export(tmp_path / "forecast_export.json", make_export())


def _forbid_database(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("no database work may happen")

    monkeypatch.setattr("server.storage.synthetic_load.get_engine", boom)
    monkeypatch.setattr("server.storage.synthetic_load.dbm_operations.migrate", boom)


_DSN = "postgresql+psycopg://u@h:5433/x_synth"


def test_dry_run_with_an_export_plans_the_forecast_rows_and_never_connects(
        fake_dataset, tmp_path, monkeypatch):
    from server.storage.forecast_load import parse_export
    directory_yaml, export = _forecast_dataset(fake_dataset, tmp_path)
    _forbid_database(monkeypatch)
    result = load_dataset(fake_dataset, _DSN, replace=False, dry_run=True,
                          directory_config_path=str(directory_yaml), forecast_export=export)
    assert result.dry_run is True
    assert result.row_counts["forecast_runs"] == 1
    assert result.row_counts["forecast_scope_series"] == 6          # 3 scopes x 2 presets
    assert result.row_counts["sessions.jsonl"] == 1                 # file-name keys are unchanged
    assert result.forecast_run_id == parse_export(export).run_id


def test_dry_run_without_an_export_has_no_forecast_counts(fake_dataset, tmp_path):
    directory_yaml, _ = _forecast_dataset(fake_dataset, tmp_path)
    result = load_dataset(fake_dataset, _DSN, replace=False, dry_run=True,
                          directory_config_path=str(directory_yaml))
    assert "forecast_runs" not in result.row_counts
    assert "forecast_scope_series" not in result.row_counts
    assert result.forecast_run_id is None


def test_an_export_for_another_org_is_refused_before_any_database_work(
        fake_dataset, tmp_path, monkeypatch):
    from tests.server.storage.forecast_export_builder import make_export, write_export
    directory_yaml, _ = _forecast_dataset(fake_dataset, tmp_path)
    foreign = write_export(tmp_path / "foreign.json", make_export(
        [("org", "other-org", "Other"), ("team", "development", "Development")]))
    _forbid_database(monkeypatch)
    with pytest.raises(SyntheticLoadError, match="forecast export") as exc:
        load_dataset(fake_dataset, _DSN, replace=True, dry_run=False,
                     directory_config_path=str(directory_yaml), forecast_export=foreign)
    assert "other-org" in str(exc.value) and "'default'" in str(exc.value)


def test_unknown_team_and_repo_ids_are_named_in_the_refusal(fake_dataset, tmp_path, monkeypatch):
    from tests.server.storage.forecast_export_builder import make_export, write_export
    directory_yaml, _ = _forecast_dataset(fake_dataset, tmp_path)
    foreign = write_export(tmp_path / "foreign.json", make_export([
        ("org", "default", "acme"), ("team", "development", "Development"),
        ("team", "ghost-team", "Ghost"), ("repo", "github:acme/auth-service", "a"),
        ("repo", "github:nobody/nothing", "n")]))
    _forbid_database(monkeypatch)
    with pytest.raises(SyntheticLoadError) as exc:
        load_dataset(fake_dataset, _DSN, replace=True, dry_run=False,
                     directory_config_path=str(directory_yaml), forecast_export=foreign)
    message = str(exc.value)
    assert "team 'ghost-team'" in message and "repo 'github:nobody/nothing'" in message
    assert "team 'development'" not in message and "auth-service" not in message


def test_a_dry_run_also_refuses_a_foreign_export(fake_dataset, tmp_path):
    directory_yaml, export = _forecast_dataset(fake_dataset, tmp_path, team_ids=("platform",))
    with pytest.raises(SyntheticLoadError, match="team 'development'"):
        load_dataset(fake_dataset, _DSN, replace=False, dry_run=True,
                     directory_config_path=str(directory_yaml), forecast_export=export)


@pytest.mark.parametrize("content, message", [
    ("{", "not valid JSON"),
    ('{"schema": "dashboard-forecast/2.0"}', "unsupported schema version"),
    ("[]", "JSON object"),
])
def test_a_malformed_export_is_a_synthetic_load_error_and_touches_nothing(
        fake_dataset, tmp_path, monkeypatch, content, message):
    directory_yaml, _ = _forecast_dataset(fake_dataset, tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text(content)
    _forbid_database(monkeypatch)
    with pytest.raises(SyntheticLoadError, match="forecast export") as exc:
        load_dataset(fake_dataset, _DSN, replace=True, dry_run=False,
                     directory_config_path=str(directory_yaml), forecast_export=bad)
    assert message in str(exc.value)


def test_a_missing_export_file_is_a_synthetic_load_error(fake_dataset, tmp_path, monkeypatch):
    directory_yaml, _ = _forecast_dataset(fake_dataset, tmp_path)
    _forbid_database(monkeypatch)
    with pytest.raises(SyntheticLoadError, match="cannot read forecast export"):
        load_dataset(fake_dataset, _DSN, replace=True, dry_run=False,
                     directory_config_path=str(directory_yaml),
                     forecast_export=tmp_path / "nope.json")


def test_the_guard_still_comes_first(fake_dataset, tmp_path):
    directory_yaml, export = _forecast_dataset(fake_dataset, tmp_path)
    with pytest.raises(SyntheticLoadError, match="_synth"):
        load_dataset(fake_dataset, "postgresql+psycopg://u@h:5433/econ", replace=False,
                     dry_run=True, directory_config_path=str(directory_yaml),
                     forecast_export=tmp_path / "nope.json")


def test_cli_dry_run_with_forecast_export_prints_the_planned_counts(
        fake_dataset, tmp_path, capsys):
    from server.storage import cli_load_synthetic
    directory_yaml, export = _forecast_dataset(fake_dataset, tmp_path)
    code = cli_load_synthetic.main(["--dataset", str(fake_dataset), "--dsn", _DSN, "--dry-run",
                                    "--forecast-export", str(export)])
    captured = capsys.readouterr()
    assert code == 0
    summary = json.loads(captured.out)
    assert summary["dry_run"] is True
    assert summary["row_counts"]["forecast_runs"] == 1
    assert summary["row_counts"]["forecast_scope_series"] == 6
    assert len(summary["forecast_run_id"]) == 16


def test_cli_exits_2_for_a_foreign_export(fake_dataset, tmp_path, capsys):
    from server.storage import cli_load_synthetic
    directory_yaml, export = _forecast_dataset(fake_dataset, tmp_path, team_ids=("platform",))
    code = cli_load_synthetic.main(["--dataset", str(fake_dataset), "--dsn", _DSN,
                                    "--forecast-export", str(export)])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "team 'development'" in captured.err


def test_cli_forecast_export_option_defaults_to_none():
    from server.storage import cli_load_synthetic
    args = cli_load_synthetic.parser().parse_args(["--dataset", "d", "--dsn", "x"])
    assert args.forecast_export is None
    args = cli_load_synthetic.parser().parse_args(
        ["--dataset", "d", "--dsn", "x", "--forecast-export", "f.json"])
    assert str(args.forecast_export) == "f.json"


def test_load_synthetic_row_counts_and_truncate_cover_the_forecast_tables():
    from pathlib import Path
    import yaml
    path = Path(__file__).resolve().parents[3] / "config" / "queries" / "synthetic.yaml"
    queries = yaml.safe_load(path.read_text())
    for table in ("forecast_runs", "forecast_scope_series"):
        assert table in queries["truncate_all"]
        assert f"'{table}'" in queries["row_counts"] and f"FROM {table}" in queries["row_counts"]
