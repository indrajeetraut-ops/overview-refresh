"""Black-box wrapper checks are added here as the implementation becomes available."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).parents[3]


def test_forecast_wrapper_help_outside_repo_is_side_effect_free(tmp_path):
    wrapper = ROOT / "econ-forecast/utils/forecast.sh"
    assert wrapper.exists(), "required forecast wrapper is missing"
    result = subprocess.run([str(wrapper), "--help"], cwd=tmp_path, env=os.environ.copy(),
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "--origin" in result.stdout and "--validate-only" in result.stdout
    assert list(tmp_path.iterdir()) == []


def _run(command, cwd):
    return subprocess.run([str(x) for x in command], cwd=cwd, env=os.environ.copy(),
                          text=True, capture_output=True)


def test_real_forecast_and_plot_wrappers_from_spaced_cwd(
        tmp_path, daily_dataset_factory, forecast_config_factory):
    working = tmp_path / "caller with spaces"; working.mkdir()
    dataset = daily_dataset_factory(working / "neutral input", days=60,
                                    token_fn=lambda d, m, u: (100, 300, 100))
    config = forecast_config_factory(working / "forecast config.yaml", dataset,
        uncertainty={"paths": 16, "seed": 17})
    forecast = ROOT / "econ-forecast/utils/forecast.sh"
    common = [forecast, "--config", config, "--origin", "2026-08-29", "--unit", "month",
              "--offset", "1", "--periods", "1"]
    validation = _run([*common, "--validate-only"], working)
    assert validation.returncode == 0, validation.stderr
    assert json.loads(validation.stdout)["status"] == "valid"
    assert all(json.loads(line) for line in validation.stderr.splitlines())
    output = working / "forecast output"
    generated = _run([*common, "--output", output], working)
    assert generated.returncode == 0, generated.stderr
    summary = json.loads(generated.stdout)
    assert summary["status"] == "complete" and Path(summary["report_path"]).is_file()
    assert all(json.loads(line) for line in generated.stderr.splitlines())
    plotted = _run([ROOT / "econ-forecast/utils/plot_forecast.sh", "--artifact", output,
        "--scope-type", "organization"], working)
    assert plotted.returncode == 0, plotted.stderr
    plot_result = json.loads(plotted.stdout)
    entry = plot_result["plots"][0]
    assert entry["stem"] == "organization-org-a__models__tokens"
    plots = output / "charts"  # charts live in the run they draw (design §7)
    html = plots / entry["outputs"]["html"]
    assert html.is_file() and b"plotly" in html.read_bytes().lower()
    assert (plots / entry["outputs"]["png"]).is_file()


def test_wrapper_invalid_input_has_machine_readable_channels(tmp_path):
    result = _run([ROOT / "econ-forecast/utils/forecast.sh", "--config", "missing.yaml",
                   "--origin", "not-a-date", "--unit", "month", "--validate-only"], tmp_path)
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "invalid"
    assert all(json.loads(line) for line in result.stderr.splitlines())
