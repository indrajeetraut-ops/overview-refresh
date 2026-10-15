"""P8: I2a (byte-identical reruns) and I2b (habit-only changes don't perturb token math),
over the full `org-expand` pipeline (plan §5 P8)."""
import json
from pathlib import Path

import pytest
import yaml

from forecast.synthetic.org.config import load_org_config
from forecast.synthetic.org.expand import run_org_expand


@pytest.fixture(scope="module")
def small_org_dirs(tmp_path_factory):
    """Build the same small series once, then run `run_org_expand` twice into separate output
    directories with the default config, and a third time with only
    `habits.cache_bust_per_100_requests` changed."""
    from forecast.synthetic.config import RosterConfig, UsersConfig, load_series_config
    from forecast.synthetic.runner import run_roster, run_series, run_users

    tmp = tmp_path_factory.mktemp("determinism")
    users_cfg = UsersConfig(schema_version=1, users={
        "count": 8, "org_domain": "determinism.test", "seed": 777, "locale": "en_US", "identity_style": "consistent"})
    users_out = tmp / "users"
    run_users(users_cfg, users_out)
    roster_cfg = RosterConfig(schema_version=1, roster={
        "users_file": users_out / "users.jsonl", "seed": 777,
        "teams": [{"team_name": "platform", "count": 4}, {"team_name": "payments", "count": 4}]})
    roster_out = tmp / "roster"
    run_roster(roster_cfg, roster_out)
    series_raw = {
        "schema_version": 3,
        "series": {"users_file": str(users_out / "users.jsonl"),
                  "roster_file": str(roster_out / "teams.jsonl"), "start_date": "2026-01-05",
                  "days": 14, "timezone": "UTC", "profile_seed": 778, "usage_seed": 779},
        "defaults": {"daily_variation_pct": 30, "output_ratio": 0.03, "cache_hit_ratio": 0.8,
                     "cache_hit_variation_pp": 5, "cache_write_ratio": 0.997, "cache_write_1h_share": 0.97, "mix_variation_pct": 10,
                     "daily_zero_probability": 0.1,
                     "weekend_reduction_probability": 0.9, "weekend_reduction_factor": 0.25},
        "profiles": {"light_profile": {"daily_input_tokens": 200_000},
                    "mid_profile": {"daily_input_tokens": 900_000},
                    "heavy_profile": {"daily_input_tokens": 3_000_000}},
        "team_profiles": {
            "platform": {"profile_mix": {"light_profile": 0.5, "mid_profile": 0.5},
                        "project_mix": {"platform_infra": 0.8, "payments_api": 0.2}},
            "payments": {"profile_mix": {"mid_profile": 0.5, "heavy_profile": 0.5},
                        "project_mix": {"payments_api": 1.0}},
        },
        "projects": [{"name": "platform_infra"}, {"name": "payments_api"}],
        "agents": {"claude_code": {"billing_mode": "api", "usage_share": 1.0,
                                   "models": {"claude-sonnet-5": 0.7, "claude-opus-5": 0.3}}},
        "events": {"spike_rules": [], "zero_periods": []},
    }
    series_yaml_path = tmp / "series.yaml"
    series_yaml_path.write_text(yaml.safe_dump(series_raw))
    series_out = tmp / "series"
    run_series(load_series_config(series_yaml_path), series_out)

    def _org_raw(habits_override=None):
        raw = {
            "schema_version": 1,
            "org": {"series_dir": str(series_out), "seed": 99, "github_owner": "acme",
                   "admin": {"github_login": "vp-eng", "display_name": "Jordan Avery",
                            "email": "jordan.avery@determinism.test"},
                   "agent_versions": ["2.1.260"]},
            "calibration": {"target_merged_prs_per_dev_week": 3.0},
            "repos": [
                {"project": "platform_infra", "name": "platform-infra",
                 "modules": [{"path": "src/", "weight": 1.0, "extensions": {".py": 1.0}}]},
                {"project": "payments_api", "name": "payments-api",
                 "modules": [{"path": "services/ledger/", "weight": 1.0, "extensions": {".py": 1.0}}]},
            ],
            "teams": {"platform": {}, "payments": {}},
        }
        if habits_override is not None:
            raw["defaults"] = {"habits": habits_override}
        return raw

    def _build(name: str, habits_override=None) -> Path:
        org_path = tmp / f"org-{name}.yaml"
        org_path.write_text(yaml.safe_dump(_org_raw(habits_override)))
        cfg = load_org_config(org_path)
        out = tmp / f"out-{name}"
        run_org_expand(cfg, out)
        return out

    out_a = _build("a")
    out_b = _build("b")
    out_c = _build("c", habits_override={"cache_bust_per_100_requests": 5.0})
    return out_a, out_b, out_c


def _jsonl_files(path: Path) -> list[str]:
    return sorted(p.name for p in path.glob("*.jsonl"))


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def test_i2a_byte_identical_rerun(small_org_dirs):
    out_a, out_b, _out_c = small_org_dirs
    files_a = _jsonl_files(out_a)
    assert files_a == _jsonl_files(out_b)
    assert files_a  # sanity: the run actually produced files
    for name in files_a:
        assert _sha256(out_a / name) == _sha256(out_b / name), name


def test_i2b_habits_only_change_leaves_structural_files_identical(small_org_dirs):
    out_a, _out_b, out_c = small_org_dirs
    unaffected = ("sessions.jsonl", "git_artifacts.jsonl", "session_git_links.jsonl",
                 "expected_rollup.jsonl")
    for name in unaffected:
        assert _sha256(out_a / name) == _sha256(out_c / name), name


def test_i2b_per_session_token_totals_unchanged(small_org_dirs):
    out_a, _out_b, out_c = small_org_dirs

    def totals_by_session(path: Path) -> dict[str, dict]:
        out = {}
        for line in (path / "session_info.jsonl").read_text().splitlines():
            row = json.loads(line)
            out[row["session_id"]] = {
                m: {"input_combined": cats["input"] + cats["cache_write"],
                   "cache_read": cats["cache_read"], "output": cats["output"]}
                for m, cats in row["models"].items()
            }
        return out

    totals_a = totals_by_session(out_a)
    totals_c = totals_by_session(out_c)
    assert totals_a == totals_c
