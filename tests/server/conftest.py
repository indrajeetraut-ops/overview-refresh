"""Shared DB-gate and fixtures for P10/P11 synthetic-org DB-gated tests. Skipped unless
`TALIUM_TEST_DSN` is set AND its database name ends with `_synth` (plan §5 P10/P11) -- the
same posture as the D13 guard itself, so these test files can never accidentally point at a
shared database."""
import os
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

TEST_DSN = os.environ.get("TALIUM_TEST_DSN")
_synth_ready = TEST_DSN is not None and (make_url(TEST_DSN).database or "").endswith("_synth")
requires_synth_db = pytest.mark.skipif(
    not _synth_ready, reason="set TALIUM_TEST_DSN to a *_synth database to run this test")


@pytest.fixture(scope="session")
def small_org_dataset(tmp_path_factory):
    """The small (8-user/2-team/14-day) org dataset from P8's own test scenario, generated
    fresh here so DB-gated test files do not depend on another test module's fixtures. Session
    scope: the on-disk dataset is immutable once generated, and every DB-gated test does its
    own explicit `--replace` load."""
    import yaml

    from forecast.synthetic.config import RosterConfig, UsersConfig, load_series_config
    from forecast.synthetic.org.config import load_org_config
    from forecast.synthetic.org.expand import run_org_expand
    from forecast.synthetic.runner import run_roster, run_series, run_users

    tmp = tmp_path_factory.mktemp("p10_small_org")
    users_cfg = UsersConfig(schema_version=1, users={
        "count": 8, "org_domain": "p10-test.test", "seed": 321, "locale": "en_US", "identity_style": "consistent"})
    users_out = tmp / "users"
    run_users(users_cfg, users_out)
    roster_cfg = RosterConfig(schema_version=1, roster={
        "users_file": users_out / "users.jsonl", "seed": 321,
        "teams": [{"team_name": "platform", "count": 4}, {"team_name": "payments", "count": 4}]})
    roster_out = tmp / "roster"
    run_roster(roster_cfg, roster_out)
    series_raw = {
        "schema_version": 3,
        "series": {"users_file": str(users_out / "users.jsonl"),
                  "roster_file": str(roster_out / "teams.jsonl"), "start_date": "2026-01-05",
                  "days": 14, "timezone": "UTC", "profile_seed": 322, "usage_seed": 323},
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

    org_raw = {
        "schema_version": 1,
        "org": {"series_dir": str(series_out), "seed": 55,
               "github_owner": "p10co",
               "admin": {"github_login": "vp-eng", "display_name": "VP Eng",
                        "email": "vp@p10-test.test"},
               "agent_versions": ["2.1.260"]},
        "calibration": {"target_merged_prs_per_dev_week": 3.0},
        "repos": [
            {"project": "platform_infra", "name": "platform-infra",
             "modules": [{"path": "src/", "weight": 1.0, "extensions": {".py": 1.0}}]},
            {"project": "payments_api", "name": "payments-api",
             "modules": [{"path": "services/", "weight": 1.0, "extensions": {".py": 1.0}}]},
        ],
        "teams": {"platform": {}, "payments": {}},
    }
    org_path = tmp / "org.yaml"
    org_path.write_text(yaml.safe_dump(org_raw))
    org_out = tmp / "org"
    run_org_expand(load_org_config(org_path), org_out)
    return org_out
