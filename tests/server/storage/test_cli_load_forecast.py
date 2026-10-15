"""T5: `cli_load_forecast` and its wrapper `utils/load_forecast.sh`. Exit 0 with one JSON line on
stdout; exit 2 for a bad file / version / refused database / argparse error; exit 1 for
anything else. Logs are JSON lines on stderr. DB tests need a `*_synth` TALIUM_TEST_DSN."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from server.storage import cli_load_forecast
from server.storage.config import StorageConfig
from server.storage.dbm import operations
from server.storage.engine import dispose_engine

from .conftest import DSN
from .forecast_export_builder import dump_forecast_tables, make_export, write_export

_synth_ready = DSN is not None and (make_url(DSN).database or "").endswith("_synth")
requires_synth_db = pytest.mark.skipif(
    not _synth_ready, reason="set TALIUM_TEST_DSN to a *_synth database to run this test")

_REPO = Path(__file__).resolve().parents[3]
_NON_SYNTH = "postgresql+psycopg://postgres@localhost:5432/econ"


@pytest.fixture
def export_file(tmp_path):
    return write_export(tmp_path / "forecast_export.json", make_export())


@pytest.fixture
def db():
    cfg = StorageConfig(dsn=DSN)
    operations.init(cfg, force=True)
    dispose_engine()
    eng = create_engine(DSN)
    yield eng
    eng.dispose()
    dispose_engine()


def _json_lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@requires_synth_db
def test_success_prints_one_json_summary_line_and_loads_the_rows(db, export_file, capsys):
    code = cli_load_forecast.main(["--export", str(export_file), "--dsn", DSN])
    captured = capsys.readouterr()
    assert code == 0

    lines = captured.out.splitlines()
    assert len(lines) == 1                                   # exactly one line on stdout
    summary = json.loads(lines[0])
    assert summary["status"] == "complete"
    assert summary["scope_series_rows"] == 6 and summary["scope_count"] == 3
    assert summary["preset_count"] == 2
    assert summary["origin"] == "2026-09-30" and summary["data_through"] == "2026-10-30"
    assert len(summary["run_id"]) == 16 and summary["duration_ms"] > 0

    dump = dump_forecast_tables(db)
    assert [r[0] for r in dump["runs"]] == [summary["run_id"]]
    assert len(dump["series"]) == 6

    logs = _json_lines(captured.err)                         # structured JSON logs on stderr
    events = [entry["event"] for entry in logs]
    assert "forecast_load.parsed" in events
    assert "forecast_load.replaced" in events
    assert "forecast_load.completed" in events
    assert all("level" in entry and "timestamp" in entry for entry in logs)


@requires_synth_db
def test_loading_twice_keeps_one_run_and_the_same_id(db, export_file, capsys):
    assert cli_load_forecast.main(["--export", str(export_file), "--dsn", DSN]) == 0
    first = json.loads(capsys.readouterr().out)
    assert cli_load_forecast.main(["--export", str(export_file), "--dsn", DSN]) == 0
    second = json.loads(capsys.readouterr().out)
    assert first["run_id"] == second["run_id"]
    assert len(dump_forecast_tables(db)["runs"]) == 1


def _bad_files(tmp_path):
    good = (json.dumps(make_export(), sort_keys=True, separators=(",", ":")) + "\n")
    truncated = tmp_path / "truncated.json"
    truncated.write_text(good[: len(good) // 2], encoding="utf-8")
    v2 = tmp_path / "v2.json"
    v2.write_text(good.replace("dashboard-forecast/1.0", "dashboard-forecast/2.0"), encoding="utf-8")
    not_object = tmp_path / "list.json"
    not_object.write_text("[]", encoding="utf-8")
    return {"missing": tmp_path / "nope.json", "truncated": truncated, "bad-version": v2,
            "not-object": not_object}


@pytest.mark.parametrize("which, message", [
    ("missing", "cannot read forecast export"),
    ("truncated", "not valid JSON"),
    ("bad-version", "unsupported schema version 'dashboard-forecast/2.0'"),
    ("not-object", "JSON object"),
])
def test_a_bad_file_exits_2_with_nothing_on_stdout(tmp_path, capsys, monkeypatch, which, message):
    """No database is needed: the file is refused before any connection (proven by making a
    connection impossible)."""
    def boom(*args, **kwargs):
        raise AssertionError("must not connect")

    from server.storage import forecast_load
    monkeypatch.setattr(forecast_load, "get_engine", boom)
    monkeypatch.setattr(forecast_load.dbm_operations, "migrate", boom)
    code = cli_load_forecast.main(["--export", str(_bad_files(tmp_path)[which]),
                                   "--dsn", "postgresql+psycopg://u@h:5433/x_synth"])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    failure = [e for e in _json_lines(captured.err) if e["event"] == "forecast_load.run_failed"]
    assert len(failure) == 1
    assert failure[0]["error_category"] == "ForecastLoadError"
    assert message in failure[0]["message"]


def test_a_non_synth_dsn_exits_2_without_connecting(export_file, capsys, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("must not connect")

    from server.storage import forecast_load
    monkeypatch.setattr(forecast_load, "get_engine", boom)
    monkeypatch.setattr(forecast_load.dbm_operations, "migrate", boom)
    code = cli_load_forecast.main(["--export", str(export_file), "--dsn", _NON_SYNTH])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "_synth" in captured.err and "no override" in captured.err


@requires_synth_db
def test_a_bad_file_leaves_the_database_unchanged(db, export_file, tmp_path, capsys):
    assert cli_load_forecast.main(["--export", str(export_file), "--dsn", DSN]) == 0
    capsys.readouterr()
    before = dump_forecast_tables(db)
    assert cli_load_forecast.main(["--export", str(_bad_files(tmp_path)["bad-version"]),
                                   "--dsn", DSN]) == 2
    assert dump_forecast_tables(db) == before


@pytest.mark.parametrize("argv", [
    [],
    ["--export", "x.json"],
    ["--dsn", "postgresql+psycopg://u@h/x_synth"],
    ["--export", "x.json", "--dsn", "d", "--bogus"],
    ["--export"],
])
def test_argparse_errors_exit_2(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        cli_load_forecast.main(argv)
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli_load_forecast.main(["--help"])
    assert exc.value.code == 0
    assert "--export" in capsys.readouterr().out


def test_an_unexpected_failure_exits_1_with_a_traceback_on_stderr(export_file, capsys, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(cli_load_forecast, "load_forecast_export", broken)
    code = cli_load_forecast.main(["--export", str(export_file),
                                   "--dsn", "postgresql+psycopg://u@h:5433/x_synth"])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "Traceback" in captured.err and "database exploded" in captured.err


# ------------------------------------------------------------------------------- wrapper

def test_the_wrapper_script_is_executable_and_runs_the_right_module():
    script = _REPO / "utils" / "load_forecast.sh"
    assert os.access(script, os.X_OK)
    text = script.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text
    assert 'exec python -m server.storage.cli_load_forecast "$@"' in text


def test_the_wrapper_runs_the_cli_end_to_end(export_file):
    """Through bash, with the venv's python first on PATH: a non-synth database is refused with
    exit 2 and nothing on stdout (no database needed)."""
    env = {**os.environ, "PYTHONPATH": str(_REPO / "src"),
           "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"}
    done = subprocess.run(
        ["bash", str(_REPO / "utils" / "load_forecast.sh"), "--export", str(export_file),
         "--dsn", _NON_SYNTH],
        cwd=_REPO, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 2
    assert done.stdout == ""
    assert "_synth" in done.stderr
