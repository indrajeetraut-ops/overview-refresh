"""Shared fixtures/factories for curve module tests."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def curve_config_dict(**overrides) -> dict:
    """A minimal, valid shape-curve config as a plain dict; override any top-level key."""
    config = {
        "schema_version": 1,
        "name": "test-curve",
        "interp": "pchip",
        "axis": {"start": 1, "end": 10},
        "anchors": [[1, "1.0"], [10, "2.0"]],
        "preview": {"days": 6},
        "description": "test fixture curve",
    }
    config.update(overrides)
    return config


def write_curve_config(path: Path, **overrides) -> Path:
    """Write a minimal valid curve YAML config to `path`; return the path."""
    path.write_text(yaml.safe_dump(curve_config_dict(**overrides), sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def curve_config_factory():
    return write_curve_config


@pytest.fixture
def curve_config_path(tmp_path) -> Path:
    return write_curve_config(tmp_path / "curve.yaml")
