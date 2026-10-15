"""The org expander consumes a series that uses per-profile personal spikes unchanged."""
import json

import yaml

from forecast.synthetic.config import RosterConfig, UsersConfig, load_series_config
from forecast.synthetic.org.config import load_org_config
from forecast.synthetic.org.expand import run_org_expand
from forecast.synthetic.runner import run_roster, run_series, run_users


def _series_raw(users_out, roster_out):
    return {
        "schema_version": 3,
        "series": {"users_file": str(users_out / "users.jsonl"),
                   "roster_file": str(roster_out / "teams.jsonl"), "start_date": "2026-01-05",
                   "days": 14, "timezone": "UTC", "profile_seed": 31, "usage_seed": 32},
        "defaults": {"daily_variation_pct": 30, "output_ratio": 0.03, "cache_hit_ratio": 0.8,
                     "cache_hit_variation_pp": 5, "cache_write_ratio": 0.997,
                     "cache_write_1h_share": 0.97, "mix_variation_pct": 10,
                     "daily_zero_probability": 0.1,
                     "weekend_reduction_probability": 0.9, "weekend_reduction_factor": 0.25},
        "profiles": {
            "mid_profile": {"daily_input_tokens": 900_000},
            "power_profile": {"daily_input_tokens": 3_000_000, "spike_probability": 0.5,
                              "spike_multiplier_min": 1.5, "spike_multiplier_max": 2.5}},
        "team_profiles": {
            "platform": {"profile_mix": {"mid_profile": 0.5, "power_profile": 0.5},
                         "project_mix": {"platform_infra": 0.8, "payments_api": 0.2}},
            "payments": {"profile_mix": {"mid_profile": 0.5, "power_profile": 0.5},
                         "project_mix": {"payments_api": 1.0}}},
        "projects": [{"name": "platform_infra"}, {"name": "payments_api"}],
        "agents": {"claude_code": {"billing_mode": "api", "usage_share": 1.0,
                                   "models": {"claude-sonnet-5": 0.7, "claude-opus-5": 0.3}}},
        "events": {"spike_rules": [], "zero_periods": []},
    }


def test_org_expand_runs_on_a_series_with_personal_spikes(tmp_path):
    users_out = tmp_path / "users"
    run_users(UsersConfig(schema_version=1, users={
        "count": 8, "org_domain": "spikes.test", "seed": 5, "locale": "en_US",
        "identity_style": "consistent"}), users_out)
    roster_out = tmp_path / "roster"
    run_roster(RosterConfig(schema_version=1, roster={
        "users_file": users_out / "users.jsonl", "seed": 5,
        "teams": [{"team_name": "platform", "count": 4}, {"team_name": "payments", "count": 4}]}),
        roster_out)
    series_path = tmp_path / "series.yaml"
    series_path.write_text(yaml.safe_dump(_series_raw(users_out, roster_out)))
    series_out = tmp_path / "series"
    run_series(load_series_config(series_path), series_out)
    events = (series_out / "generation_events.jsonl").read_text().splitlines()
    assert events and all(json.loads(line)["kind"] == "personal_spike" for line in events)

    org_path = tmp_path / "org.yaml"
    org_path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "org": {"series_dir": str(series_out), "seed": 9, "github_owner": "acme",
                "admin": {"github_login": "vp-eng", "display_name": "Jordan Avery",
                          "email": "jordan.avery@spikes.test"},
                "agent_versions": ["2.1.260"]},
        "calibration": {"target_merged_prs_per_dev_week": 3.0},
        "repos": [
            {"project": "platform_infra", "name": "platform-infra",
             "modules": [{"path": "src/", "weight": 1.0, "extensions": {".py": 1.0}}]},
            {"project": "payments_api", "name": "payments-api",
             "modules": [{"path": "services/ledger/", "weight": 1.0, "extensions": {".py": 1.0}}]}],
        "teams": {"platform": {}, "payments": {}}}))
    out = tmp_path / "org-out"
    run_org_expand(load_org_config(org_path), out)  # asserts the usage rollup internally
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["status"] == "complete"
