"""Tier-1 basic acceptance for `--force` on the curve authoring tool (Phase 0).

Curve's "existing" semantics differ from the other three tools: a complete, matching
artifact is normally a friendly no-op (not a refusal), and only an incomplete/orphaned
directory is refused (see `test_runner.py`, kept green and unmodified). `--force` here
always clears and rebuilds the computed `<name>/<hash>/` leaf -- for either case -- but
must never touch the `--output` artifacts root or any sibling curve living under it.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from forecast.curve.config import CurveConfig
from forecast.curve.errors import ArtifactError
from forecast.curve.runner import config_hash, run_curve

ROOT = Path(__file__).resolve().parents[3]
PYTHON = ROOT / "venv" / "bin" / "python"
ENV = {**os.environ, "PYTHONPATH": f"{ROOT / 'econ-forecast' / 'src'}:{ROOT / 'src'}"}


def _cli(*args, cwd=None):
    return subprocess.run([str(PYTHON), "-m", "forecast.curve.cli_curve", *map(str, args)],
                          cwd=cwd or ROOT, env=ENV, text=True, capture_output=True, timeout=60)


def _config(**overrides) -> CurveConfig:
    config = {
        "schema_version": 1, "name": "test-curve", "interp": "pchip",
        "axis": {"start": 1, "end": 10}, "anchors": [[1, "1.0"], [10, "2.0"]],
        "preview": {"days": 6}, "description": "test fixture curve",
    }
    config.update(overrides)
    return CurveConfig.model_validate(config)


def test_force_rebuilds_a_complete_matching_artifact(tmp_path):
    config = _config()
    first = run_curve(config, tmp_path)
    assert first is not None
    stray = first.output_dir / "stray.txt"
    stray.write_text("leftover", encoding="utf-8")

    second = run_curve(config, tmp_path, force=True)
    assert second is not None
    assert second.output_dir == first.output_dir
    assert not stray.exists()
    manifest = json.loads((second.output_dir / "manifest.json").read_text())
    assert manifest["status"] == "complete"


def test_force_rebuilds_an_incomplete_orphaned_artifact(tmp_path):
    config = _config()
    first = run_curve(config, tmp_path)
    (first.output_dir / "manifest.json").unlink()

    with pytest.raises(ArtifactError, match="incomplete prior artifact"):
        run_curve(config, tmp_path)

    second = run_curve(config, tmp_path, force=True)
    assert second is not None
    assert json.loads((second.output_dir / "manifest.json").read_text())["status"] == "complete"


def test_force_refuses_a_non_directory_target(tmp_path):
    config = _config()
    digest = config_hash(config)
    leaf = tmp_path / config.name / digest
    leaf.parent.mkdir(parents=True)
    leaf.write_bytes(b"not a directory")

    with pytest.raises(ArtifactError, match="not a directory"):
        run_curve(config, tmp_path, force=True)
    assert leaf.is_file()
    assert leaf.read_bytes() == b"not a directory"


def test_force_confines_deletion_to_the_single_leaf(tmp_path):
    kept = _config(preview={"days": 5})
    forced = _config(preview={"days": 9})
    kept_result = run_curve(kept, tmp_path)
    forced_result = run_curve(forced, tmp_path)
    assert kept_result.output_dir != forced_result.output_dir
    kept_before = (kept_result.output_dir / "manifest.json").read_bytes()

    second = run_curve(forced, tmp_path, force=True)
    assert second is not None
    assert second.output_dir == forced_result.output_dir
    # The sibling curve's leaf, under the same --output root, must be untouched.
    assert kept_result.output_dir.is_dir()
    assert (kept_result.output_dir / "manifest.json").read_bytes() == kept_before


def test_force_cli_flag_rebuilds_an_incomplete_artifact(tmp_path, curve_config_factory):
    config = curve_config_factory(tmp_path / "curve.yaml", preview={"days": 5})
    output = tmp_path / "artifacts"
    ok = _cli("--config", config, "--output", output)
    assert ok.returncode == 0, ok.stderr
    summary = json.loads(ok.stdout)
    manifest_path = Path(summary["output_dir"]) / "manifest.json"
    manifest_path.unlink()

    refused = _cli("--config", config, "--output", output)
    assert refused.returncode == 1
    assert not manifest_path.exists()

    forced = _cli("--config", config, "--output", output, "--force")
    assert forced.returncode == 0, forced.stderr
    assert manifest_path.is_file()
