"""`run_curve` behavior: hashing, artifact contents, no-op idempotency, and the
incomplete/orphaned-directory guard."""
from __future__ import annotations

import json

import pytest

from forecast.curve.config import CurveConfig
from forecast.curve.errors import ArtifactError
from forecast.curve.model import evaluate_curve, resolve_anchors
from forecast.curve.runner import config_digest, config_hash, run_curve


def _config_dict(**overrides) -> dict:
    config = {
        "schema_version": 1, "name": "test-curve", "interp": "pchip",
        "axis": {"start": 1, "end": 10}, "anchors": [[1, "1.0"], [10, "2.0"]],
        "preview": {"days": 6}, "description": "test fixture curve",
    }
    config.update(overrides)
    return config


def _config(**overrides) -> CurveConfig:
    return CurveConfig.model_validate(_config_dict(**overrides))


# --- config_hash / config_digest ---------------------------------------------

def test_config_hash_is_deterministic():
    a = _config()
    b = _config()
    assert config_hash(a) == config_hash(b)
    assert config_digest(a) == config_digest(b)
    assert config_hash(a) == config_digest(a)[:16]


def test_config_hash_is_sensitive_to_preview_days():
    a = _config(preview={"days": 5})
    b = _config(preview={"days": 9})
    assert config_hash(a) != config_hash(b)


def test_config_hash_is_sensitive_to_anchors_and_name():
    base = _config()
    different_anchor = _config(anchors=[[1, "1.0"], [10, "3.0"]])
    different_name = _config(name="other-name")
    assert config_hash(base) != config_hash(different_anchor)
    assert config_hash(base) != config_hash(different_name)


# --- artifact contents --------------------------------------------------------

def test_curve_jsonl_matches_evaluate_curve_exactly(tmp_path):
    config = _config(preview={"days": 7})
    result = run_curve(config, tmp_path)
    resolved = resolve_anchors(config.anchors, config.axis.start, config.axis.end)
    expected = evaluate_curve(resolved, 7)
    rows = [json.loads(line) for line in (result.output_dir / "curve.jsonl").read_text().splitlines()]
    assert len(rows) == 7
    for row, level in zip(rows, expected):
        assert row["level"] == pytest.approx(level)


def test_default_sample_count_is_two_hundred_when_no_preview_days_set(tmp_path):
    config = _config(preview={})
    result = run_curve(config, tmp_path)
    rows = (result.output_dir / "curve.jsonl").read_text().splitlines()
    assert len(rows) == 200
    manifest = json.loads((result.output_dir / "manifest.json").read_text())
    assert manifest["sample_count"] == 200


def test_all_six_artifact_files_present(tmp_path):
    config = _config(preview={"days": 5})
    result = run_curve(config, tmp_path)
    names = {path.name for path in result.output_dir.iterdir()}
    assert names == {"config.yaml", "curve.jsonl", "manifest.json",
                      "preview.html", "preview.png", "report.md"}


def test_manifest_written_last_with_complete_status_and_matching_hashes(tmp_path):
    import hashlib

    config = _config(preview={"days": 5})
    result = run_curve(config, tmp_path)
    manifest = json.loads((result.output_dir / "manifest.json").read_text())
    assert manifest["status"] == "complete"
    assert manifest["config_sha256"] == config_digest(config)
    # The manifest can only correctly record hashes of files that already existed when
    # it was computed -- i.e. it was written after them, not concurrently or before.
    report_bytes = (result.output_dir / "report.md").read_bytes()
    assert manifest["report"]["sha256"] == hashlib.sha256(report_bytes).hexdigest()
    for name in ("config.yaml", "preview.html", "preview.png", "curve.jsonl"):
        payload = (result.output_dir / name).read_bytes()
        assert manifest["files"][name]["sha256"] == hashlib.sha256(payload).hexdigest()


def test_config_yaml_persists_the_resolved_config(tmp_path):
    import yaml

    config = _config(preview={"days": 5}, description="persisted description")
    result = run_curve(config, tmp_path)
    saved = yaml.safe_load((result.output_dir / "config.yaml").read_text())
    assert saved["name"] == config.name
    assert saved["description"] == "persisted description"
    assert saved["preview"]["days"] == 5


# --- idempotent no-op ----------------------------------------------------------

def test_identical_config_rerun_returns_none_and_writes_nothing(tmp_path):
    config = _config(preview={"days": 5})
    first = run_curve(config, tmp_path)
    before = {path: path.stat().st_mtime_ns for path in first.output_dir.rglob("*")}
    second = run_curve(config, tmp_path)
    assert second is None
    after = {path: path.stat().st_mtime_ns for path in first.output_dir.rglob("*")}
    assert before == after


def test_different_preview_days_create_different_directories(tmp_path):
    a = _config(preview={"days": 5})
    b = _config(preview={"days": 9})
    result_a = run_curve(a, tmp_path)
    result_b = run_curve(b, tmp_path)
    assert result_a.output_dir != result_b.output_dir
    assert result_a.output_dir.parent == result_b.output_dir.parent


# --- incomplete / orphaned directory guard --------------------------------------

def test_incomplete_directory_missing_manifest_raises_artifact_error(tmp_path):
    config = _config(preview={"days": 5})
    first = run_curve(config, tmp_path)
    (first.output_dir / "manifest.json").unlink()
    with pytest.raises(ArtifactError, match="incomplete prior artifact"):
        run_curve(config, tmp_path)


def test_incomplete_directory_status_not_complete_raises_artifact_error(tmp_path):
    config = _config(preview={"days": 5})
    first = run_curve(config, tmp_path)
    manifest_path = first.output_dir / "manifest.json"
    data = json.loads(manifest_path.read_text())
    data["status"] = "partial"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ArtifactError, match="incomplete prior artifact"):
        run_curve(config, tmp_path)


def test_incomplete_directory_unparseable_manifest_raises_artifact_error(tmp_path):
    config = _config(preview={"days": 5})
    first = run_curve(config, tmp_path)
    manifest_path = first.output_dir / "manifest.json"
    manifest_path.write_text("not valid json {{{", encoding="utf-8")
    with pytest.raises(ArtifactError, match="incomplete prior artifact"):
        run_curve(config, tmp_path)


def test_incomplete_directory_is_not_overwritten(tmp_path):
    config = _config(preview={"days": 5})
    first = run_curve(config, tmp_path)
    manifest_path = first.output_dir / "manifest.json"
    manifest_path.unlink()
    other_file = first.output_dir / "config.yaml"
    original_bytes = other_file.read_bytes()
    with pytest.raises(ArtifactError):
        run_curve(config, tmp_path)
    assert other_file.read_bytes() == original_bytes
    assert not manifest_path.exists()
