"""`load_curve_artifact`/`resolve_curve_ref`: verified reading, tamper rejection, and
`name`/`name:id` resolution (Task 2's curve-artifact consumer, `forecast/curve/artifacts.py`).
"""
from __future__ import annotations

import json

import pytest

from forecast.curve.artifacts import load_curve_artifact, resolve_curve_ref
from forecast.curve.config import CurveConfig
from forecast.curve.errors import ArtifactError
from forecast.curve.model import resolve_anchors
from forecast.curve.runner import config_hash, run_curve

FLAT = [[0, "1.0"], [1, "1.0"]]


def _curve_config(name="test-curve", anchors=None, axis=(0, 1)) -> CurveConfig:
    return CurveConfig.model_validate({
        "schema_version": 1, "name": name, "interp": "pchip",
        "axis": {"start": axis[0], "end": axis[1]},
        "anchors": anchors or [[axis[0], "1.0"], [axis[1], "2.0"]],
        "preview": {}, "description": None,
    })


def _build(root, **kwargs):
    """Build one real, complete curve artifact under `root`; return (config, directory)."""
    config = _curve_config(**kwargs)
    result = run_curve(config, root)
    assert result is not None
    return config, result.output_dir


# --- load_curve_artifact: happy path -------------------------------------------

def test_load_curve_artifact_returns_correct_fields(tmp_path):
    config, directory = _build(tmp_path, name="ramp", axis=(2, 6),
                               anchors=[[2, "1.0"], [4, "+0.5"], [6, "-0.2"]])
    artifact = load_curve_artifact(directory)
    assert artifact.name == "ramp"
    assert artifact.curve_id == config_hash(config)
    assert artifact.interp == "pchip"
    assert artifact.axis == (2, 6)
    assert artifact.anchors == resolve_anchors(config.anchors, 2, 6)


# --- load_curve_artifact: negative cases (each isolates ONE broken invariant) ---

def test_missing_directory_rejected(tmp_path):
    with pytest.raises(ArtifactError, match="not found"):
        load_curve_artifact(tmp_path / "nope")


def test_missing_manifest_rejected(tmp_path):
    _, directory = _build(tmp_path)
    (directory / "manifest.json").unlink()
    with pytest.raises(ArtifactError, match="no manifest"):
        load_curve_artifact(directory)


def test_manifest_replaced_by_symlink_rejected(tmp_path):
    _, directory = _build(tmp_path)
    elsewhere = tmp_path / "elsewhere-manifest.json"
    elsewhere.write_text((directory / "manifest.json").read_text(), encoding="utf-8")
    (directory / "manifest.json").unlink()
    (directory / "manifest.json").symlink_to(elsewhere)
    with pytest.raises(ArtifactError, match="no manifest"):
        load_curve_artifact(directory)


def test_incomplete_status_rejected(tmp_path):
    _, directory = _build(tmp_path)
    manifest_path = directory / "manifest.json"
    data = json.loads(manifest_path.read_text())
    data["status"] = "partial"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ArtifactError, match="not complete"):
        load_curve_artifact(directory)


def test_missing_config_sha256_rejected(tmp_path):
    _, directory = _build(tmp_path)
    manifest_path = directory / "manifest.json"
    data = json.loads(manifest_path.read_text())
    del data["config_sha256"]
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ArtifactError, match="config_sha256"):
        load_curve_artifact(directory)


@pytest.mark.parametrize("bad_digest", ["deadbeef", "z" * 64, 12345])
def test_malformed_config_sha256_rejected(tmp_path, bad_digest):
    _, directory = _build(tmp_path)
    manifest_path = directory / "manifest.json"
    data = json.loads(manifest_path.read_text())
    data["config_sha256"] = bad_digest
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ArtifactError, match="config_sha256"):
        load_curve_artifact(directory)


def test_tampered_config_yaml_rejected(tmp_path):
    _, directory = _build(tmp_path)
    config_path = directory / "config.yaml"
    config_path.write_bytes(config_path.read_bytes() + b"\n# tampered\n")
    with pytest.raises(ArtifactError, match="checksum mismatch"):
        load_curve_artifact(directory)


def test_directory_id_mismatch_rejected(tmp_path):
    _, directory = _build(tmp_path)
    wrong = directory.parent / "deadbeefdeadbeef"
    directory.rename(wrong)
    with pytest.raises(ArtifactError, match="directory id"):
        load_curve_artifact(wrong)


def test_directory_name_mismatch_rejected(tmp_path):
    _, directory = _build(tmp_path)
    renamed_parent = directory.parent.parent / "renamed-name"
    directory.parent.rename(renamed_parent)
    moved = renamed_parent / directory.name
    with pytest.raises(ArtifactError, match="directory name"):
        load_curve_artifact(moved)


def test_missing_config_yaml_rejected(tmp_path):
    _, directory = _build(tmp_path)
    (directory / "config.yaml").unlink()
    with pytest.raises(ArtifactError, match="missing config.yaml"):
        load_curve_artifact(directory)


def test_config_yaml_replaced_by_symlink_rejected(tmp_path):
    _, directory = _build(tmp_path)
    elsewhere = tmp_path / "elsewhere-config.yaml"
    elsewhere.write_bytes((directory / "config.yaml").read_bytes())
    (directory / "config.yaml").unlink()
    (directory / "config.yaml").symlink_to(elsewhere)
    with pytest.raises(ArtifactError, match="missing config.yaml"):
        load_curve_artifact(directory)


# --- resolve_curve_ref ----------------------------------------------------------

def test_resolve_curve_ref_name_id_resolves(tmp_path):
    config, directory = _build(tmp_path, name="solo")
    assert resolve_curve_ref(tmp_path, f"solo:{config_hash(config)}") == directory


def test_resolve_curve_ref_bare_unique_name_resolves(tmp_path):
    _, directory = _build(tmp_path, name="solo")
    assert resolve_curve_ref(tmp_path, "solo") == directory


def test_resolve_curve_ref_missing_name_directory_rejected(tmp_path):
    with pytest.raises(ArtifactError, match="no curve artifacts named"):
        resolve_curve_ref(tmp_path, "nope")


def test_resolve_curve_ref_zero_complete_rejected(tmp_path):
    (tmp_path / "orphan" / "incomplete-id").mkdir(parents=True)
    with pytest.raises(ArtifactError, match="no complete curve artifact"):
        resolve_curve_ref(tmp_path, "orphan")


def test_resolve_curve_ref_ambiguous_rejected(tmp_path):
    _build(tmp_path, name="dup", anchors=[[0, "1.0"], [1, "1.0"]])
    _build(tmp_path, name="dup", anchors=[[0, "1.0"], [1, "2.0"]])
    with pytest.raises(ArtifactError, match="ambiguous"):
        resolve_curve_ref(tmp_path, "dup")


def test_resolve_curve_ref_ignores_incomplete_orphaned_sibling(tmp_path):
    _, directory = _build(tmp_path, name="mix")
    (directory.parent / "orphan-id").mkdir()
    assert resolve_curve_ref(tmp_path, "mix") == directory


@pytest.mark.parametrize("bad_ref", [
    "Bad Name", "name:short", "name:0123456789ABCDEF",
    "", ":0123456789abcdef", "name:0123456789abcdef0",
])
def test_resolve_curve_ref_bad_format_rejected(tmp_path, bad_ref):
    with pytest.raises(ArtifactError, match="invalid curve reference"):
        resolve_curve_ref(tmp_path, bad_ref)
