"""Tier-1 basic acceptance for `--force` on the forecasting CLI/runner (Phase 0).

Covers: refuse-by-default is preserved (the frozen byte-preservation contract lives in
`test_input_failure_contract.py`), `--force` clears exactly the resolved output
directory and proceeds, a non-directory target is refused rather than force-deleted,
deletion never escapes the resolved output path, force-clearing an output that would
delete the run's own input dataset is refused, and the real `forecast.sh` wrapper wires
the flag through end to end.
"""
from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
import subprocess

import pytest

from forecast.forecasting.errors import InputError
from forecast.forecasting.request import ForecastRequest
from forecast.forecasting.runner import run_forecast

ROOT = Path(__file__).parents[3]
WRAPPER = ROOT / "econ-forecast/utils/forecast.sh"


def test_force_recreates_existing_output_directory(tmp_path, daily_dataset_factory, forecast_config_factory):
    dataset = daily_dataset_factory(tmp_path / "daily")
    config = forecast_config_factory(tmp_path / "forecast.yaml", dataset)
    output = tmp_path / "existing-output"
    output.mkdir()
    (output / "sentinel.bin").write_bytes(b"stale bytes")

    result = run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output, force=True)
    assert result.status == "complete"
    assert not (output / "sentinel.bin").exists()
    assert (output / "manifest.json").is_file()


def test_force_refuses_a_non_directory_target(tmp_path, daily_dataset_factory, forecast_config_factory):
    dataset = daily_dataset_factory(tmp_path / "daily")
    config = forecast_config_factory(tmp_path / "forecast.yaml", dataset)
    output = tmp_path / "existing-output"
    output.write_bytes(b"not a directory")

    with pytest.raises(InputError, match="not a directory"):
        run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output, force=True)
    assert output.is_file()
    assert output.read_bytes() == b"not a directory"


def test_force_confines_deletion_to_the_resolved_target(tmp_path, daily_dataset_factory, forecast_config_factory):
    dataset = daily_dataset_factory(tmp_path / "daily")
    config = forecast_config_factory(tmp_path / "forecast.yaml", dataset)
    output = tmp_path / "existing-output"
    output.mkdir()
    (output / "sentinel.bin").write_bytes(b"stale")
    sibling = tmp_path / "existing-output-sibling"
    sibling.mkdir()
    (sibling / "keep-me.txt").write_text("keep", encoding="utf-8")

    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output, force=True)
    assert (sibling / "keep-me.txt").read_text(encoding="utf-8") == "keep"


def test_force_refuses_an_output_that_would_delete_the_input_dataset(
        tmp_path, daily_dataset_factory, forecast_config_factory):
    dataset = daily_dataset_factory(tmp_path / "daily")
    config = forecast_config_factory(tmp_path / "forecast.yaml", dataset)
    # --output pointed directly at the run's own input dataset: force-clearing it would
    # destroy the data being forecast. Must refuse, never silently sweep it away.
    with pytest.raises(InputError, match="required input"):
        run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), dataset, force=True)
    assert (dataset / "manifest.json").is_file()


def test_force_cli_wrapper_recreates_existing_output(tmp_path, daily_dataset_factory, forecast_config_factory):
    dataset = daily_dataset_factory(tmp_path / "daily", days=60, token_fn=lambda d, m, u: (100, 300, 100))
    config = forecast_config_factory(tmp_path / "forecast.yaml", dataset, uncertainty={"paths": 16, "seed": 17})
    output = tmp_path / "forecast-output"
    output.mkdir()
    (output / "sentinel.bin").write_bytes(b"stale")
    common = [str(WRAPPER), "--config", str(config), "--origin", "2026-08-29", "--unit", "month",
              "--offset", "1", "--periods", "1", "--output", str(output)]

    refused = subprocess.run(common, cwd=tmp_path, env=os.environ.copy(), text=True, capture_output=True)
    assert refused.returncode == 2
    assert json.loads(refused.stdout)["status"] == "invalid"
    assert (output / "sentinel.bin").exists()

    forced = subprocess.run([*common, "--force"], cwd=tmp_path, env=os.environ.copy(),
                            text=True, capture_output=True)
    assert forced.returncode == 0, forced.stderr
    summary = json.loads(forced.stdout)
    assert summary["status"] == "complete"
    assert not (output / "sentinel.bin").exists()
