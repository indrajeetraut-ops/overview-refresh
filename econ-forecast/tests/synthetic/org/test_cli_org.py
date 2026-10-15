"""P8/P9: the `org-expand` CLI end to end (`--validate-only`, a fresh generation run, exit
codes, the JSON/stderr contract) and the P9 one-shot chain wrapper (`cli_synth_org.py`):
end-date retiming and a full users->roster->series->org-expand run from one config dir."""
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml


def _run_cli(repo_root, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "forecast.synthetic.cli_synt_gen", *args],
        capture_output=True, text=True, cwd=str(repo_root),
        env={**os.environ, "PYTHONPATH": f"{repo_root}/econ-forecast/src:{repo_root}/src"})


def _run_chain_cli(repo_root, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "forecast.synthetic.org.cli_synth_org", *args],
        capture_output=True, text=True, cwd=str(repo_root),
        env={**os.environ, "PYTHONPATH": f"{repo_root}/econ-forecast/src:{repo_root}/src"})


@pytest.fixture
def tiny_config_dir(tmp_path):
    """A minimal 4-file config-dir for `cli_synth_org.py`: 2 teams x 4 users, 2 repos, 10 days,
    one dated spike and one zero period (to exercise retiming)."""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "users.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "users": {"count": 8, "org_domain": "tiny-chain.test", "seed": 5, "locale": "en_US",
                                       "identity_style": "consistent"}}))
    (cfg_dir / "roster.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "roster": {"users_file": "../users/users.jsonl", "seed": 5,
                                        "teams": [{"team_name": "platform", "count": 4},
                                                 {"team_name": "payments", "count": 4}]}}))
    (cfg_dir / "series.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "series": {"users_file": "../users/users.jsonl", "roster_file": "../roster/teams.jsonl",
                  "start_date": "2026-01-01", "days": 10, "timezone": "UTC",
                  "profile_seed": 6, "usage_seed": 7},
        "defaults": {"daily_variation_pct": 30, "output_ratio": 0.03, "cache_hit_ratio": 0.8,
                     "cache_hit_variation_pp": 5, "cache_write_ratio": 0.997, "cache_write_1h_share": 0.97, "mix_variation_pct": 10,
                     "daily_zero_probability": 0.1,
                     "weekend_reduction_probability": 0.9, "weekend_reduction_factor": 0.25},
        "profiles": {"light_profile": {"daily_input_tokens": 200_000},
                    "mid_profile": {"daily_input_tokens": 900_000}},
        "team_profiles": {
            "platform": {"profile_mix": {"light_profile": 0.5, "mid_profile": 0.5},
                        "project_mix": {"platform_infra": 1.0}},
            "payments": {"profile_mix": {"light_profile": 0.5, "mid_profile": 0.5},
                        "project_mix": {"payments_api": 1.0}}},
        "projects": [{"name": "platform_infra"}, {"name": "payments_api"}],
        "agents": {"claude_code": {"billing_mode": "api", "usage_share": 1.0,
                                   "models": {"claude-sonnet-5": 1.0}}},
        "events": {
            "spike_rules": [{"id": "test-spike", "probability": 1.0,
                            "multiplier_min": 2.0, "multiplier_max": 2.0,
                            "date_range": {"start_date": "2026-01-03", "end_date": "2026-01-04"},
                            "targets": {"teams": ["platform"], "agents": [], "models": []}}],
            "zero_periods": [{"id": "test-zero", "target_type": "org", "target": "tiny-chain.test",
                             "start_date": "2026-01-05", "end_date": "2026-01-05"}],
        },
    }))
    (cfg_dir / "org.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "org": {"series_dir": "../series", "seed": 9, "github_owner": "tinyco",
               "admin": {"github_login": "vp-eng", "display_name": "VP Eng",
                        "email": "vp@tiny-chain.test"},
               "agent_versions": ["2.1.260"]},
        "calibration": {"target_merged_prs_per_dev_week": 3.0},
        "repos": [
            {"project": "platform_infra", "name": "platform-infra",
             "modules": [{"path": "src/", "weight": 1.0, "extensions": {".py": 1.0}}]},
            {"project": "payments_api", "name": "payments-api",
             "modules": [{"path": "services/", "weight": 1.0, "extensions": {".py": 1.0}}]},
        ],
        "teams": {"platform": {}, "payments": {}},
    }))
    return cfg_dir


def test_validate_only_writes_nothing(org_config_dict, write_org_yaml,
                                      repo_root, tmp_path):
    path = write_org_yaml(org_config_dict)
    before = set(tmp_path.iterdir())
    result = _run_cli(repo_root, ["--op", "org-expand", "--config", str(path), "--validate-only"])
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "valid"
    assert summary["operation"] == "org"
    assert summary["files"] == []
    assert summary["output_dir"] is None
    assert summary["report_path"] is None
    assert summary["counts"]["repos"] == 2
    assert summary["counts"]["rostered_users"] == 8
    assert set(tmp_path.iterdir()) == before


def test_successful_generation_run(org_config_dict, write_org_yaml,
                                   repo_root, tmp_path):
    path = write_org_yaml(org_config_dict)
    out = tmp_path / "out"
    result = _run_cli(repo_root, ["--op", "org-expand", "--config", str(path), "--output", str(out)])
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "complete"
    assert summary["operation"] == "org"
    assert out.is_dir()
    assert (out / "manifest.json").is_file()
    assert (out / "report.md").is_file()
    counts = summary["counts"]
    for key in ("teams", "users", "repos", "sessions_root", "sessions_child", "prs",
               "prs_merged", "issues", "reviews", "commits", "links"):
        assert key in counts, key
    for line in result.stderr.splitlines():
        if line.strip():
            json.loads(line)  # every stderr line is a JSON log record


def test_existing_output_exits_1(org_config_dict, write_org_yaml,
                                 repo_root, tmp_path):
    path = write_org_yaml(org_config_dict)
    out = tmp_path / "out"
    out.mkdir()
    result = _run_cli(repo_root, ["--op", "org-expand", "--config", str(path), "--output", str(out)])
    assert result.returncode == 1, result.stderr


def test_bad_config_exits_2(write_org_yaml, repo_root, tmp_path):
    path = write_org_yaml({"schema_version": 1, "org": {"seed": "not-an-int"}})
    out = tmp_path / "out"
    result = _run_cli(repo_root, ["--op", "org-expand", "--config", str(path), "--output", str(out)])
    assert result.returncode == 2, result.stderr
    assert not out.exists()


def test_stderr_lines_are_json(org_config_dict, write_org_yaml, repo_root):
    path = write_org_yaml(org_config_dict)
    result = _run_cli(repo_root, ["--op", "org-expand", "--config", str(path), "--validate-only"])
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert lines
    for line in lines:
        record = json.loads(line)
        assert "event" in record


# --------------------------------------------------------------------------------------------
# P9: cli_synth_org.py -- the one-shot chain wrapper, retiming, and end-to-end generation
# --------------------------------------------------------------------------------------------

def test_retiming_shift_is_correct(tiny_config_dir, repo_root, tmp_path):
    out = tmp_path / "chain-out"
    result = _run_chain_cli(repo_root, ["--config-dir", str(tiny_config_dir), "--output", str(out),
                                        "--end-date", "2026-09-18"])
    assert result.returncode == 0, result.stderr
    retimed = yaml.safe_load((out / "configs" / "series.yaml").read_text())
    assert retimed["series"]["start_date"] == "2026-09-09"  # end - (days=10) + 1

    original_start = date(2026, 1, 1)
    new_start = date(2026, 9, 9)
    delta = new_start - original_start
    spike = retimed["events"]["spike_rules"][0]["date_range"]
    assert spike["start_date"] == (date(2026, 1, 3) + delta).isoformat()
    assert spike["end_date"] == (date(2026, 1, 4) + delta).isoformat()
    zero = retimed["events"]["zero_periods"][0]
    assert zero["start_date"] == (date(2026, 1, 5) + delta).isoformat()
    assert zero["end_date"] == (date(2026, 1, 5) + delta).isoformat()


def test_end_date_gives_matching_series_end(tiny_config_dir, repo_root, tmp_path):
    out = tmp_path / "chain-out"
    end_date = date(2026, 9, 18)
    result = _run_chain_cli(repo_root, ["--config-dir", str(tiny_config_dir), "--output", str(out),
                                        "--end-date", str(end_date)])
    assert result.returncode == 0, result.stderr
    retimed = yaml.safe_load((out / "configs" / "series.yaml").read_text())
    start = date.fromisoformat(retimed["series"]["start_date"])
    days = retimed["series"]["days"]
    assert start + timedelta(days=days - 1) == end_date


def test_chain_produces_all_four_datasets(tiny_config_dir, repo_root, tmp_path):
    out = tmp_path / "chain-out"
    result = _run_chain_cli(repo_root, ["--config-dir", str(tiny_config_dir), "--output", str(out),
                                        "--end-date", "2026-09-18"])
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "complete"
    for stage in ("users", "roster", "series", "org"):
        assert stage in summary
        assert summary[stage]["run_id"]
        stage_dir = Path(summary[stage]["output_dir"])
        assert stage_dir.is_dir()
        assert (stage_dir / "manifest.json").is_file()
    org_manifest = json.loads((out / "org" / "manifest.json").read_text())
    assert org_manifest["status"] == "complete"
    assert org_manifest["operation"] == "org"


def test_curve_binding_resolves_against_the_config_dir(tiny_config_dir, repo_root, tmp_path):
    """A team curve bound with a config-dir-relative `curve.artifacts` survives the chain
    re-homing series.yaml under <output>/configs/, and the series is generated from it."""
    from forecast.curve.config import CurveConfig
    from forecast.curve.runner import run_curve

    artifacts = tiny_config_dir / "curves" / "artifacts"
    curve = run_curve(CurveConfig.model_validate({
        "schema_version": 1, "name": "doubling", "interp": "pchip", "axis": {"start": 0, "end": 1},
        "anchors": [[0, "1.0"], [1, "2.0"]], "preview": {}, "description": None}), artifacts)
    ref = f"{curve.output_dir.parent.name}:{curve.output_dir.name}"
    series_path = tiny_config_dir / "series.yaml"
    series = yaml.safe_load(series_path.read_text())
    series["curve"] = {"artifacts": "curves/artifacts", "teams": {"payments": ref}}
    series_path.write_text(yaml.safe_dump(series))

    out = tmp_path / "chain-out"
    result = _run_chain_cli(repo_root, ["--config-dir", str(tiny_config_dir), "--output", str(out),
                                        "--end-date", "2026-09-18"])
    assert result.returncode == 0, result.stderr
    rehomed = yaml.safe_load((out / "configs" / "series.yaml").read_text())
    assert Path(rehomed["curve"]["artifacts"]) == artifacts.resolve()
    assert rehomed["curve"]["teams"] == {"payments": ref}
    snapshot = out / "series" / "inputs" / "curves" / "doubling" / curve.output_dir.name
    assert (snapshot / "config.yaml").is_file()


def test_pricing_snapshot_is_self_contained(tmp_path):
    """#68 design §5: the snapshot carries the bound catalog version(s), a trimmed index,
    history and the optional config, and prices with the live catalog out of the picture."""
    import json

    from common.model_pricing import Pricer
    from forecast.synthetic.org.pricing import load_pricer, pricing_files

    version, files = pricing_files(None, None)
    index = json.loads(dict(files)["catalog/index.json"])
    assert index["versions"][-1] == version
    assert f"catalog/{version}.json" in dict(files)
    assert "history.yaml" in dict(files)
    for rel, content in files:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(content)

    snap = load_pricer(version, None, tmp_path)           # reads ONLY tmp_path
    live = Pricer.load(None, None)
    tokens = {"input": 1_000_000, "output": 1_000_000}
    assert snap.fingerprint == live.fingerprint
    assert snap.price_tokens("claude-sonnet-5", tokens, "2026-10-01") == \
        live.price_tokens("claude-sonnet-5", tokens, "2026-10-01")


def test_pricing_files_rejects_an_unknown_version():
    import pytest

    from forecast.synthetic.errors import ConfigError
    from forecast.synthetic.org.pricing import pricing_files

    with pytest.raises(ConfigError):
        pricing_files("1999-01-01-deadbeef", None)
