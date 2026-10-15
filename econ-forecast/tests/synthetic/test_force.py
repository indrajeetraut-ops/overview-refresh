"""Tier-1 basic acceptance for `--force` on the synthetic generator (Phase 0).

Covers: refuse-by-default is preserved, `--force` clears exactly the resolved output
directory and proceeds, a non-directory target is refused rather than force-deleted,
deletion never escapes the resolved output path, and force-clearing an output that
would swallow a required input file is refused.
"""
from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
import yaml

from forecast.synthetic import runner
from forecast.synthetic.config import RosterConfig, UsersConfig
from forecast.synthetic.errors import ArtifactError

ROOT = Path(__file__).parents[3]
WRAPPER = ROOT / "econ-forecast/utils/synt_gen.sh"


def _users_config(**overrides) -> UsersConfig:
    data = {"schema_version": 1, "users": {"count": 3, "org_domain": "talium.ai",
                                            "locale": "en_US", "seed": 7}}
    data.update(overrides)
    return UsersConfig.model_validate(data)


def test_refuse_by_default_is_preserved(tmp_path):
    output = tmp_path / "users"
    output.mkdir()
    sentinel = output / "sentinel.bin"
    sentinel.write_bytes(b"preexisting bytes\x00must survive\n")
    with pytest.raises(ArtifactError, match="output path already exists"):
        runner.run_users(_users_config(), output)
    assert sentinel.read_bytes() == b"preexisting bytes\x00must survive\n"
    assert sorted(p.name for p in output.iterdir()) == ["sentinel.bin"]


def test_force_recreates_an_existing_output_directory(tmp_path):
    output = tmp_path / "users"
    output.mkdir()
    (output / "sentinel.bin").write_bytes(b"stale")
    result = runner.run_users(_users_config(), output, force=True)
    assert result.output_dir == output.resolve()
    assert (output / "manifest.json").is_file()
    assert not (output / "sentinel.bin").exists()


def test_force_refuses_a_non_directory_target(tmp_path):
    output = tmp_path / "users"
    output.write_bytes(b"not a directory")
    with pytest.raises(ArtifactError, match="not a directory"):
        runner.run_users(_users_config(), output, force=True)
    assert output.is_file()
    assert output.read_bytes() == b"not a directory"


def test_force_confines_deletion_to_the_resolved_target(tmp_path):
    output = tmp_path / "users"
    output.mkdir()
    (output / "sentinel.bin").write_bytes(b"stale")
    sibling = tmp_path / "users-sibling"
    sibling.mkdir()
    (sibling / "keep-me.txt").write_text("keep", encoding="utf-8")
    runner.run_users(_users_config(), output, force=True)
    assert (sibling / "keep-me.txt").read_text(encoding="utf-8") == "keep"


def test_force_refuses_an_output_that_would_delete_a_required_input(tmp_path):
    users_out = tmp_path / "users"
    runner.run_users(_users_config(), users_out)
    roster_config = RosterConfig.model_validate({"schema_version": 1, "roster": {
        "users_file": users_out / "users.jsonl", "seed": 1,
        "teams": [{"team_name": "team", "count": 3}]}})
    # --output pointed at the users dataset itself: force-clearing it would destroy the
    # roster's own required input. Must refuse, never silently sweep it away.
    with pytest.raises(ArtifactError, match="required input"):
        runner.run_roster(roster_config, users_out, force=True)
    assert (users_out / "users.jsonl").exists()


def test_force_cli_flag_recreates_existing_output(tmp_path):
    config = tmp_path / "users.yaml"
    config.write_text(yaml.safe_dump({"schema_version": 1, "users": {
        "count": 2, "org_domain": "talium.ai", "locale": "en_US", "seed": 3}}), encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()
    (output / "sentinel.bin").write_bytes(b"stale")

    refused = subprocess.run([str(WRAPPER), "--op", "user-create", "--config", str(config),
                              "--output", str(output)], capture_output=True, text=True)
    assert refused.returncode == 1
    assert (output / "sentinel.bin").exists()

    forced = subprocess.run([str(WRAPPER), "--op", "user-create", "--config", str(config),
                             "--output", str(output), "--force"], capture_output=True, text=True)
    assert forced.returncode == 0, forced.stderr
    assert not (output / "sentinel.bin").exists()
    assert (output / "manifest.json").is_file()
