"""The shipped config file is what runs, and cannot drift from the model's defaults.

`cli_normalize --config` used to default to `None`, so every real run silently used the
built-in Python defaults and `config/normalizer/claude_code.yaml` was documentation pretending
to be configuration. Every other service (uploader, ingest, worker, storage) already defaults
to its own `config/<svc>.yaml`; this was the outlier.

The model keeps its defaults because 37 sites construct `NormalizerConfig(...)` directly, nearly
all tests passing one kwarg to exercise one behaviour — a text-cap test should not need a YAML
file. That leaves two places a value can live, so this asserts they agree.
"""
from pathlib import Path

import pytest
import yaml

from collector.cli_normalize import DEFAULT_CONFIG
from collector.normalizer.config import NormalizerConfig, load_config

REPO = Path(__file__).resolve().parents[3]
SHIPPED = REPO / "config/normalizer/claude_code.yaml"


def test_the_default_config_path_exists():
    """A missing file is an error at startup, not a silent fallback to the defaults."""
    assert DEFAULT_CONFIG == SHIPPED
    assert SHIPPED.is_file()


def test_shipped_config_equals_the_model_defaults():
    """The guard. Change a default in code and not the file — or the reverse — and this fails.

    Not cosmetic: without it the file drifts, someone reads it to learn what a run did, and is
    told something untrue.
    """
    assert load_config(SHIPPED) == NormalizerConfig()


def test_shipped_config_states_every_field():
    """A run must be fully described by the file. A field present in the model but absent from
    the file would be supplied silently by a default nobody read."""
    on_disk = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
    missing = sorted(set(NormalizerConfig().model_dump()) - set(on_disk))
    assert not missing, f"config fields not stated in the shipped file: {missing}"


def test_result_length_policy_is_in_the_file():
    """These two are the security-relevant part: together they decide how much of a tool
    result -- including whole files -- reaches `raw`. They have to be reviewable on disk, not
    only in code. Bodies default to 0 (dropped); everything else is capped, not kept whole,
    so a new vendor key holding content cannot arrive unbounded."""
    on_disk = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
    assert on_disk["tool_result_content_len"] == 0
    assert on_disk["tool_result_len"] == 1000


def test_a_missing_config_is_an_error_not_a_fallback(tmp_path, capsys):
    """Falling back is how a run ends up configured by something nobody looked at."""
    from collector import cli_normalize

    with pytest.raises(SystemExit) as exc:
        cli_normalize.main([
            str(tmp_path / "session.jsonl"),
            "--out", str(tmp_path / "out.json"),
            "--config", str(tmp_path / "nope.yaml"),
        ])
    assert exc.value.code != 0
    assert "config not found" in capsys.readouterr().err
