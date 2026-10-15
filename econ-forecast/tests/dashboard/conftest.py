"""Fixtures shared by the dashboard tests: a small Layer 1 dataset, a pinned tiered price
catalog, a fast forecast template and one completed dashboard run.

They are session-scoped and read-only: no test may write into the directories they return
(copy first when a test needs to change something). The import mode is `importlib`, so
test modules cannot import from here; whatever they need is a fixture.
"""

from datetime import date
import json
from pathlib import Path
import shutil

import pytest
import yaml

from common.model_pricing.catalog import CatalogVersion, RateRow, RegistryEntry, load_catalog
from common.model_pricing.decimals import per_million_to_per_token as pm

from forecast.dashboard import DEFAULT_PRESETS_PATH, run_dashboard_forecast
from forecast.dashboard import runner as runner_module
from forecast.synthetic.config import load_roster_config, load_series_config, load_users_config
from forecast.synthetic.runner import run_roster, run_series, run_users

FORECAST_TEMPLATE = runner_module.DEFAULT_FORECAST_TEMPLATE
MODELS = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001")

USERS = """\
schema_version: 1
users:
  count: 12
  org_domain: fixture.dev
  locale: en_US
  seed: 7
"""
ROSTER = """\
schema_version: 1
roster:
  users_file: {users}
  seed: 43
  teams:
  - team_name: Alpha
    count: 5
  - team_name: Beta
    count: 5
"""
# Mid-month run: 75 days from 2026-08-02 end on 2026-10-15, so the origin is 2026-09-30 and the
# forecast covers October and November. Month-end run: 60 days end on 2026-09-30, so the
# origin is that day itself and the forecast covers the same two months. A fixture catalog is
# pinned, so the dates only need to give those two shapes.
SERIES = """\
schema_version: 3
series:
  users_file: {users}
  roster_file: {roster}
  start_date: '{start}'
  days: {days}
  timezone: Asia/Kolkata
  profile_seed: 100
  usage_seed: 123
defaults:
  daily_variation_pct: 30
  output_ratio: 0.03
  cache_hit_ratio: 0.95
  cache_hit_variation_pp: 5
  cache_write_ratio: 0.997
  cache_write_1h_share: 0.97
  mix_variation_pct: 10
  daily_zero_probability: 0.05
profiles:
  light_profile: {{daily_input_tokens: 250000}}
  mid_profile: {{daily_input_tokens: 1000000}}
  heavy_profile: {{daily_input_tokens: 5000000}}
team_profiles:
  Alpha:
    profile_mix: {{mid_profile: 0.6, heavy_profile: 0.4}}
    project_mix: {{checkout_flow: 0.6, search_index: 0.4}}
  Beta:
    profile_mix: {{light_profile: 0.4, mid_profile: 0.6}}
    project_mix: {{billing_sync: 1.0}}
projects:
- name: checkout_flow
- name: search_index
- name: billing_sync
agents:
  claude_code:
    billing_mode: api
    usage_share: 1.0
    models:
      claude-opus-5: 0.4
      claude-sonnet-5: 0.4
      claude-haiku-4-5-20251001: 0.2
events:
  spike_rules: []
  zero_periods: []
"""


# USD per million tokens: input, output, cache read, 5-minute cache write, 1-hour cache write.
# Strictly tiered (opus > sonnet > haiku); sonnet is exactly opus / 5 at every token type.
PRICES = {
    "claude-opus-5": (15, 75, 1.5, 18.75, 30),
    "claude-sonnet-5": (3, 15, 0.3, 3.75, 6),
    "claude-haiku-4-5-20251001": (1, 5, 0.1, 1.25, 2),
}


@pytest.fixture(scope="session")
def pinned_catalog(tmp_path_factory):
    """Tiered rates for the three preset models from 2020, so the run never depends on
    the live catalog (same construction as tests/cost/test_request.py)."""
    root = tmp_path_factory.mktemp("catalog")
    (root / "config").mkdir()
    rates = [
        RateRow(
            channel="anthropic", model_id=model, start_date=date(2020, 1, 1),
            input=pm(i), output=pm(o), cache_read=pm(r),
            cache_write_5m=pm(w5), cache_write_1h=pm(w1),
            source="test", source_key=model,
        )
        for model, (i, o, r, w5, w1) in PRICES.items()
    ]
    models = {m: RegistryEntry(vendor="anthropic", names_seen=[m]) for m in MODELS}
    stub = CatalogVersion(version="pending", built_on=date(2026, 1, 1), models=models, rates=rates)
    version = f"2026-01-01-{stub.compute_hash8()}"
    (root / f"{version}.json").write_text(stub.model_copy(update={"version": version}).model_dump_json())
    (root / "index.json").write_text(json.dumps({"versions": [version]}))
    return load_catalog(root=root, config_root=root / "config")


@pytest.fixture(scope="session")
def layer1_inputs(tmp_path_factory):
    """Users and roster, shared by the two series datasets."""
    base = tmp_path_factory.mktemp("layer1")
    (base / "users.yaml").write_text(USERS)
    run_users(load_users_config(base / "users.yaml"), base / "users")
    (base / "roster.yaml").write_text(ROSTER.format(users=base / "users" / "users.jsonl"))
    run_roster(load_roster_config(base / "roster.yaml"), base / "roster")
    return base


def _make_series(base: Path, name: str, start: str, days: int) -> Path:
    (base / f"{name}.yaml").write_text(
        SERIES.format(
            users=base / "users" / "users.jsonl", roster=base / "roster" / "teams.jsonl",
            start=start, days=days,
        )
    )
    run_series(load_series_config(base / f"{name}.yaml"), base / name)
    return base / name


@pytest.fixture(scope="session")
def series_dir(layer1_inputs):
    return _make_series(layer1_inputs, "series", "2026-08-02", 75)  # ends 2026-10-15


@pytest.fixture(scope="session")
def month_end_series_dir(layer1_inputs):
    return _make_series(layer1_inputs, "series-month-end", "2026-08-02", 60)  # ends 2026-09-30


@pytest.fixture(scope="session")
def fast_template(tmp_path_factory):
    template = yaml.safe_load(FORECAST_TEMPLATE.read_text())
    template["uncertainty"]["paths"] = 100
    path = tmp_path_factory.mktemp("template") / "forecast.yaml"
    path.write_text(yaml.safe_dump(template))
    return path


@pytest.fixture(scope="session")
def dashboard_run(series_dir, fast_template, pinned_catalog, tmp_path_factory):
    out = tmp_path_factory.mktemp("dashboard-out") / "run"
    result = run_dashboard_forecast(
        series_dir, DEFAULT_PRESETS_PATH, out, forecast_template=fast_template, catalog=pinned_catalog
    )
    return result


@pytest.fixture(scope="session")
def make_series():
    """Factory: `make_series(base, name, start, days)` builds a Layer 1 series dataset
    from the fixture config under `base/name` (the users and roster must already be there)."""
    return _make_series


# --- an org-demo directory around the fixture dataset -------------------------

ORG_ID = "fixture.dev"
DIRECTORY_ORG_ID = "default"
GITHUB_OWNER = "acme"
#: Layer 1 projects of the fixture dataset and the repository each one belongs to.
REPOS = {
    "checkout_flow": "checkout-flow",
    "search_index": "search-index",
    "billing_sync": "billing-sync",
}
#: Directory teams: the parent has no Layer 1 scope; `Qa` is how the example org spells
#: Layer 1's `QA`, and the last one has a name that slugs differently from itself.
DIRECTORY_TEAMS = (
    ("eng", "Engineering", None),
    ("alpha", "Alpha", "eng"),
    ("beta", "Beta", "eng"),
    ("qa", "Qa", "eng"),
    ("data-platform-x", "Data Platform X", "eng"),
)


def _write_jsonl(path: Path, rows) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _make_org_demo(dest: Path, series: Path | None = None) -> Path:
    """The org-demo files the scope mapping reads (and, with `series`, a copy of that
    series dataset as `<dest>/series`)."""
    (dest / "org").mkdir(parents=True)
    (dest / "configs").mkdir()
    (dest / "org" / "manifest.json").write_text(
        json.dumps({"org_id": ORG_ID, "directory_org_id": DIRECTORY_ORG_ID, "operation": "org"}), encoding="utf-8"
    )
    _write_jsonl(
        dest / "org" / "budgets.jsonl",
        [
            {"amount_usd": 3000.0, "period": "monthly", "scope_id": DIRECTORY_ORG_ID, "scope_type": "org"},
            {"amount_usd": 1000.0, "period": "monthly", "scope_id": "alpha", "scope_type": "team"},
        ],
    )
    _write_jsonl(
        dest / "org" / "directory_teams.jsonl",
        [
            {"name": name, "org_id": DIRECTORY_ORG_ID, "parent_team_id": parent, "source_provider": "csv", "team_id": team_id}
            for team_id, name, parent in DIRECTORY_TEAMS
        ],
    )
    _write_jsonl(
        dest / "org" / "repositories.jsonl",
        [
            {"kind": "git_remote", "label": f"{GITHUB_OWNER}/{repo}", "repo_id": f"github:{GITHUB_OWNER}/{repo}"}
            for repo in REPOS.values()
        ],
    )
    (dest / "configs" / "org.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "org": {"github_owner": GITHUB_OWNER},
                "repos": [{"project": project, "name": name} for project, name in REPOS.items()],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    if series is not None:
        shutil.copytree(series, dest / "series")
    return dest


@pytest.fixture(scope="session")
def make_org_demo():
    """Factory: `make_org_demo(dest, series=None)`, see `_make_org_demo`."""
    return _make_org_demo


@pytest.fixture(scope="session")
def org_demo(series_dir, tmp_path_factory):
    """A shared, read-only org-demo directory (do not run the CLI with its default output)."""
    return _make_org_demo(tmp_path_factory.mktemp("org-demo") / "demo", series_dir)


@pytest.fixture(scope="session")
def dashboard_scopes(dashboard_run, org_demo):
    from forecast.dashboard import map_scopes

    return map_scopes(org_demo, dashboard_run.scopes, org_id=dashboard_run.org_id)
