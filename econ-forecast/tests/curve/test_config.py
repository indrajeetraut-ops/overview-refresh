"""Config loading and validation: `load_curve_config` must raise curve `ConfigError`
(not just any exception) on every malformed input, and load valid configs faithfully."""
from __future__ import annotations

import pytest

from forecast.curve.config import load_curve_config
from forecast.curve.errors import ConfigError


def test_valid_config_round_trips_all_fields(curve_config_factory, tmp_path):
    path = curve_config_factory(
        tmp_path / "curve.yaml",
        name="q3-ramp-and-plateau",
        axis={"start": 1, "end": 14},
        anchors=[[1, "10"], [5, "-0.5"], [6, "-0.8"], [7, "+4"], [14, "12"]],
        preview={"days": 60, "baseline_tokens": 1000000},
        description="ramp up then gentle decline",
    )
    config = load_curve_config(path)
    assert config.schema_version == 1
    assert config.name == "q3-ramp-and-plateau"
    assert config.interp == "pchip"
    assert (config.axis.start, config.axis.end) == (1, 14)
    assert config.anchors == [(1, "10"), (5, "-0.5"), (6, "-0.8"), (7, "+4"), (14, "12")]
    assert config.preview.days == 60
    assert config.preview.baseline_tokens == 1000000
    assert config.description == "ramp up then gentle decline"


def test_default_preview_is_optional(curve_config_factory, tmp_path):
    path = curve_config_factory(tmp_path / "curve.yaml", preview={})
    config = load_curve_config(path)
    assert config.preview.days is None
    assert config.preview.baseline_tokens is None


def test_bad_interp_rejected(curve_config_factory, tmp_path):
    path = curve_config_factory(tmp_path / "curve.yaml", interp="cubic")
    with pytest.raises(ConfigError):
        load_curve_config(path)


def test_fewer_than_two_anchors_rejected(curve_config_factory, tmp_path):
    path = curve_config_factory(tmp_path / "curve.yaml", anchors=[[1, "1.0"]])
    with pytest.raises(ConfigError):
        load_curve_config(path)


def test_non_increasing_positions_rejected(curve_config_factory, tmp_path):
    path = curve_config_factory(
        tmp_path / "curve.yaml",
        axis={"start": 1, "end": 10},
        anchors=[[1, "1.0"], [5, "2.0"], [3, "3.0"], [10, "4.0"]],
    )
    with pytest.raises(ConfigError):
        load_curve_config(path)


def test_endpoint_violation_rejected(curve_config_factory, tmp_path):
    path = curve_config_factory(
        tmp_path / "curve.yaml",
        axis={"start": 1, "end": 10},
        anchors=[[2, "1.0"], [10, "2.0"]],
    )
    with pytest.raises(ConfigError):
        load_curve_config(path)


def test_axis_bounds_violation_rejected(curve_config_factory, tmp_path):
    path = curve_config_factory(
        tmp_path / "curve.yaml",
        axis={"start": 1, "end": 10},
        anchors=[[1, "1.0"], [15, "2.0"], [10, "3.0"]],
    )
    with pytest.raises(ConfigError):
        load_curve_config(path)


def test_negative_level_walk_rejected(curve_config_factory, tmp_path):
    path = curve_config_factory(
        tmp_path / "curve.yaml",
        axis={"start": 1, "end": 10},
        anchors=[[1, "2.0"], [10, "-5.0"]],
    )
    with pytest.raises(ConfigError, match=r"position 10.*negative level"):
        load_curve_config(path)


def test_leading_delta_first_anchor_rejected(curve_config_factory, tmp_path):
    path = curve_config_factory(
        tmp_path / "curve.yaml",
        axis={"start": 1, "end": 10},
        anchors=[[1, "+5.0"], [10, "2.0"]],
    )
    with pytest.raises(ConfigError):
        load_curve_config(path)


def test_missing_name_rejected(curve_config_factory, tmp_path):
    path = curve_config_factory(tmp_path / "curve.yaml")
    text = path.read_text(encoding="utf-8")
    path.write_text("\n".join(line for line in text.splitlines() if not line.startswith("name:")),
                    encoding="utf-8")
    with pytest.raises(ConfigError):
        load_curve_config(path)


@pytest.mark.parametrize("bad_name", ["", "  ", "has spaces", "has/slash", "has:colon"])
def test_invalid_name_characters_rejected(curve_config_factory, tmp_path, bad_name):
    path = curve_config_factory(tmp_path / "curve.yaml", name=bad_name)
    with pytest.raises(ConfigError):
        load_curve_config(path)


def test_duplicate_yaml_keys_rejected(tmp_path):
    path = tmp_path / "curve.yaml"
    path.write_text(
        "schema_version: 1\n"
        "schema_version: 1\n"
        "name: dup\n"
        "interp: pchip\n"
        "axis: {start: 1, end: 10}\n"
        'anchors: [[1, "1.0"], [10, "2.0"]]\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_curve_config(path)


def test_unquoted_plus_yaml_trap_rejected(tmp_path):
    # `+2` unquoted parses as the YAML int 2, not the string "+2" -- must be a loud
    # ConfigError, never a silently-accepted absolute value of 2.
    path = tmp_path / "curve.yaml"
    path.write_text(
        "schema_version: 1\n"
        "name: trap\n"
        "interp: pchip\n"
        "axis: {start: 1, end: 10}\n"
        "anchors:\n"
        '  - [1, "5"]\n'
        "  - [10, +2]\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_curve_config(path)


def test_missing_config_file_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_curve_config(tmp_path / "does-not-exist.yaml")


def test_unknown_field_rejected(curve_config_factory, tmp_path):
    path = curve_config_factory(tmp_path / "curve.yaml", extra_unknown_field="nope")
    with pytest.raises(ConfigError):
        load_curve_config(path)
