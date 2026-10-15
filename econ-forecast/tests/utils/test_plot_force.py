"""`--force` on the plot-forecast CLI, against the design section 7 `charts/`
contract: an existing `<artifact>/charts/` is refused unless `--force`, which
replaces it outright (never anything else in the run). Both single-plot and
`--config` batch mode share one driver (`run.run_forecast_plots`).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WRAPPER = ROOT / "econ-forecast" / "utils" / "plot_forecast.sh"

_fixture_path = Path(__file__).with_name("test_plot_forecast_cli.py")
_spec = importlib.util.spec_from_file_location("_plot_force_fixtures", _fixture_path)
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
make_run = _fixtures.make_run


def _run(*args, cwd=None):
    return subprocess.run([str(WRAPPER), *map(str, args)], cwd=cwd, text=True,
                          capture_output=True, env={**os.environ, "NO_COLOR": "1"}, timeout=60)


def test_force_replaces_an_existing_charts_dir(tmp_path):
    run = make_run(tmp_path / "run")
    charts = run / "charts"
    charts.mkdir()
    (charts / "stray.txt").write_text("stale", encoding="utf-8")

    result = _run("--artifact", run, "--scope-type", "team", "--scope-name", "Payments", "--force")
    assert result.returncode == 0, result.stderr
    assert not (charts / "stray.txt").exists()
    assert (charts / "team-payments__models__tokens.html").exists()


def test_force_refuses_a_charts_path_that_is_not_a_directory(tmp_path):
    run = make_run(tmp_path / "run")
    (run / "charts").write_bytes(b"not a directory")

    result = _run("--artifact", run, "--scope-type", "organization", "--force")
    assert result.returncode == 1
    assert "not a directory" in json.loads(result.stderr.splitlines()[-1])["message"]
    assert (run / "charts").is_file()
    assert (run / "charts").read_bytes() == b"not a directory"


def test_force_confines_deletion_to_charts_and_leaves_the_rest_of_the_run(tmp_path):
    run = make_run(tmp_path / "run")
    charts = run / "charts"
    charts.mkdir()
    (charts / "stray.txt").write_text("stale", encoding="utf-8")
    report_before = (run / "report.md").read_text(encoding="utf-8")

    result = _run("--artifact", run, "--scope-type", "team", "--scope-name", "Payments", "--force")
    assert result.returncode == 0, result.stderr
    assert (run / "report.md").read_text(encoding="utf-8") == report_before
    assert (run / "manifest.json").is_file()
    assert not (charts / "stray.txt").exists()


def test_force_refuses_a_charts_symlink(tmp_path):
    run = make_run(tmp_path / "run")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (run / "charts").symlink_to(elsewhere)

    result = _run("--artifact", run, "--scope-type", "organization", "--force")
    assert result.returncode == 1
    assert "symlink" in json.loads(result.stderr.splitlines()[-1])["message"]
    assert (run / "charts").is_symlink()


def test_config_mode_honors_force(tmp_path):
    run = make_run(tmp_path / "run")
    charts = run / "charts"
    charts.mkdir()
    (charts / "stray.txt").write_text("stale", encoding="utf-8")
    config = tmp_path / "plot.yaml"
    config.write_text(yaml.safe_dump({
        "schema_version": 1,
        "forecast": [{"scope_type": "team", "scope_name": "Payments"}],
    }), encoding="utf-8")

    refused = _run("--artifact", run, "--config", config)
    assert refused.returncode == 1
    assert (charts / "stray.txt").exists()

    forced = _run("--artifact", run, "--config", config, "--force")
    assert forced.returncode == 0, forced.stderr
    assert not (charts / "stray.txt").exists()
    assert (charts / "team-payments__models__tokens.html").exists()


def test_result_json_records_the_run_relative_to_charts(tmp_path):
    run = make_run(tmp_path / "run")

    result = _run("--artifact", run, "--scope-type", "team", "--scope-name", "Payments")
    assert result.returncode == 0, result.stderr
    record = json.loads((run / "charts" / "result.json").read_text(encoding="utf-8"))
    assert record["artifact"] == ".."
    dataset = record["forecast_data"]["dataset"]
    assert dataset is None or not Path(dataset).is_absolute()
