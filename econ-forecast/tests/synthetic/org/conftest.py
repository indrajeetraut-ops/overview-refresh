"""Shared fixtures for the Layer 2 (`org-expand`) test suite.

`small_series` runs the real Layer 1 chain (users -> roster -> series) into a temp directory
with a tiny, fast configuration: 2 teams of 4 users each, 2 repos/projects, a short date range.
`org_config_dict` is a matching, valid `org.yaml` document as a plain dict; `write_org_yaml`
writes it (or a caller-supplied override) to disk and returns the path, for `load_org_config`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from forecast.synthetic.config import RosterConfig, SeriesConfig, UsersConfig
from forecast.synthetic.runner import run_roster, run_series, run_users

SERIES_DAYS = 14
SEED = 4242


def _series_yaml(days: int = SERIES_DAYS) -> dict:
    return {
        "schema_version": 3,
        "series": {
            "users_file": "users.jsonl", "roster_file": "roster.jsonl",
            "start_date": "2026-01-05", "days": days, "timezone": "UTC",
            "profile_seed": SEED + 1, "usage_seed": SEED + 2,
        },
        "defaults": {"daily_variation_pct": 30, "output_ratio": 0.03, "cache_hit_ratio": 0.8,
                     "cache_hit_variation_pp": 5, "cache_write_ratio": 0.997, "cache_write_1h_share": 0.97, "mix_variation_pct": 10,
                     "daily_zero_probability": 0.1,
                     "weekend_reduction_probability": 0.9, "weekend_reduction_factor": 0.25},
        "profiles": {
            "light_profile": {"daily_input_tokens": 200_000},
            "mid_profile": {"daily_input_tokens": 900_000},
            "heavy_profile": {"daily_input_tokens": 3_000_000},
        },
        "team_profiles": {
            "platform": {
                "profile_mix": {"light_profile": 0.5, "mid_profile": 0.5},
                "project_mix": {"platform_infra": 0.8, "payments_api": 0.2},
            },
            "payments": {
                "profile_mix": {"mid_profile": 0.5, "heavy_profile": 0.5},
                "project_mix": {"payments_api": 1.0},
            },
        },
        "projects": [{"name": "platform_infra"}, {"name": "payments_api"}],
        "agents": {
            "claude_code": {
                "billing_mode": "api", "usage_share": 1.0,
                "models": {"claude-sonnet-5": 0.7, "claude-opus-5": 0.3},
            },
        },
        "events": {"spike_rules": [], "zero_periods": []},
    }


@pytest.fixture
def build_series_chain(tmp_path: Path):
    """Run the real Layer 1 chain (users -> roster -> series) into `tmp_path/label`: 2 teams of
    4 users each, 2 repos/projects, a short date range. Returns the dict `small_series` exposes.
    Callers pick `identity_style`/`org_domain` so more than one series can coexist in one test
    (e.g. to exercise the "users need github_login" rule without touching the shared fixture).
    """
    def _build(*, identity_style: str = "consistent", org_domain: str = "org-conf.test",
              label: str = "series") -> dict:
        from forecast.synthetic.config import load_series_config

        root = tmp_path / label
        users_cfg = UsersConfig(schema_version=1, users={
            "count": 8, "org_domain": org_domain, "seed": SEED, "locale": "en_US", "identity_style": identity_style})
        users_out = root / "users"
        run_users(users_cfg, users_out)

        roster_cfg = RosterConfig(schema_version=1, roster={
            "users_file": users_out / "users.jsonl", "seed": SEED,
            "teams": [{"team_name": "platform", "count": 4}, {"team_name": "payments", "count": 4}]})
        roster_out = root / "roster"
        run_roster(roster_cfg, roster_out)

        series_yaml_path = root / "series.yaml"
        series_raw = _series_yaml()
        series_raw["series"]["users_file"] = str(users_out / "users.jsonl")
        series_raw["series"]["roster_file"] = str(roster_out / "teams.jsonl")
        series_yaml_path.write_text(yaml.safe_dump(series_raw))
        series_out = root / "series"
        run_series(load_series_config(series_yaml_path), series_out)

        return {"tmp_path": root, "users_dir": users_out, "roster_dir": roster_out,
                "series_dir": series_out}
    return _build


@pytest.fixture
def small_series(build_series_chain):
    """The default (identity_style: consistent) small series dataset, shared by most tests."""
    return build_series_chain()


@pytest.fixture
def org_config_dict(small_series) -> dict:
    return {
        "schema_version": 1,
        "org": {
            "series_dir": str(small_series["series_dir"]),
            "seed": 7,
            "github_owner": "acme",
            "admin": {"github_login": "vp-eng", "display_name": "Jordan Avery",
                      "email": "jordan.avery@org-conf.test"},
            "agent_versions": ["2.1.260"],
        },
        "calibration": {"target_merged_prs_per_dev_week": 3.0},
        "repos": [
            {"project": "platform_infra", "name": "platform-infra",
             "modules": [{"path": "src/", "weight": 1.0, "extensions": {".py": 1.0}}]},
            {"project": "payments_api", "name": "payments-api",
             "modules": [{"path": "services/ledger/", "weight": 1.0, "extensions": {".py": 1.0}}]},
        ],
        "teams": {"platform": {}, "payments": {}},
    }


@pytest.fixture
def write_org_yaml(tmp_path):
    def _write(data: dict, name: str = "org.yaml") -> Path:
        path = tmp_path / name
        path.write_text(yaml.safe_dump(data))
        return path
    return _write


@pytest.fixture(scope="session")
def repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "config" / "model_pricing").is_dir():
            return parent
    raise FileNotFoundError("config/model_pricing not found above the test file")


@pytest.fixture
def read_series_inputs():
    """Read manifest/users/teams/series-config/pricing fresh from a series output dir, the same
    way `cli_synth_org.py` (P3) will: from the series dataset's OWN snapshots, never the
    originals. Used directly by `series_inputs`, and again after a test tampers with the
    snapshotted `config.yaml` to exercise one §3.5 rule."""
    def _read(series_dir: Path) -> dict:
        from common.model_pricing import Pricer as CommonPricer
        from forecast.synthetic.org.config import read_series_snapshot

        data = read_series_snapshot(series_dir)
        data["pricing"] = CommonPricer.load(None, None)
        return data
    return _read


@pytest.fixture
def series_inputs(small_series, read_series_inputs):
    """The things `validate_org_inputs` cross-checks `org.yaml` against, for the shared
    `small_series` fixture."""
    return read_series_inputs(small_series["series_dir"])


@pytest.fixture
def build_org_config(write_org_yaml):
    """Write an org.yaml dict and load it."""
    def _build(data: dict):
        from forecast.synthetic.org.config import load_org_config

        data = json.loads(json.dumps(data))  # deep copy; keeps callers free to mutate afterwards
        return load_org_config(write_org_yaml(data))
    return _build


@pytest.fixture
def resolved(build_org_config, org_config_dict, series_inputs):
    """A `ResolvedOrg` built from the default `org_config_dict` against `small_series`, shared
    across P3+ test files (`directory`/`repos`/`sessions`/... all need one)."""
    from forecast.synthetic.org.config import validate_org_inputs

    cfg = build_org_config(org_config_dict)
    return validate_org_inputs(cfg, series_inputs["manifest"], series_inputs["users"],
                               series_inputs["teams"], series_inputs["series_cfg"],
                               series_inputs["pricing"])


@pytest.fixture
def usage_records(small_series):
    """The generated series' own `usage.jsonl`, parsed -- the direct input to
    `sessions.plan_sessions`."""
    from forecast.artifact_io import read_records
    from forecast.contracts import DailyUsageRecord

    return read_records(small_series["series_dir"] / "usage.jsonl", DailyUsageRecord,
                        schema_version=2)


@pytest.fixture(scope="module")
def four_team_org(tmp_path_factory):
    """A purpose-built 4-team (platform/payments/growth/data, 5 users each), 90-day scenario --
    large enough for I6 calibration and cross-team monotonic checks (P5+), reused by P6+ test
    files too. NOT the real P9 example config, which does not exist until task P9 (see the
    progress log's P5 entry)."""
    from pathlib import Path

    import yaml as yaml_
    from common.model_pricing import Pricer as CommonPricer
    from forecast.artifact_io import read_records
    from forecast.contracts import DailyUsageRecord
    from forecast.synthetic.config import RosterConfig, UsersConfig, load_series_config
    from forecast.synthetic.org.config import load_org_config, read_series_snapshot, validate_org_inputs
    from forecast.synthetic.org.sessions import plan_sessions
    from forecast.synthetic.runner import run_roster, run_series, run_users

    tmp = tmp_path_factory.mktemp("four_team")
    users_cfg = UsersConfig(schema_version=1, users={
        "count": 20, "org_domain": "calib.test", "seed": 999, "locale": "en_US", "identity_style": "consistent"})
    users_out = tmp / "users"
    run_users(users_cfg, users_out)

    teams = [{"team_name": n, "count": 5} for n in ("platform", "payments", "growth", "data")]
    roster_cfg = RosterConfig(schema_version=1, roster={
        "users_file": users_out / "users.jsonl", "seed": 999, "teams": teams})
    roster_out = tmp / "roster"
    run_roster(roster_cfg, roster_out)

    series_raw = {
        "schema_version": 3,
        "series": {"users_file": str(users_out / "users.jsonl"),
                  "roster_file": str(roster_out / "teams.jsonl"), "start_date": "2026-01-05",
                  "days": 90, "timezone": "UTC", "profile_seed": 1, "usage_seed": 2},
        "defaults": {"daily_variation_pct": 30, "output_ratio": 0.03, "cache_hit_ratio": 0.8,
                     "cache_hit_variation_pp": 5, "cache_write_ratio": 0.997, "cache_write_1h_share": 0.97, "mix_variation_pct": 10,
                     "daily_zero_probability": 0.1,
                     "weekend_reduction_probability": 0.7, "weekend_reduction_factor": 0.25},
        "profiles": {"light_profile": {"daily_input_tokens": 300_000},
                    "mid_profile": {"daily_input_tokens": 1_200_000},
                    "heavy_profile": {"daily_input_tokens": 4_000_000}},
        "team_profiles": {
            "platform": {"profile_mix": {"light_profile": .25, "mid_profile": .5, "heavy_profile": .25},
                        "project_mix": {"platform_proj": 1.0}},
            "payments": {"profile_mix": {"light_profile": .4, "mid_profile": .4, "heavy_profile": .2},
                        "project_mix": {"payments_proj": 1.0}},
            "growth": {"profile_mix": {"light_profile": .2, "mid_profile": .6, "heavy_profile": .2},
                      "project_mix": {"growth_proj": 1.0}},
            "data": {"profile_mix": {"mid_profile": .25, "heavy_profile": .75},
                    "project_mix": {"data_proj": 1.0}},
        },
        "projects": [{"name": "platform_proj"}, {"name": "payments_proj"},
                    {"name": "growth_proj"}, {"name": "data_proj"}],
        "agents": {"claude_code": {"billing_mode": "api", "usage_share": 1.0,
                                   "models": {"claude-sonnet-5": 0.7, "claude-opus-5": 0.3}}},
        "events": {"spike_rules": [], "zero_periods": []},
    }
    series_yaml_path = tmp / "series.yaml"
    series_yaml_path.write_text(yaml_.safe_dump(series_raw))
    series_out = tmp / "series"
    run_series(load_series_config(series_yaml_path), series_out)

    org_raw = {
        "schema_version": 1,
        "org": {"series_dir": str(series_out), "seed": 7, "github_owner": "acme",
               "admin": {"github_login": "vp-eng", "display_name": "Jordan Avery",
                        "email": "jordan.avery@calib.test"},
               "agent_versions": ["2.1.260"]},
        "calibration": {"target_merged_prs_per_dev_week": 3.0},
        "repos": [
            {"project": "platform_proj", "name": "platform-infra",
             "modules": [{"path": "src/", "weight": 1.0, "extensions": {".py": 1.0}}]},
            {"project": "payments_proj", "name": "payments-api",
             "modules": [{"path": "services/ledger/", "weight": 1.0, "extensions": {".py": 1.0}}]},
            {"project": "growth_proj", "name": "growth-web",
             "modules": [{"path": "app/", "weight": 1.0, "extensions": {".ts": 1.0}}]},
            {"project": "data_proj", "name": "data-pipeline",
             "modules": [{"path": "pipelines/", "weight": 1.0, "extensions": {".py": 1.0}}]},
        ],
        "teams": {
            "platform": {"tokens_per_loc_multiplier": 0.7},
            "payments": {"tokens_per_loc_multiplier": 1.0},
            "growth": {"tokens_per_loc_multiplier": 1.1},
            "data": {"tokens_per_loc_multiplier": 1.6},
        },
    }
    org_path = tmp / "org.yaml"
    org_path.write_text(yaml_.safe_dump(org_raw))

    cfg = load_org_config(org_path)
    snap = read_series_snapshot(cfg.org.series_dir)
    pricing = CommonPricer.load(None, None)
    resolved = validate_org_inputs(cfg, snap["manifest"], snap["users"], snap["teams"],
                                   snap["series_cfg"], pricing)

    usage = read_records(Path(cfg.org.series_dir) / "usage.jsonl", DailyUsageRecord,
                          schema_version=2)
    plans = plan_sessions(resolved, usage)
    from forecast.synthetic.org.work import compute_work
    result = compute_work(resolved, plans)
    return resolved, plans, result


@pytest.fixture(scope="module")
def four_team_git(four_team_org):
    """`compute_git` over `four_team_org` -- shared by P6+ test files needing a large,
    realistic dataset (reverts, open-at-series-end PRs, etc. all appear at this scale)."""
    from forecast.synthetic.org.git import compute_git

    resolved, plans, work = four_team_org
    return resolved, plans, work, compute_git(resolved, plans, work)


@pytest.fixture(scope="module")
def records_and_ledgers(four_team_git):
    """`content.build_all` over `four_team_git`: `(resolved, plans, work, git_result, records,
    ledgers)`, built once per module."""
    from common.model_pricing import Pricer as CommonPricer
    from forecast.synthetic.org.content import build_all
    from forecast.synthetic.org.pricing import OrgPricer

    resolved, plans, work, git_result = four_team_git
    pricer = OrgPricer(CommonPricer.load(None, None))
    recs, ledgers = build_all(resolved, plans, work, git_result, pricer)
    return resolved, plans, work, git_result, recs, ledgers


@pytest.fixture(scope="module")
def records(records_and_ledgers):
    """`content.build_all` records over `four_team_git` -- shared by P7+ test files
    (`test_content.py`, `test_parity_graph_fold.py`, ...)."""
    return records_and_ledgers[:5]


@pytest.fixture
def load_and_validate(build_org_config, series_inputs):
    """Build an org.yaml dict and cross-validate it against the (unmodified) `small_series`
    fixture's inputs in one call -- the common path every §3.5 failing-config test exercises."""
    from forecast.synthetic.org.config import validate_org_inputs

    def _run(data: dict, *, inputs: dict | None = None):
        inputs = inputs or series_inputs
        cfg = build_org_config(data)
        return validate_org_inputs(cfg, inputs["manifest"], inputs["users"], inputs["teams"],
                                   inputs["series_cfg"], inputs["pricing"])
    return _run
