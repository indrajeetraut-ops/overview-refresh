"""T10 accept: exit code per case; existing output refused without --force;
--force deletes only the target. `cli_cost.py` invoked as a real subprocess
(mirrors `tests/curve/test_cli.py`); the CLI has no `--catalog` flag (fix 1: Python
API only), so these use `claude-sonnet-5`, a real live-catalog entry, rather than
the fixture-catalog machinery `test_request.py` needs for synthetic models."""

from __future__ import annotations

from datetime import date
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
PYTHON = ROOT / "venv" / "bin" / "python"
WRAPPER = ROOT / "econ-forecast" / "utils" / "cost.sh"
ENV = {**os.environ, "PYTHONPATH": f"{ROOT / 'econ-forecast' / 'src'}:{ROOT / 'src'}"}

_fixture_path = Path(__file__).parents[1] / "acceptance" / "conftest.py"
_spec = importlib.util.spec_from_file_location("_cost_cli_test_fixtures", _fixture_path)
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)


def cli(*args, cwd=None, env=None):
    return subprocess.run(
        [str(PYTHON), "-m", "forecast.cost.cli_cost", *map(str, args)],
        cwd=cwd or ROOT, env=env or ENV, text=True, capture_output=True, timeout=120,
    )


@pytest.fixture
def forecast_run(tmp_path):
    from forecast.forecasting.request import ForecastRequest
    from forecast.forecasting.runner import run_forecast

    dataset = _fixtures.make_daily_dataset(
        tmp_path / "daily", models=(("claude-sonnet-5", "claude"),), users=("u1", "u2"),
    )
    config = _fixtures.write_forecast_config(tmp_path / "config.yaml", dataset)
    output = tmp_path / "forecast-out"
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    return output


def test_help_does_no_io(tmp_path):
    result = cli("--help", cwd=tmp_path)
    assert result.returncode == 0
    assert "--forecast-run" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_missing_required_arguments_exit_2(tmp_path):
    result = cli(cwd=tmp_path)
    assert result.returncode == 2


def test_validate_only_writes_nothing_and_exits_0(forecast_run, tmp_path):
    before = set(tmp_path.rglob("*"))
    result = cli("--forecast-run", forecast_run, "--validate-only")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "valid"
    assert summary["unpriced_models"] == []
    assert summary["output_dir"] is None
    assert set(tmp_path.rglob("*")) == before  # nothing new on disk


def test_validate_only_with_unmatched_model_exits_2(tmp_path):
    from forecast.forecasting.request import ForecastRequest
    from forecast.forecasting.runner import run_forecast

    dataset = _fixtures.make_daily_dataset(
        tmp_path / "daily", models=(("totally-fake-model-xyz", "claude"),), users=("u1",),
    )
    config = _fixtures.write_forecast_config(tmp_path / "config.yaml", dataset)
    forecast_output = tmp_path / "forecast-out"
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), forecast_output)

    result = cli("--forecast-run", forecast_output, "--validate-only")
    assert result.returncode == 2
    summary = json.loads(result.stdout)
    assert summary["status"] == "invalid"
    assert "totally-fake-model-xyz" in summary["unpriced_models"]


def test_missing_forecast_run_exits_2(tmp_path):
    result = cli("--forecast-run", tmp_path / "does-not-exist", "--validate-only")
    assert result.returncode == 2
    summary = json.loads(result.stdout)
    assert summary["status"] == "invalid"


def test_real_run_writes_output_and_exits_0(forecast_run, tmp_path):
    output = tmp_path / "cost-out"
    result = cli("--forecast-run", forecast_run, "--output", output)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "complete"
    assert summary["scenario"] == "baseline"
    assert (output / "manifest.json").is_file()
    assert (output / "report.md").is_file()


def test_existing_output_is_refused_without_force(forecast_run, tmp_path):
    output = tmp_path / "cost-out"
    output.mkdir()
    (output / "sentinel.txt").write_text("keep me")
    result = cli("--forecast-run", forecast_run, "--output", output)
    assert result.returncode == 2
    assert (output / "sentinel.txt").is_file()  # untouched


def test_force_recreates_only_the_target(forecast_run, tmp_path):
    output = tmp_path / "cost-out"
    first = cli("--forecast-run", forecast_run, "--output", output)
    assert first.returncode == 0, first.stderr
    sibling = tmp_path / "sibling.txt"
    sibling.write_text("must survive")

    second = cli("--forecast-run", forecast_run, "--output", output, "--force")
    assert second.returncode == 0, second.stderr
    assert (output / "manifest.json").is_file()
    assert sibling.is_file()


def test_wrapper_script_runs(forecast_run, tmp_path):
    output = tmp_path / "cost-out"
    result = subprocess.run(
        [str(WRAPPER), "--forecast-run", str(forecast_run), "--output", str(output)],
        cwd=ROOT, text=True, capture_output=True, timeout=180,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "complete"


def test_execution_error_exits_1(monkeypatch):
    """`main()`'s exit-1 path (`ExecutionError`), exercised directly since
    provoking a genuine I/O failure end-to-end would be needlessly elaborate.

    Captures stdout by hand (`io.StringIO`) rather than pytest's `capsys`: capsys
    swaps `sys.stdout`/`sys.stderr` at the fd level, and `main()` configures
    structlog against whatever `sys.stderr` is current at call time -- letting
    capsys tear down afterward leaves a stale, closed file behind for any later
    in-process logging in this same test session (the pre-existing structlog
    "closed file" interaction; this test just avoids ever creating an instance
    of it)."""
    import io

    from forecast.cost import cli_cost
    from forecast.cost.errors import ExecutionError

    def _raise(*args, **kwargs):
        raise ExecutionError("simulated execution failure", stage="write")

    monkeypatch.setattr(cli_cost, "run_cost", _raise)
    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)  # sys.stderr is left alone on purpose
    code = cli_cost.main(["--forecast-run", "unused", "--output", "unused"])
    assert code == 1
    summary = json.loads(captured.getvalue())
    assert summary["status"] == "failed"
    assert summary["stage"] == "write"


def test_run_cost_prices_a_scenario_python_api(tmp_path):
    """`run_cost`'s scenario path (Python API, not subprocess, so a pinned
    `catalog=` fixture can stand in for the live one -- model_transfer's `to` needs
    a `matched` model)."""
    from common.model_pricing.catalog import CatalogVersion, RateRow, RegistryEntry, load_catalog
    from common.model_pricing.decimals import per_million_to_per_token as pm
    from forecast.forecasting.request import ForecastRequest
    from forecast.forecasting.runner import run_forecast
    from forecast.cost.request import CostRequest
    from forecast.cost.runner import run_cost

    dataset = _fixtures.make_daily_dataset(
        tmp_path / "daily", models=(("model-a", "agent-a"), ("model-b", "agent-b")), users=("u1", "u2"),
    )
    config = _fixtures.write_forecast_config(
        tmp_path / "config.yaml", dataset,
        scopes=[{"type": "organization"}, {"type": "team", "name": "Team A"}],
    )
    forecast_out = tmp_path / "forecast-out"
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), forecast_out)

    catalog_root, config_root = tmp_path / "catalog", tmp_path / "catalog-config"
    catalog_root.mkdir()
    config_root.mkdir()
    rates = [
        RateRow(
            channel="anthropic", model_id=m, start_date=date(2020, 1, 1),
            input=pm(3), output=pm(15), cache_read=pm(0.3), source="test", source_key=m,
        )
        for m in ("model-a", "model-b")
    ]
    models = {m: RegistryEntry(vendor="anthropic", names_seen=[m]) for m in ("model-a", "model-b")}
    stub = CatalogVersion(version="pending", built_on=date(2026, 1, 1), models=models, rates=rates)
    version = f"2026-01-01-{stub.compute_hash8()}"
    (catalog_root / f"{version}.json").write_text(stub.model_copy(update={"version": version}).model_dump_json())
    (catalog_root / "index.json").write_text(json.dumps({"versions": [version]}))
    catalog = load_catalog(root=catalog_root, config_root=config_root)

    scenario_path = tmp_path / "team-a-shift.yaml"
    scenario_path.write_text(
        "schema: scenario/1.0\n"
        "name: team-a-shift\n"
        "scope:\n  type: team\n  name: Team A\n"
        "changes:\n"
        "  - model_transfer:\n"
        "      from: model-a\n      to: model-b\n      fraction: 0.3\n",
        encoding="utf-8",
    )

    cost_out = tmp_path / "cost-out"
    result = run_cost(forecast_out, CostRequest(scenario=scenario_path, catalog=catalog), cost_out)
    assert result.exit_code == 0
    assert result.scenario == "team-a-shift"
    assert (cost_out / "scenario.yaml").is_file()
    assert json.loads((cost_out / "request.json").read_text())["scenario"] == "team-a-shift"
