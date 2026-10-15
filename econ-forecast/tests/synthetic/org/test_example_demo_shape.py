"""Structure and forecast behaviour of the shipped example org (`econ-forecast/config/synthetic/org/`).

The example is tuned so the generated demo looks like a real org: two power users in
Development, a few low users, one team with margin (Platform) that never needs a crunch, spiky
Development and QA curves inside the last 60 days of the series, and a busy-days rule. Most of
this file is cheap structure (roster and budget rule, profile assignment, profile and curve
configuration, a 30-day series); the last test generates a series for three end dates, runs the
dashboard forecast on it and checks the landing against the budgets (the targets of plan
2026-10-10-synthetic-demo-realism section 3.3, measured the way the plan's calibration loop does).
`EXAMPLE_ORG_CONFIG_DIR` points the whole file at another config directory (used to check that
these tests fail on a deliberately broken copy).
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from forecast.curve.artifacts import load_curve_artifact, resolve_curve_ref
from forecast.curve.model import evaluate_curve
from forecast.synthetic.allocation import allocate_integer
from forecast.synthetic.config import (RosterConfig, SeriesConfig, UsersConfig, load_series_config,
                                       validate_series_inputs)
from forecast.synthetic.models import TeamRecord, UserRecord
from forecast.synthetic.profiles import assign_profiles
from forecast.synthetic.runner import run_roster, run_series, run_users
from forecast.artifact_io import read_records

EXAMPLE = Path(os.environ.get("EXAMPLE_ORG_CONFIG_DIR")
               or Path(__file__).resolve().parents[3] / "config" / "synthetic" / "org")
BUDGET_PER_DEVELOPER = 200
ROSTER_COUNTS = {"Development": 20, "Platform": 10, "QA": 5}


def _yaml(name: str) -> dict:
    return yaml.safe_load((EXAMPLE / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def example_series() -> dict:
    return _yaml("series.yaml")


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    """users -> roster from the example configs, series config pointed at them (no series run)."""
    out = tmp_path_factory.mktemp("example")
    users_cfg = UsersConfig.model_validate(_yaml("users.yaml"))
    run_users(users_cfg, out / "users")
    roster_raw = _yaml("roster.yaml")
    roster_raw["roster"]["users_file"] = str(out / "users" / "users.jsonl")
    run_roster(RosterConfig.model_validate(roster_raw), out / "roster")
    return out


def _series_config(chain: Path, tmp: Path, **series_overrides) -> Path:
    raw = _yaml("series.yaml")
    raw["series"]["users_file"] = str(chain / "users" / "users.jsonl")
    raw["series"]["roster_file"] = str(chain / "roster" / "teams.jsonl")
    raw["series"].update(series_overrides)
    raw["curve"]["artifacts"] = str((EXAMPLE / raw["curve"]["artifacts"]).resolve())
    path = tmp / "series.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def assignment(chain, tmp_path_factory):
    config = load_series_config(_series_config(chain, tmp_path_factory.mktemp("cfg")))
    users = read_records(chain / "users" / "users.jsonl", UserRecord)
    teams = read_records(chain / "roster" / "teams.jsonl", TeamRecord)
    validate_series_inputs(config, users, teams)
    rows = assign_profiles(config, teams)
    by_team = {t.team_id: t.team_name for t in teams}
    counts: dict[str, Counter] = {}
    for row in rows:
        counts.setdefault(by_team[row.team_id], Counter())[row.profile] += 1
    return config, rows, counts


# --- roster and budgets ----------------------------------------------------------------------


def test_roster_counts_and_budget_rule_intact():
    roster = {t["team_name"]: t["count"] for t in _yaml("roster.yaml")["roster"]["teams"]}
    assert roster == ROSTER_COUNTS
    budgets = _yaml("org.yaml")["budgets"]
    assert budgets["teams"] == {name: BUDGET_PER_DEVELOPER * n for name, n in ROSTER_COUNTS.items()}
    assert budgets["org_usd"] == BUDGET_PER_DEVELOPER * sum(ROSTER_COUNTS.values()) == 7000


def test_every_roster_team_has_a_profile_mix_summing_to_one(example_series):
    assert set(example_series["team_profiles"]) == set(ROSTER_COUNTS)
    for team, spec in example_series["team_profiles"].items():
        mix = spec["profile_mix"]
        assert sum(mix.values()) == pytest.approx(1.0, abs=1e-9), team
        assert set(mix) <= set(example_series["profiles"]), team


# --- profiles ---------------------------------------------------------------------------------


def test_power_profile_spike_fields_are_valid(assignment):
    config = assignment[0]
    power = config.profiles["power_profile"]
    assert power.has_personal_spikes
    assert 0.25 <= power.spike_probability <= 0.5   # spikes often: visible in a 28-day window
    assert 1 <= power.spike_multiplier_min <= power.spike_multiplier_max <= 3
    # Only the power profile spikes on its own.
    assert [n for n, p in config.profiles.items() if p.has_personal_spikes] == ["power_profile"]


def test_power_is_far_above_heavy_and_idle_far_below_light(assignment):
    profiles = assignment[0].profiles
    assert profiles["power_profile"].daily_input_tokens >= 2 * profiles["heavy_profile"].daily_input_tokens
    assert profiles["idle_profile"].daily_input_tokens <= 400_000
    assert profiles["idle_profile"].daily_input_tokens * 5 <= profiles["light_profile"].daily_input_tokens


def test_exactly_two_development_power_users_and_no_others(assignment):
    counts = assignment[2]
    assert counts["Development"]["power_profile"] == 2
    assert counts["Platform"]["power_profile"] == 0
    assert counts["QA"]["power_profile"] == 0


def test_development_has_heavy_mid_light_idle_and_power_users(assignment):
    counts = assignment[2]["Development"]
    assert counts["heavy_profile"] >= 1 and counts["mid_profile"] >= 1
    assert counts["light_profile"] >= 1 and counts["idle_profile"] >= 1
    assert counts["power_profile"] == 2


def test_idle_tokens_are_at_most_15_percent_of_the_org_median_user(assignment):
    """Computed from the profile definitions and the assignment, not from generated usage."""
    config, rows, _ = assignment
    tokens = sorted(config.profiles[row.profile].daily_input_tokens for row in rows)
    median = tokens[len(tokens) // 2]
    idle = config.profiles["idle_profile"].daily_input_tokens
    assert idle <= 0.15 * median
    assert sum(1 for row in rows if row.profile == "idle_profile") >= 4


def test_at_least_four_idle_users_org_wide_in_every_team(assignment):
    counts = assignment[2]
    assert sum(c["idle_profile"] for c in counts.values()) >= 4
    assert all(counts[team]["idle_profile"] >= 1 for team in ROSTER_COUNTS)


def test_team_counts_follow_the_roster(assignment):
    counts = assignment[2]
    for team, n in ROSTER_COUNTS.items():
        assert sum(counts[team].values()) == n
        mix = assignment[0].team_profiles[team].profile_mix
        assert dict(counts[team]) == {k: v for k, v in allocate_integer(n, mix).items() if v}


def test_platform_is_the_light_team(assignment):
    """Platform carries the margin: the lowest average profile tokens per user of the three."""
    config, _, counts = assignment
    tokens = {p: cfg.daily_input_tokens for p, cfg in config.profiles.items()}
    average = {team: sum(tokens[p] * n for p, n in counts[team].items()) / ROSTER_COUNTS[team]
               for team in ROSTER_COUNTS}
    assert min(average, key=average.get) == "Platform"
    assert counts["Platform"]["heavy_profile"] <= 1


# --- curves and events -------------------------------------------------------------------------


def test_every_team_has_a_curve_and_each_reference_resolves(example_series):
    curve = example_series["curve"]
    assert set(curve["teams"]) == set(ROSTER_COUNTS)
    root = EXAMPLE / curve["artifacts"]
    for team, ref in curve["teams"].items():
        name, _, curve_id = ref.partition(":")
        assert curve_id, f"{team}: reference must pin an id"
        directory = resolve_curve_ref(root, ref)
        assert directory == root / name / curve_id
        artifact = load_curve_artifact(directory)  # checks manifest, config hash and id
        assert (artifact.name, artifact.curve_id) == (name, curve_id)
        assert artifact.axis == (1, 61)


def test_no_unreferenced_curve_artifact_is_left_behind(example_series):
    curve = example_series["curve"]
    referenced = {tuple(ref.split(":")) for ref in curve["teams"].values()}
    root = EXAMPLE / curve["artifacts"]
    on_disk = {(d.parent.name, d.name) for d in root.glob("*/*") if d.is_dir()}
    assert on_disk == referenced


def test_curve_sources_exist_for_every_team_and_match_their_artifact(example_series):
    for team, ref in example_series["curve"]["teams"].items():
        name = ref.partition(":")[0]
        source = yaml.safe_load((EXAMPLE / "curves" / f"{name}.yaml").read_text(encoding="utf-8"))
        assert source["name"] == name, team
        artifact_config = yaml.safe_load(
            (EXAMPLE / "curves" / "artifacts" / name / ref.partition(":")[2] / "config.yaml").read_text())
        assert artifact_config["anchors"] == source["anchors"], f"{name}.yaml changed: rerun curve.sh"


def _levels(example_series, team: str) -> list[float]:
    """The team's curve level on each of the 120 series days (the curve is stretched over them)."""
    name, _, curve_id = example_series["curve"]["teams"][team].partition(":")
    anchors = load_curve_artifact(EXAMPLE / "curves" / "artifacts" / name / curve_id).anchors
    return evaluate_curve(anchors, example_series["series"]["days"])


def test_development_has_a_crunch_in_the_last_60_days_and_a_smaller_earlier_bump(example_series):
    levels = _levels(example_series, "Development")
    assert len(levels) == 120
    crunch = max(levels[60:])
    assert crunch >= 1.4, "the release crunch must sit inside the last 60 days of the series"
    # A crunch in the first 60 days would not be visible in the dashboard's windows; the bump
    # before it is smaller than the crunch and still a real step above a regular day.
    bump = max(levels[20:60])
    assert 1.2 <= bump < crunch
    # The last weeks stay quiet: the level the forecast extrapolates is not a crunch's tail.
    assert max(levels[95:]) <= 1.1 and min(levels[95:]) >= 0.9


def test_qa_has_a_crunch_in_the_last_60_days(example_series):
    levels = _levels(example_series, "QA")
    assert max(levels[60:]) >= 1.3
    assert max(levels[95:]) <= 1.1 and min(levels[95:]) >= 0.9


def test_platform_curve_has_no_crunch_anywhere(example_series):
    """Platform is the team with margin: a crunch anywhere in the series reaches the
    forecaster's 90-day lookback for some end date (late in a month it reaches day 0)."""
    levels = _levels(example_series, "Platform")
    assert 0.90 <= min(levels) and max(levels) <= 1.10


def test_no_curve_goes_above_2_3(example_series):
    for team in ROSTER_COUNTS:
        assert max(_levels(example_series, team)) <= 2.3, team


def test_busy_days_rule_is_org_wide_and_undated(example_series):
    rules = example_series["events"]["spike_rules"]
    assert len(rules) == 1
    rule = rules[0]
    assert rule["targets"] == {"teams": [], "agents": [], "models": []}
    assert "date_range" not in rule  # nothing for cli_synth_org's retiming to shift
    assert 0.05 <= rule["probability"] <= 0.2
    assert 1.3 <= rule["multiplier_min"] <= rule["multiplier_max"] <= 1.6


# --- a short generated series ------------------------------------------------------------------


def test_power_users_are_the_top_two_development_users_on_a_30_day_series(chain, tmp_path):
    config = load_series_config(_series_config(chain, tmp_path, start_date="2026-09-07", days=30))
    result = run_series(config, tmp_path / "series")
    series_dir = Path(result.output_dir)
    team_of = {}
    for line in (chain / "roster" / "teams.jsonl").read_text().splitlines():
        row = json.loads(line)
        for uid in row["user_ids"]:
            team_of[uid] = row["team_name"]
    profile = {r["user_id"]: r["profile"] for r in map(json.loads, (series_dir / "user_profiles.jsonl").read_text().splitlines())}
    tokens: Counter = Counter()
    for line in (series_dir / "usage.jsonl").read_text().splitlines():
        row = json.loads(line)
        tokens[row["user_id"]] += sum(row["tokens"].values())
    development = sorted((u for u in tokens if team_of[u] == "Development"), key=tokens.get, reverse=True)
    assert [profile[u] for u in development[:2]] == ["power_profile", "power_profile"]
    heavy = [tokens[u] for u in tokens if profile[u] == "heavy_profile"]
    assert min(tokens[u] for u in development[:2]) >= 2 * max(heavy)
    # Personal spikes appear, and only for power users.
    events = [json.loads(line) for line in (series_dir / "generation_events.jsonl").read_text().splitlines()]
    personal = [e for e in events if e.get("kind") == "personal_spike"]
    assert personal and {e["profile"] for e in personal} == {"power_profile"}


# --- forecast landing against the budgets ------------------------------------------------------

LANDING_DATES = ("2026-10-09", "2026-10-29", "2026-11-29")


def _month_end(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _landings(chain: Path, tmp: Path, end: date) -> dict:
    """Series for the example ending `end` -> baseline forecast and cost -> landing per scope.

    Landing = spend to date in the current month (the series rows priced with the model
    catalog) + the mean forecast from the day after `end`; p90 adds the 90th percentile of the
    forecast. Both for the current month and the next, as a fraction of the scope's budget
    (`cur` and `next`). This is the same arithmetic the dashboard API applies to the export.
    """
    from forecast.cost import load_cost_run
    from forecast.cost.rates import type_rates
    from forecast.cost.schemas import TOKEN_TYPES
    from forecast.dashboard.runner import run_dashboard_forecast
    from common.model_pricing import rate_sheet
    import numpy as np

    days = _yaml("series.yaml")["series"]["days"]
    config = load_series_config(_series_config(chain, tmp, start_date=(end - timedelta(days=days - 1)).isoformat()))
    series_dir = Path(run_series(config, tmp / "series").output_dir)
    presets = tmp / "presets.yaml"
    presets.write_text(yaml.safe_dump({"schema": "dashboard-presets/1.0", "presets": [
        {"id": "baseline", "label": "Current trajectory", "description": "baseline"}]}))
    result = run_dashboard_forecast(series_dir, presets, tmp / "forecast")
    run = load_cost_run(result.cost_dirs["baseline"])
    dates = [date.fromisoformat(d) for d in run.index["dates"]]

    first = end + timedelta(days=1)
    month_start = first if first.month != end.month else end.replace(day=1)
    months = {"cur": (month_start, _month_end(month_start))}
    months["next"] = (months["cur"][1] + timedelta(days=1), _month_end(months["cur"][1] + timedelta(days=1)))

    team_of = {}
    for line in (series_dir / "inputs" / "roster" / "teams.jsonl").read_text().splitlines():
        row = json.loads(line)
        for uid in row["user_ids"]:
            team_of[uid] = row["team_name"]
    rows = [json.loads(line) for line in (series_dir / "usage.jsonl").read_text().splitlines()]
    start = min(date.fromisoformat(r["date"]) for r in rows)
    span = tuple(start + timedelta(days=i) for i in range((end - start).days + 1))
    sheet = rate_sheet("", sorted({r["model"] for r in rows}), start, end)
    rates = {m: type_rates(sheet, m, span) for m in {r["model"] for r in rows}}
    spend: dict[str, dict[date, float]] = {"organization": {}, **{t: {} for t in ROSTER_COUNTS}}
    for r in rows:
        d = date.fromisoformat(r["date"])
        usd = sum(r["tokens"][k] * rates[r["model"]][k][(d - start).days] for k in TOKEN_TYPES)
        for scope in ("organization", team_of[r["user_id"]]):
            spend[scope][d] = spend[scope].get(d, 0.0) + usd

    budgets = _yaml("org.yaml")["budgets"]
    budget = {"organization": budgets["org_usd"], **budgets["teams"]}
    out = {}
    for scope in budget:
        stype = "organization" if scope == "organization" else "team"
        entry = next(e for e in run.index["scopes"]
                     if e["scope_type"] == stype and (stype == "organization" or e["scope_name"] == scope))
        paths = run.scope_paths(entry)
        out[scope] = {}
        for label, (lo_day, hi_day) in months.items():
            lo, hi = dates.index(max(lo_day, first)), dates.index(hi_day)
            total = paths[lo:hi + 1].sum(axis=0)
            actual = sum(v for d, v in spend[scope].items() if lo_day <= d <= end)
            out[scope][label] = ((actual + float(total.mean())) / budget[scope],
                                 (actual + float(np.quantile(total, 0.9))) / budget[scope])
    return out


@pytest.fixture(scope="module")
def landings(chain, tmp_path_factory):
    return {d: _landings(chain, tmp_path_factory.mktemp(f"landing_{d}"), date.fromisoformat(d))
            for d in LANDING_DATES}


@pytest.mark.slow
def test_platform_keeps_its_margin_and_the_org_lands_near_its_budget(landings):
    """Platform (10 people, $2,000): landing <= 70% and p90 <= 85% of budget, this month and
    next, at every end date tried; the org lands within 90-120% of $7,000. The end dates are
    the main demo date and two late-month ones, where the forecaster's 90-day lookback reaches
    the furthest back into the series (the sweep of the full config covers every third day)."""
    for end, scopes in landings.items():
        for month in ("cur", "next"):
            landing, p90 = scopes["Platform"][month]
            assert landing <= 0.70, f"Platform {month} landing {landing:.0%} at {end}"
            assert p90 <= 0.85, f"Platform {month} p90 {p90:.0%} at {end}"
            org = scopes["organization"][month][0]
            assert 0.90 <= org <= 1.20, f"org {month} landing {org:.0%} at {end}"


@pytest.mark.slow
def test_development_is_over_budget_but_not_absurdly_and_qa_near_its_line(landings):
    """Development: 110-140% of $4,000 at the main demo date (this month), and never below
    100% or above 160% at the other end dates; QA within 85-115% at the main date."""
    main = landings["2026-10-09"]
    assert 1.10 <= main["Development"]["cur"][0] <= 1.40, main["Development"]
    assert 0.85 <= main["QA"]["cur"][0] <= 1.15, main["QA"]
    for end, scopes in landings.items():
        for month in ("cur", "next"):
            assert 1.00 <= scopes["Development"][month][0] <= 1.60, f"Development {month} at {end}"
