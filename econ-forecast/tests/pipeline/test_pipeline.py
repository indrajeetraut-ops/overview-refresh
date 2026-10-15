"""Workspace pipeline (design §14.4, plan T7). Failure paths use stub wrappers
(`UTILS_DIR` pointed at a temp directory of fake `utils/*.sh`); one test runs the
real wrappers end to end: synthetic dataset -> forecast -> cost -> plot."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import structlog
import yaml

from forecast.pipeline import cli_pipeline

ROOT = Path(__file__).resolve().parents[3]
WRAPPER = ROOT / "econ-forecast" / "utils" / "pipeline.sh"

_fixture_path = Path(__file__).parents[1] / "acceptance" / "conftest.py"
_spec = importlib.util.spec_from_file_location("_pipeline_test_fixtures", _fixture_path)
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)

_STUB = """#!/usr/bin/env bash
printf '%s\\t%s\\t%s\\n' "$(basename "$0")" "$PWD" "$*" >> "$STUB_LOG"
prev=""
for arg in "$@"; do
  if [ "$prev" = "--output" ]; then mkdir -p "$arg"; fi
  prev="$arg"
done
if [ -n "${STUB_FAIL:-}" ] && [[ "$*" == *"$STUB_FAIL"* ]]; then
  for i in $(seq 1 60); do echo "stub stderr line $i" >&2; done
  echo '{"status": "invalid", "exit_code": 2, "error": "boom", "stage": "request"}'
  exit 2
fi
echo "stub progress" >&2
echo '{"status": "complete"}'
"""


@pytest.fixture(autouse=True)
def _restore_structlog():
    saved = structlog.get_config()
    yield
    structlog.configure(**saved)


@pytest.fixture
def stubs(tmp_path, monkeypatch):
    utils = tmp_path / "stub-utils"
    utils.mkdir()
    for name in ("forecast.sh", "cost.sh", "plot_forecast.sh", "plot_cost.sh"):
        (utils / name).write_text(_STUB, encoding="utf-8")
        (utils / name).chmod(0o755)
    log = tmp_path / "calls.log"
    monkeypatch.setattr(cli_pipeline, "UTILS_DIR", utils)
    monkeypatch.setenv("STUB_LOG", str(log))
    monkeypatch.delenv("STUB_FAIL", raising=False)
    return log


def _workspace(tmp_path, pipeline: dict, scenarios=("a-growth", "b-cheaper")) -> Path:
    workspace = tmp_path / "ws"
    (workspace / "cost" / "scenarios").mkdir(parents=True)
    for name in scenarios:
        (workspace / "cost" / "scenarios" / f"{name}.yaml").write_text("x: 1\n", encoding="utf-8")
    (workspace / "pipeline.yaml").write_text(yaml.safe_dump(pipeline, sort_keys=False), encoding="utf-8")
    return workspace


def _calls(log: Path) -> list[tuple[str, str, str]]:
    if not log.exists():
        return []
    return [tuple(line.split("\t")) for line in log.read_text(encoding="utf-8").splitlines()]


def _run(workspace: Path, capsys) -> tuple[int, dict, dict]:
    code = cli_pipeline.main(["--config", str(workspace / "pipeline.yaml")])
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    written = json.loads((workspace / "pipeline_result.json").read_text(encoding="utf-8"))
    return code, printed, written


def test_all_stages_run_in_order_with_the_design_commands(tmp_path, stubs, capsys):
    workspace = _workspace(tmp_path, {"stages": ["plot", "cost", "forecast"],
                                      "forecast": {"unit": "month", "offset": 1, "periods": 2}})
    code, printed, written = _run(workspace, capsys)
    assert code == 0 and printed == written and written["status"] == "complete"
    assert [s["stage"] for s in written["stages"]] == ["forecast", "cost", "plot"]
    calls = _calls(stubs)
    assert {cwd for _, cwd, _ in calls} == {str(workspace.resolve())}
    assert [(name, args) for name, _, args in calls] == [
        ("forecast.sh", "--config forecast/forecast.yaml --output forecast/run --force "
                        "--unit month --offset 1 --periods 2"),
        ("cost.sh", "--forecast-run forecast/run --output cost/baseline --force"),
        ("cost.sh", "--forecast-run forecast/run --scenario cost/scenarios/a-growth.yaml "
                    "--output cost/a-growth --force"),
        ("cost.sh", "--forecast-run forecast/run --scenario cost/scenarios/b-cheaper.yaml "
                    "--output cost/b-cheaper --force"),
        ("plot_forecast.sh", "--artifact forecast/run --config plot.yaml --force"),
        ("plot_cost.sh", "--cost-run cost/baseline --config plot.yaml --force"),
        ("plot_cost.sh", "--cost-run cost/a-growth --config plot.yaml --force"),
        ("plot_cost.sh", "--cost-run cost/b-cheaper --config plot.yaml --force"),
    ]
    first = written["stages"][0]["commands"][0]
    assert first["argv"][0] == "utils/forecast.sh"
    assert first["summary"] == {"status": "complete"} and first["stderr_tail"] is None
    assert first["exit_code"] == 0 and first["elapsed_seconds"] >= 0


def test_failing_cost_scenario_stops_and_skips_the_rest(tmp_path, stubs, capsys, monkeypatch):
    monkeypatch.setenv("STUB_FAIL", "a-growth.yaml")
    workspace = _workspace(tmp_path, {"stages": ["forecast", "cost", "plot"]})
    code, _, written = _run(workspace, capsys)
    assert code == 2
    assert written["status"] == "failed"
    forecast, cost, plot = written["stages"]
    assert forecast["status"] == "complete"
    assert cost["status"] == "failed"
    baseline, failing, later = cost["commands"]
    assert baseline["status"] == "complete"
    assert failing["status"] == "failed" and failing["exit_code"] == 2
    assert failing["summary"]["error"] == "boom"
    assert len(failing["stderr_tail"]) == 50
    assert failing["stderr_tail"][-1] == "stub stderr line 60"
    assert later["status"] == "skipped" and later["exit_code"] is None
    assert plot["status"] == "skipped"
    assert plot["commands"] and all(c["status"] == "skipped" for c in plot["commands"])
    assert not any(name.startswith("plot") for name, _, _ in _calls(stubs))


@pytest.mark.parametrize("pipeline, fragment", [
    ({"stages": ["forecast", "budget"]}, "stages"),
    ({"stages": []}, "stages"),
    ({"stages": ["forecast"], "forecast": {"output": "elsewhere"}}, "--output"),
    ({"stages": ["forecast"], "forecast": {"verbose": True}}, "forecast"),
    ({"stages": ["cost"], "extra": 1}, "extra"),
])
def test_config_error_exits_1_with_top_level_error_and_runs_nothing(
        tmp_path, stubs, capsys, pipeline, fragment):
    workspace = _workspace(tmp_path, pipeline)
    code, printed, written = _run(workspace, capsys)
    assert code == 1 and printed == written
    assert written["status"] == "failed" and written["stages"] == []
    assert written["error"]["type"] == "input" and fragment in written["error"]["message"]
    assert _calls(stubs) == []


@pytest.mark.parametrize("stem", ["baseline", "scenarios"])
def test_reserved_scenario_stem_is_a_config_error(tmp_path, stubs, capsys, stem):
    workspace = _workspace(tmp_path, {"stages": ["forecast", "cost", "plot"]},
                           scenarios=("ok", stem))
    code, _, written = _run(workspace, capsys)
    assert code == 1 and written["stages"] == []
    assert f"{stem}.yaml" in written["error"]["message"]
    assert _calls(stubs) == []


def test_unreadable_pipeline_yaml_is_a_config_error(tmp_path, stubs, capsys):
    workspace = _workspace(tmp_path, {"stages": ["forecast"]})
    (workspace / "pipeline.yaml").write_text("stages: [forecast\n", encoding="utf-8")
    code, _, written = _run(workspace, capsys)
    assert code == 1 and written["error"]["type"] == "input"


def test_help_and_missing_config(tmp_path):
    result = subprocess.run([str(WRAPPER), "--help"], cwd=tmp_path, text=True,
                            capture_output=True, timeout=60)
    assert result.returncode == 0 and "--config" in result.stdout
    with pytest.raises(SystemExit) as info:
        cli_pipeline.main([])
    assert info.value.code == 1


# ---------------------------------------------------------------------------
# One real run through the wrappers: synthetic dataset -> forecast -> cost -> plot
# ---------------------------------------------------------------------------


def _real_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "examples"
    (root / "dataset").mkdir(parents=True)
    _fixtures.make_daily_dataset(root / "dataset" / "synth-mini",
                                 models=(("claude-sonnet-5", "claude"),), users=("u1", "u2"))
    workspace = root / "mini"
    (workspace / "forecast").mkdir(parents=True)
    (workspace / "cost" / "scenarios").mkdir(parents=True)
    _fixtures.write_forecast_config(
        workspace / "forecast" / "forecast.yaml", Path("../../dataset/synth-mini"),
        scopes=[{"type": "organization"}, {"type": "team", "name": "Team A"}])
    (workspace / "cost" / "scenarios" / "org-growth.yaml").write_text(
        "schema: scenario/1.0\nname: org-growth\nscope:\n  type: organization\nchanges:\n"
        "  - growth:\n      factor: 1.25\n      effective_from: 2026-09-15\n", encoding="utf-8")
    (workspace / "plot.yaml").write_text(
        "schema_version: 1\nplotly_js: ../assets/plotly.min.js\ndefaults:\n  format: html\n"
        "forecast:\n  - scope_type: organization\n  - scope_type: team\n"
        "cost:\n  - scope_type: organization\n  - scope_type: team\n", encoding="utf-8")
    (workspace / "pipeline.yaml").write_text(
        "stages: [forecast, cost, plot]\nforecast:\n  unit: month\n", encoding="utf-8")
    return workspace


def test_real_pipeline_end_to_end(tmp_path):
    from forecast.cost.artifacts import load_cost_run
    from forecast.forecasting.artifacts import load_run

    workspace = _real_workspace(tmp_path)
    result = subprocess.run([str(WRAPPER), "--config", str(workspace / "pipeline.yaml")],
                            cwd=tmp_path, text=True, capture_output=True, timeout=600,
                            env={**os.environ, "NO_COLOR": "1"})
    written = json.loads((workspace / "pipeline_result.json").read_text(encoding="utf-8"))
    assert result.returncode == 0, json.dumps(written, indent=1)[:4000]
    assert written["status"] == "complete"
    assert json.loads(result.stdout.strip().splitlines()[-1]) == written
    assert [len(s["commands"]) for s in written["stages"]] == [1, 2, 3]

    load_run(workspace / "forecast" / "run")
    assert (tmp_path / "examples" / "assets" / "plotly.min.js").is_file()
    for run_dir in ("forecast/run", "cost/baseline", "cost/org-growth"):
        charts = workspace / run_dir / "charts"
        listed = [f for item in json.loads((charts / "index.json").read_text())["plots"]
                  for f in item["files"]]
        on_disk = sorted(p.name for p in charts.iterdir() if p.suffix in (".html", ".png"))
        assert sorted(listed) == on_disk and on_disk
        assert not (charts / "plotly.min.js").exists()
    for cost_dir in ("cost/baseline", "cost/org-growth"):
        load_cost_run(workspace / cost_dir)
        report = (workspace / cost_dir / "report.md").read_text(encoding="utf-8")
        charts = workspace / cost_dir / "charts"
        listed = {f for item in json.loads((charts / "index.json").read_text())["plots"]
                  for f in item["files"]}
        assert set(re.findall(r"\]\(charts/([^)]+)\)", report)) == listed
    request = json.loads((workspace / "cost" / "baseline" / "request.json").read_text())
    assert request["forecast_run"]["path"] == "../../forecast/run"
