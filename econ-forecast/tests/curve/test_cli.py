"""CLI dispatch: exit codes, stdout/stderr JSON channels, validate-only, no-op, and
`--preview-days` hash-folding, invoked as a real subprocess (mirrors
`tests/synthetic/test_interfaces.py`). One smoke test also exercises the actual
`utils/curve.sh` wrapper script."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PYTHON = ROOT / "venv" / "bin" / "python"
WRAPPER = ROOT / "econ-forecast" / "utils" / "curve.sh"
ENV = {**os.environ, "PYTHONPATH": f"{ROOT / 'econ-forecast' / 'src'}:{ROOT / 'src'}"}


def cli(*args, cwd=None, env=None):
    return subprocess.run([str(PYTHON), "-m", "forecast.curve.cli_curve", *map(str, args)],
                          cwd=cwd or ROOT, env=env or ENV, text=True, capture_output=True,
                          timeout=60)


def test_help_and_invalid_arguments_do_no_io(tmp_path):
    help_result = cli("--help", cwd=tmp_path)
    assert help_result.returncode == 0
    assert "--preview-days" in help_result.stdout
    assert list(tmp_path.iterdir()) == []

    missing_config = cli(cwd=tmp_path)
    assert missing_config.returncode == 2

    missing_output = cli("--config", "curve.yaml", cwd=tmp_path)
    assert missing_output.returncode == 2

    bad_preview_days = cli("--config", "curve.yaml", "--validate-only",
                          "--preview-days", "0", cwd=tmp_path)
    assert bad_preview_days.returncode == 2
    assert list(tmp_path.iterdir()) == []


def test_validate_only_writes_nothing_and_hash_matches_a_real_run(curve_config_factory, tmp_path):
    config = curve_config_factory(tmp_path / "curve.yaml", preview={"days": 5})
    validated = cli("--config", config, "--validate-only")
    assert validated.returncode == 0
    summary = json.loads(validated.stdout)
    assert summary["status"] == "valid"
    assert summary["files"] == [] and summary["output_dir"] is None and summary["report_path"] is None

    output = tmp_path / "artifacts"
    real = cli("--config", config, "--output", output)
    assert real.returncode == 0, real.stderr
    real_summary = json.loads(real.stdout)
    assert real_summary["config_hash"] == summary["config_hash"]


def test_validate_only_with_preview_days_override_matches_real_run_hash(curve_config_factory, tmp_path):
    config = curve_config_factory(tmp_path / "curve.yaml", preview={"days": 60})
    validated = cli("--config", config, "--validate-only", "--preview-days", "45")
    summary = json.loads(validated.stdout)

    output = tmp_path / "artifacts"
    real = cli("--config", config, "--output", output, "--preview-days", "45")
    real_summary = json.loads(real.stdout)
    assert real_summary["config_hash"] == summary["config_hash"]

    # The config's own baked-in preview.days (60) must NOT be what got hashed/generated.
    manifest = json.loads((Path(real_summary["output_dir"]) / "manifest.json").read_text())
    assert manifest["sample_count"] == 45


def test_exit_codes_success_config_error_and_artifact_error(curve_config_factory, tmp_path):
    good = curve_config_factory(tmp_path / "good.yaml", preview={"days": 5})
    ok = cli("--config", good, "--output", tmp_path / "out1")
    assert ok.returncode == 0, ok.stderr

    bad = curve_config_factory(tmp_path / "bad.yaml", interp="cubic")
    config_error = cli("--config", bad, "--output", tmp_path / "out2")
    assert config_error.returncode == 2
    last_log = json.loads(config_error.stderr.splitlines()[-1])
    assert last_log["error_category"] == "ConfigError"
    # A failure also emits a summary on stdout, mirroring the success shape.
    error_summary = json.loads(config_error.stdout)
    assert error_summary["status"] == "error"
    assert error_summary["error_category"] == "ConfigError"
    assert error_summary["message"]

    artifact_target = curve_config_factory(tmp_path / "artifact.yaml", name="artifact-curve",
                                           preview={"days": 5})
    out3 = tmp_path / "out3"
    first = cli("--config", artifact_target, "--output", out3)
    assert first.returncode == 0, first.stderr
    config_hash = json.loads(first.stdout)["config_hash"]
    manifest_path = out3 / "artifact-curve" / config_hash / "manifest.json"
    manifest_path.unlink()
    artifact_error = cli("--config", artifact_target, "--output", out3)
    assert artifact_error.returncode == 1
    last_log = json.loads(artifact_error.stderr.splitlines()[-1])
    assert last_log["error_category"] == "ArtifactError"
    error_summary = json.loads(artifact_error.stdout)
    assert error_summary["status"] == "error"
    assert error_summary["error_category"] == "ArtifactError"


def test_noop_path_reports_unchanged_and_exits_zero(curve_config_factory, tmp_path):
    config = curve_config_factory(tmp_path / "curve.yaml", preview={"days": 5})
    output = tmp_path / "out"
    first = cli("--config", config, "--output", output)
    assert first.returncode == 0, first.stderr
    second = cli("--config", config, "--output", output)
    assert second.returncode == 0
    summary = json.loads(second.stdout)
    assert summary["status"] == "unchanged"
    assert summary["files"] == []
    assert summary["config_hash"] == json.loads(first.stdout)["config_hash"]


def test_different_preview_days_create_different_directories_and_rerun_noops(curve_config_factory, tmp_path):
    config = curve_config_factory(tmp_path / "curve.yaml", preview={"days": 60})
    output = tmp_path / "out"
    first = cli("--config", config, "--output", output, "--preview-days", "5")
    second = cli("--config", config, "--output", output, "--preview-days", "9")
    assert first.returncode == 0 and second.returncode == 0
    first_summary = json.loads(first.stdout)
    second_summary = json.loads(second.stdout)
    assert first_summary["config_hash"] != second_summary["config_hash"]
    assert first_summary["output_dir"] != second_summary["output_dir"]

    rerun = cli("--config", config, "--output", output, "--preview-days", "5")
    assert rerun.returncode == 0
    assert json.loads(rerun.stdout)["status"] == "unchanged"


def test_stdout_is_one_json_object_and_stderr_lines_are_all_json(curve_config_factory, tmp_path):
    config = curve_config_factory(tmp_path / "curve.yaml", preview={"days": 5})
    result = cli("--config", config, "--output", tmp_path / "out")
    assert result.returncode == 0, result.stderr
    # Exactly one line of stdout (a single JSON object), nothing else mixed in.
    stdout_lines = result.stdout.splitlines()
    assert len(stdout_lines) == 1
    json.loads(stdout_lines[0])
    for line in result.stderr.splitlines():
        json.loads(line)


def test_open_flag_exercises_the_browser_path_without_crashing(curve_config_factory, tmp_path):
    config = curve_config_factory(tmp_path / "curve.yaml", preview={"days": 5})
    # $BROWSER makes Python's webbrowser module invoke a harmless no-op command
    # instead of launching a real browser window.
    env = {**ENV, "BROWSER": "true"}
    result = cli("--config", config, "--output", tmp_path / "out", "--open", env=env)
    assert result.returncode == 0
    assert "Traceback" not in result.stderr


def test_wrapper_script_validate_only_smoke(curve_config_factory, tmp_path):
    config = curve_config_factory(tmp_path / "curve.yaml", preview={"days": 5})
    result = subprocess.run([str(WRAPPER), "--config", str(config), "--validate-only"],
                            cwd=tmp_path, env=ENV, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "valid"


def test_error_summary_shape_and_values(curve_config_factory, tmp_path):
    # A config whose deltas walk the level negative (pos 10 -> 1.0 - 5.0 = -4.0).
    bad = curve_config_factory(
        tmp_path / "bad.yaml",
        axis={"start": 1, "end": 10},
        anchors=[[1, "1.0"], [10, "-5.0"]],
    )
    result = cli("--config", bad, "--validate-only")
    assert result.returncode == 2

    # Exactly one JSON object on stdout, just like the success case.
    stdout_lines = result.stdout.splitlines()
    assert len(stdout_lines) == 1
    error_summary = json.loads(stdout_lines[0])

    # Shape: the error summary carries every key of a success summary, plus the two
    # error-only fields. Derive the success key set from a real valid run.
    good = curve_config_factory(tmp_path / "good.yaml")
    ok_summary = json.loads(cli("--config", good, "--validate-only").stdout)
    assert set(error_summary) == set(ok_summary) | {"error_category", "message"}

    # Values.
    assert error_summary["status"] == "error"
    assert error_summary["error_category"] == "ConfigError"
    assert error_summary["config_hash"] is None       # config never resolved
    assert error_summary["name"] is None
    assert error_summary["output_dir"] is None
    assert error_summary["report_path"] is None
    assert error_summary["run_id"] is None            # validate-only
    assert error_summary["files"] == []
    # Message is specific: it names the offending anchor position and that it went negative.
    assert "position 10" in error_summary["message"]
    assert "negative" in error_summary["message"]

    # stderr still carries the machine log with the same category.
    assert json.loads(result.stderr.splitlines()[-1])["error_category"] == "ConfigError"
