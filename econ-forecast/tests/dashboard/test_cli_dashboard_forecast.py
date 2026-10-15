"""The command line: exit codes, the JSON summary on stdout, structured logs on stderr,
`--force`, the order of work, and the wrapper script. The in-process tests pin the
fixture's price catalog and fast forecast template (the CLI has no such options); one
subprocess test runs the real entry point and checks structure only."""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import structlog

from forecast.dashboard import ExecutionError, validate_export
from forecast.dashboard import cli_dashboard_forecast as cli
from forecast.dashboard import run_dashboard_forecast as real_run

ROOT = Path(__file__).resolve().parents[3]
WRAPPER = ROOT / "econ-forecast" / "utils" / "dashboard_forecast.sh"
SUMMARY_KEYS = {
    "status", "exit_code", "export_path", "bytes", "origin", "data_through", "org_id",
    "scope_count", "preset_count", "generated_at",
}


def invoke(argv):
    """Run `main(argv)` in this process; returns (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    saved, was_configured = structlog.get_config(), structlog.is_configured()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main([str(a) for a in argv])
    finally:
        structlog.configure(**saved) if was_configured else structlog.reset_defaults()
    return code, out.getvalue(), err.getvalue()


def summary_of(stdout):
    lines = stdout.splitlines()
    assert len(lines) == 1, stdout
    return json.loads(lines[0])


@pytest.fixture(scope="module")
def pinned_runner(fast_template, pinned_catalog):
    """`main` with the fixture template and catalog behind the runner call."""
    patch = pytest.MonkeyPatch()
    patch.setattr(
        cli, "run_dashboard_forecast",
        lambda series, presets, output, **kw: real_run(
            series, presets, output, forecast_template=fast_template, catalog=pinned_catalog, **kw
        ),
    )
    yield
    patch.undo()


@pytest.fixture(scope="module")
def success(pinned_runner, series_dir, make_org_demo, tmp_path_factory):
    demo = make_org_demo(tmp_path_factory.mktemp("cli-demo") / "demo", series_dir)
    code, out, err = invoke(["--org-demo", demo])
    return {"demo": demo, "code": code, "out": out, "err": err}


@pytest.fixture
def demo(pinned_runner, series_dir, make_org_demo, tmp_path):
    return make_org_demo(tmp_path / "demo", series_dir)


# --- success ---------------------------------------------------------------------------------


def test_success_exits_zero_with_the_summary_and_the_file(success):
    assert success["code"] == 0
    summary = summary_of(success["out"])
    assert set(summary) == SUMMARY_KEYS
    export = success["demo"] / "forecast" / "forecast_export.json"
    assert summary["export_path"] == str(export.resolve()) or Path(summary["export_path"]).samefile(export)
    assert summary["status"] == "complete" and summary["exit_code"] == 0
    assert summary["bytes"] == export.stat().st_size > 10_000
    assert (summary["origin"], summary["data_through"]) == ("2026-09-30", "2026-10-15")
    assert (summary["org_id"], summary["scope_count"], summary["preset_count"]) == ("fixture.dev", 6, 7)
    doc = json.loads(export.read_text())
    validate_export(doc)
    assert summary["generated_at"] == doc["generated_at"]
    assert len(doc["scopes"]) == 6 and len(doc["presets"]) == 7


def test_the_default_output_is_forecast_inside_the_org_demo(success):
    forecast = success["demo"] / "forecast"
    assert sorted(p.name for p in forecast.iterdir()) == [
        "cost", "forecast", "forecast.yaml", "forecast_export.json", "scenarios",
    ]


def test_stdout_is_one_json_line_and_logs_are_json_on_stderr(success):
    assert success["out"].endswith("\n") and success["out"].count("\n") == 1
    events = [json.loads(line) for line in success["err"].splitlines()]
    names = [e["event"] for e in events]
    assert names[0] == "dashboard.run.started"
    assert "dashboard.export.started" in names and "dashboard.export.completed" in names
    assert names[-1] == "dashboard.export_completed"
    assert names.count("dashboard.export.scope_exported") == 6
    assert all("level" in e and "timestamp" in e for e in events)


# --- exit 2 --------------------------------------------------------------------------------


def _invalid(code, out, stage=None):
    assert code == 2
    summary = summary_of(out)
    assert summary["status"] == "invalid" and summary["exit_code"] == 2
    assert set(summary) == {"status", "exit_code", "error", "stage"}
    if stage is not None:
        assert summary["stage"] == stage
    return summary


@pytest.mark.parametrize("argv", [[], ["--force"], ["--org-demo"], ["--org-demo", "x", "--nope"]])
def test_command_line_errors_exit_2(pinned_runner, argv):
    code, out, _ = invoke(argv)
    assert "invalid command line" in _invalid(code, out)["error"]


def test_a_missing_org_demo_exits_2(pinned_runner, tmp_path):
    code, out, _ = invoke(["--org-demo", tmp_path / "nowhere"])
    assert "not found" in _invalid(code, out)["error"]


def test_an_org_demo_without_a_series_dataset_exits_2(pinned_runner, tmp_path):
    (tmp_path / "empty").mkdir()
    code, out, _ = invoke(["--org-demo", tmp_path / "empty"])
    assert "manifest" in _invalid(code, out)["error"]


def test_a_missing_presets_file_exits_2_and_leaves_the_output_alone(demo, tmp_path):
    code, out, _ = invoke(["--org-demo", demo, "--presets", tmp_path / "nope.yaml"])
    assert "presets" in _invalid(code, out)["error"]
    assert not (demo / "forecast").exists()


def test_a_non_empty_output_without_force_exits_2_and_changes_nothing(demo, tmp_path):
    out_dir = tmp_path / "taken"
    out_dir.mkdir()
    (out_dir / "keep.txt").write_text("keep")
    code, out, _ = invoke(["--org-demo", demo, "--output", out_dir])
    assert "not empty" in _invalid(code, out)["error"]
    assert [p.name for p in out_dir.iterdir()] == ["keep.txt"]


def test_an_unmappable_scope_stops_before_the_output_is_touched(demo, tmp_path):
    (demo / "org" / "repositories.jsonl").write_text("")
    out_dir = tmp_path / "out"
    code, out, _ = invoke(["--org-demo", demo, "--output", out_dir])
    summary = _invalid(code, out, stage="scopes")
    assert "checkout_flow" in summary["error"]
    assert not out_dir.exists()


def test_an_unmappable_scope_with_force_keeps_the_previous_run(demo, success, tmp_path):
    previous = tmp_path / "previous"
    shutil.copytree(success["demo"] / "forecast", previous)
    before = (previous / "forecast_export.json").read_bytes()
    (demo / "org" / "directory_teams.jsonl").write_text("")
    code, out, _ = invoke(["--org-demo", demo, "--output", previous, "--force"])
    _invalid(code, out, stage="scopes")
    assert (previous / "forecast_export.json").read_bytes() == before


# --- --force ---------------------------------------------------------------------------------


def test_force_replaces_the_previous_run_and_its_stale_export(demo, success, tmp_path):
    out_dir = tmp_path / "again"
    shutil.copytree(success["demo"] / "forecast", out_dir)
    (out_dir / "forecast_export.json").write_text("stale")
    (out_dir / "forecast" / "stale.txt").write_text("stale")
    (out_dir / "notes.txt").write_text("not ours")
    code, out, _ = invoke(["--org-demo", demo, "--output", out_dir, "--force"])
    assert code == 0, out
    assert not (out_dir / "forecast" / "stale.txt").exists()
    assert (out_dir / "notes.txt").read_text() == "not ours"
    doc = json.loads((out_dir / "forecast_export.json").read_text())
    validate_export(doc)
    assert summary_of(out)["bytes"] == (out_dir / "forecast_export.json").stat().st_size


# --- exit 1 ---------------------------------------------------------------------------------


def test_an_execution_error_in_the_run_exits_1(demo, tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise ExecutionError("cost run 'baseline' did not complete", stage="cost")

    monkeypatch.setattr(cli, "run_dashboard_forecast", boom)
    code, out, err = invoke(["--org-demo", demo, "--output", tmp_path / "out"])
    assert code == 1
    summary = summary_of(out)
    assert summary == {
        "status": "failed", "exit_code": 1, "error": "cost run 'baseline' did not complete", "stage": "cost",
    }
    assert json.loads(err.splitlines()[-1])["event"] == "dashboard.execution_error"


def test_an_execution_error_in_the_export_exits_1(demo, tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise ExecutionError("cannot write", stage="export")

    monkeypatch.setattr(cli, "export_forecast", boom)
    code, out, _ = invoke(["--org-demo", demo, "--output", tmp_path / "out"])
    assert code == 1 and summary_of(out)["stage"] == "export"
    assert not (tmp_path / "out" / "forecast_export.json").exists()


def test_a_run_over_other_scopes_than_planned_exits_1_without_an_export(demo, tmp_path, monkeypatch):
    class Result:
        scopes = ()

    monkeypatch.setattr(cli, "run_dashboard_forecast", lambda *a, **k: Result())
    code, out, _ = invoke(["--org-demo", demo, "--output", tmp_path / "out"])
    assert code == 1 and "differ" in summary_of(out)["error"]


# --- the wrapper and the real entry point --------------------------------------------------------


def test_the_wrapper_is_executable_and_runs_the_cli_module():
    assert os.access(WRAPPER, os.X_OK)
    text = WRAPPER.read_text()
    assert text.startswith("#!/usr/bin/env bash")
    assert "-m forecast.dashboard.cli_dashboard_forecast" in text
    assert '"$@"' in text and "_forecast_env.sh" in text


def _subprocess(*args, cwd=None):
    env = {**os.environ, "PYTHONPATH": f"{ROOT / 'econ-forecast' / 'src'}:{ROOT / 'src'}"}
    return subprocess.run(
        [sys.executable, "-m", "forecast.dashboard.cli_dashboard_forecast", *map(str, args)],
        cwd=cwd or ROOT, env=env, text=True, capture_output=True, timeout=120,
    )


def test_help_does_no_io(tmp_path):
    result = _subprocess("--help", cwd=tmp_path)
    assert result.returncode == 0 and "--org-demo" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_the_real_entry_point_on_the_fixture_org(series_dir, make_org_demo, tmp_path):
    # No catalog or template option here: this prices with the live catalog, so only the
    # structure of the result is checked, never a dollar figure.
    demo = make_org_demo(tmp_path / "demo", series_dir)
    result = _subprocess("--org-demo", demo, "--output", tmp_path / "out")
    assert result.returncode == 0, result.stderr[-2000:]
    summary = summary_of(result.stdout)
    assert set(summary) == SUMMARY_KEYS and summary["scope_count"] == 6 and summary["preset_count"] == 7
    doc = json.loads((tmp_path / "out" / "forecast_export.json").read_text())
    validate_export(doc)
    assert [s["scope_id"] for s in doc["scopes"]][:3] == ["default", "alpha", "beta"]
    assert all(json.loads(line)["event"] for line in result.stderr.splitlines())
    assert not (demo / "forecast").exists()


def test_the_real_entry_point_reports_a_missing_org_demo(tmp_path):
    result = _subprocess("--org-demo", tmp_path / "nowhere")
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "invalid"
