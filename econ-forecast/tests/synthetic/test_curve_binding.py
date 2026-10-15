"""Task 2 -- curve-guided series generation: binding, semantics, provenance, edges.

Centerpiece: a flat / absent curve must be a byte-identical no-op against today's
generator, under REALISTIC (nonzero jitter/weekend/inactivity/mix, plus an active spike)
settings -- a no-op that only holds with everything else turned off would be hollow.
"""
from __future__ import annotations

import copy
import hashlib
import json

import pytest
import yaml
from pydantic import ValidationError

from forecast.artifact_io import read_records
from forecast.curve.config import CurveConfig
from forecast.curve.runner import run_curve
from forecast.synthetic.config import (CurveBindings, RosterConfig, SeriesConfig, UsersConfig,
                                       load_series_config, validate_series_inputs)
from forecast.synthetic.errors import InputDataError
from forecast.synthetic.models import TeamRecord, UserRecord
from forecast.synthetic.profiles import assign_profiles
from forecast.synthetic.runner import run_roster, run_series, run_users
from forecast.synthetic.series import CurveLevels, generate_days

FLAT = [[0, "1.0"], [1, "1.0"]]


# --- curve-artifact fixture helper (real artifacts via run_curve, never hand-faked) --

def _curve_config(name, anchors, axis=(0, 1)) -> CurveConfig:
    return CurveConfig.model_validate({
        "schema_version": 1, "name": name, "interp": "pchip",
        "axis": {"start": axis[0], "end": axis[1]}, "anchors": anchors,
        "preview": {}, "description": None,
    })


def _processed(row):
    t = row.tokens
    return t.input + t.cache_write_5m + t.cache_write_1h + t.cache_read


def make_curve(root, name, anchors, axis=(0, 1)) -> str:
    """Build one real, complete curve artifact under `root`; return its `name:id` ref."""
    result = run_curve(_curve_config(name, anchors, axis), root)
    assert result is not None
    return f"{result.output_dir.parent.name}:{result.output_dir.name}"


# --- full-pipeline fixtures (real users/roster; realistic, non-degenerate settings) --

@pytest.fixture
def raw_config():
    return {
        "schema_version": 3,
        "series": {"users_file": "unused", "roster_file": "unused",
                   "start_date": "2026-07-06",  # a Monday
                   "days": 10, "timezone": "UTC", "profile_seed": 100, "usage_seed": 123},
        "defaults": {"daily_variation_pct": 25, "output_ratio": .03, "cache_hit_ratio": .8,
                     "cache_hit_variation_pp": 5, "cache_write_ratio": 0.997, "cache_write_1h_share": 0.97, "mix_variation_pct": 15,
                     "daily_zero_probability": .1, "weekend_reduction_probability": .5,
                     "weekend_reduction_factor": .3},
        "profiles": {"light": {"daily_input_tokens": 100_000},
                     "heavy": {"daily_input_tokens": 500_000}},
        "team_profiles": {
            "team1": {"profile_mix": {"light": .7, "heavy": .3}, "project_mix": {"Payments": 1}},
            "team2": {"profile_mix": {"light": .3, "heavy": .7}, "project_mix": {"Payments": 1}},
        },
        "projects": [{"name": "Payments"}],
        "agents": {"claude": {"billing_mode": "api", "usage_share": .6,
                              "models": {"sonnet": .75, "opus": .25}},
                   "codex": {"billing_mode": "api", "usage_share": .4,
                             "models": {"terra": .75, "sol": .25}}},
        "events": {"spike_rules": [{
            "id": "release", "probability": 1.0, "multiplier_min": 2, "multiplier_max": 2,
            "date_range": {"start_date": "2026-07-06", "end_date": "2026-07-10"},
            "targets": {"teams": [], "agents": [], "models": []},
        }], "zero_periods": []},
    }


@pytest.fixture
def prepared(tmp_path, raw_config):
    user_config = UsersConfig.model_validate({"schema_version": 1, "users": {
        "count": 12, "org_domain": "talium.ai", "locale": "en_US", "seed": 42}})
    run_users(user_config, tmp_path / "users")
    roster_config = RosterConfig.model_validate({"schema_version": 1, "roster": {
        "users_file": tmp_path / "users/users.jsonl", "seed": 43,
        "teams": [{"team_name": "team1", "count": 5}, {"team_name": "team2", "count": 5}]}})
    run_roster(roster_config, tmp_path / "roster")
    raw_config["series"]["users_file"] = str(tmp_path / "users/users.jsonl")
    raw_config["series"]["roster_file"] = str(tmp_path / "roster/teams.jsonl")
    return raw_config


@pytest.fixture
def curves_root(tmp_path):
    return tmp_path / "curves"


# --- CurveLevels unit ------------------------------------------------------------

def test_curve_levels_identity_default_is_flat_everywhere():
    levels = CurveLevels()
    assert levels.baseline("any-team", 0) == 1.0
    assert levels.baseline("any-team", 999) == 1.0
    assert levels.agent("any-agent", 0) == 1.0


def test_curve_levels_team_overrides_org_else_falls_back():
    levels = CurveLevels(org_levels=[10.0, 20.0], team_levels={"t1": [1.0, 2.0]})
    assert levels.baseline("t1", 0) == 1.0
    assert levels.baseline("t1", 1) == 2.0
    assert levels.baseline("t2", 0) == 10.0
    assert levels.baseline("t2", 1) == 20.0


def test_curve_levels_flat_when_neither_org_nor_team_bound():
    levels = CurveLevels(team_levels={"other": [5.0]})
    assert levels.baseline("t1", 0) == 1.0


def test_curve_levels_agent_multiplies_or_flat():
    levels = CurveLevels(agent_levels={"claude": [1.0, 3.0]})
    assert levels.agent("claude", 1) == 3.0
    assert levels.agent("codex", 0) == 1.0


# --- generation semantics (direct generate_days, in-memory CurveLevels, no I/O) -----

def _semantics_config(*, days=1, variation=0, weekend_probability=0, zero_probability=0,
                      mix_variation=0):
    return SeriesConfig.model_validate({
        "schema_version": 3,
        "series": {"users_file": "unused", "roster_file": "unused", "start_date": "2026-07-06",
                   "days": days, "timezone": "UTC", "profile_seed": 1, "usage_seed": 2},
        "defaults": {"daily_variation_pct": variation, "output_ratio": .03,
                     "cache_hit_ratio": .8, "cache_hit_variation_pp": 0, "cache_write_ratio": 0.997, "cache_write_1h_share": 0.97,
                     "mix_variation_pct": mix_variation, "daily_zero_probability": zero_probability,
                     "weekend_reduction_probability": weekend_probability,
                     "weekend_reduction_factor": 1},
        "profiles": {"only": {"daily_input_tokens": 1_000_000}},
        "team_profiles": {"team": {"profile_mix": {"only": 1}, "project_mix": {"Project": 1}}},
        "projects": [{"name": "Project"}],
        "agents": {"claude": {"billing_mode": "api", "usage_share": .6, "models": {"sonnet": 1}},
                   "codex": {"billing_mode": "api", "usage_share": .4, "models": {"terra": 1}}},
        "events": {"spike_rules": [], "zero_periods": []},
    })


def _population(n=1):
    users = [UserRecord(user_id=f"u{i:02}", org_id="org", name=f"User {i}", email=f"u{i}@org.test")
             for i in range(n)]
    team = TeamRecord(team_id="t", org_id="org", team_name="team", user_ids=[u.user_id for u in users])
    return users, [team]


def _run(cfg, n=1, curve_levels=None):
    users, teams = _population(n)
    assignments = assign_profiles(cfg, teams)
    return list(generate_days(cfg, users, teams, assignments, curve_levels=curve_levels))


def test_org_curve_scales_the_daily_baseline_exactly():
    cfg = _semantics_config(days=3)
    flat = _run(cfg)
    flat_total = sum(_processed(r) for r in flat[0].usage)
    bent = _run(cfg, curve_levels=CurveLevels(org_levels=[1.0, 2.0, 4.0]))
    totals = [sum(_processed(r) for r in day.usage) for day in bent]
    assert totals == [flat_total, flat_total * 2, flat_total * 4]


def test_team_curve_overrides_org_without_stacking():
    cfg = _semantics_config(days=1)
    flat_total = sum(_processed(r) for r in _run(cfg)[0].usage)
    levels = CurveLevels(org_levels=[5.0], team_levels={"team": [2.0]})
    bent_total = sum(_processed(r) for r in _run(cfg, curve_levels=levels)[0].usage)
    assert bent_total == flat_total * 2
    assert bent_total != flat_total * 10  # would be org*team if the org curve incorrectly stacked


def test_flat_agent_reproduces_the_configured_ratio_exactly():
    cfg = _semantics_config(days=1)
    totals = {}
    for row in _run(cfg)[0].usage:
        totals[row.agent_type] = totals.get(row.agent_type, 0) + _processed(row)
    assert totals == {"claude": 600_000, "codex": 400_000}


def test_agent_curve_shifts_the_split_and_renormalizes():
    cfg = _semantics_config(days=1)
    levels = CurveLevels(agent_levels={"claude": [2.0]})
    totals = {}
    for row in _run(cfg, curve_levels=levels)[0].usage:
        totals[row.agent_type] = totals.get(row.agent_type, 0) + _processed(row)
    # claude 0.6*2=1.2, codex 0.4*1=0.4, sum=1.6 -> claude 75%, codex 25%
    assert totals == {"claude": 750_000, "codex": 250_000}


def test_curve_never_changes_coverage_or_activity_counts():
    cfg = _semantics_config(days=5, variation=10, weekend_probability=.4,
                            zero_probability=.2, mix_variation=10)
    flat = _run(cfg, n=10)
    bent = _run(cfg, n=10, curve_levels=CurveLevels(
        org_levels=[1, 50, 1, 50, 1], agent_levels={"claude": [.1, 5, .1, 5, .1]}))
    for f, b in zip(flat, bent):
        assert f.active_users == b.active_users
        assert f.inactive_users == b.inactive_users
        assert f.coverage == b.coverage


def test_all_bound_agents_at_level_zero_raises_clear_error():
    cfg = _semantics_config(days=1)
    users, teams = _population(1)
    assignments = assign_profiles(cfg, teams)
    levels = CurveLevels(agent_levels={"claude": [0.0], "codex": [0.0]})
    with pytest.raises(InputDataError, match="level 0"):
        list(generate_days(cfg, users, teams, assignments, curve_levels=levels))


# --- THE centerpiece: flat = V1 byte-identical regression --------------------------

def test_flat_curve_is_byte_identical_to_v1(tmp_path, prepared, curves_root):
    """No `curve:`, an empty `curve:` block, and an all-flat curve bound to
    org + every team + every agent must all produce byte-identical usage/coverage/
    events for the same seeds, under realistic jitter/weekend/inactivity/mix settings
    plus an active spike rule (not a hollow all-zero-jitter proof)."""
    no_curve_cfg = SeriesConfig.model_validate(copy.deepcopy(prepared))
    no_curve_out = tmp_path / "no-curve"
    run_series(no_curve_cfg, no_curve_out)

    curves_root.mkdir(parents=True, exist_ok=True)
    empty_raw = copy.deepcopy(prepared)
    empty_raw["curve"] = {"artifacts": str(curves_root)}
    empty_out = tmp_path / "empty-curve"
    run_series(SeriesConfig.model_validate(empty_raw), empty_out)

    flat_ref = make_curve(curves_root, "flat", FLAT)
    bound_raw = copy.deepcopy(prepared)
    bound_raw["curve"] = {
        "artifacts": str(curves_root),
        "org": flat_ref,
        "teams": {"team1": flat_ref, "team2": flat_ref},
        "agents": {"claude": flat_ref, "codex": flat_ref},
    }
    bound_out = tmp_path / "bound-curve"
    run_series(SeriesConfig.model_validate(bound_raw), bound_out)

    # Sanity: this is not a hollow all-zero-jitter proof -- the spike actually fired.
    assert (no_curve_out / "generation_events.jsonl").read_bytes()

    for name in ("usage.jsonl", "coverage.jsonl", "generation_events.jsonl"):
        a = (no_curve_out / name).read_bytes()
        b = (empty_out / name).read_bytes()
        c = (bound_out / name).read_bytes()
        assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest(), \
            f"{name}: empty-curve differs from no-curve"
        assert hashlib.sha256(a).hexdigest() == hashlib.sha256(c).hexdigest(), \
            f"{name}: all-flat-bound-curve differs from no-curve"
        assert a == b == c


# --- config binding: CurveBindings ref format --------------------------------------

@pytest.mark.parametrize("ref", ["name", "name-2", "name_x", "n1:0123456789abcdef"])
def test_curve_ref_format_accepts_name_and_name_id(tmp_path, ref):
    CurveBindings.model_validate({"artifacts": str(tmp_path), "org": ref})


@pytest.mark.parametrize("ref", [
    "Bad Name", "name:short", "name:0123456789ABCDEF",
    "", ":0123456789abcdef", "name:0123456789abcdef0",
])
def test_curve_ref_format_rejects_bad_strings(tmp_path, ref):
    with pytest.raises(ValidationError):
        CurveBindings.model_validate({"artifacts": str(tmp_path), "org": ref})


def test_curve_artifacts_path_resolves_relative_to_series_yaml(tmp_path, raw_config):
    raw_config["series"]["users_file"] = "users.jsonl"
    raw_config["series"]["roster_file"] = "roster.jsonl"
    raw_config["curve"] = {"artifacts": "../curves"}
    nested = tmp_path / "nested"
    nested.mkdir()
    series_path = nested / "series.yaml"
    series_path.write_text(yaml.safe_dump(raw_config))
    config = load_series_config(series_path)
    assert config.curve.artifacts == (tmp_path / "curves").resolve()


def test_curve_artifacts_path_absolute_stays_put(tmp_path, raw_config):
    raw_config["series"]["users_file"] = "users.jsonl"
    raw_config["series"]["roster_file"] = "roster.jsonl"
    absolute = (tmp_path / "abs-curves").resolve()
    raw_config["curve"] = {"artifacts": str(absolute)}
    series_path = tmp_path / "series.yaml"
    series_path.write_text(yaml.safe_dump(raw_config))
    config = load_series_config(series_path)
    assert config.curve.artifacts == absolute


# --- config binding: validate_series_inputs ----------------------------------------

def _validate(raw):
    config = SeriesConfig.model_validate(raw)
    users = read_records(config.series.users_file, UserRecord)
    teams = read_records(config.series.roster_file, TeamRecord)
    validate_series_inputs(config, users, teams)


def test_validate_series_inputs_rejects_unknown_team(prepared, curves_root):
    flat_ref = make_curve(curves_root, "flat", FLAT)
    raw = copy.deepcopy(prepared)
    raw["curve"] = {"artifacts": str(curves_root), "teams": {"not-a-team": flat_ref}}
    with pytest.raises(InputDataError, match="not-a-team"):
        _validate(raw)


def test_validate_series_inputs_rejects_unknown_agent(prepared, curves_root):
    flat_ref = make_curve(curves_root, "flat", FLAT)
    raw = copy.deepcopy(prepared)
    raw["curve"] = {"artifacts": str(curves_root), "agents": {"not-an-agent": flat_ref}}
    with pytest.raises(InputDataError, match="not-an-agent"):
        _validate(raw)


def test_validate_series_inputs_rejects_non_directory_artifacts(prepared, tmp_path):
    not_a_dir = tmp_path / "afile"
    not_a_dir.write_text("x")
    raw = copy.deepcopy(prepared)
    raw["curve"] = {"artifacts": str(not_a_dir)}
    with pytest.raises(InputDataError, match="not a directory"):
        _validate(raw)


def test_validate_series_inputs_rejects_missing_name_id(prepared, curves_root):
    curves_root.mkdir(parents=True, exist_ok=True)
    raw = copy.deepcopy(prepared)
    raw["curve"] = {"artifacts": str(curves_root), "org": "nonexistent:0123456789abcdef"}
    with pytest.raises(InputDataError, match="org"):
        _validate(raw)


def test_validate_series_inputs_rejects_ambiguous_bare_name(prepared, curves_root):
    make_curve(curves_root, "dup", [[0, "1.0"], [1, "1.0"]])
    make_curve(curves_root, "dup", [[0, "1.0"], [1, "2.0"]])
    raw = copy.deepcopy(prepared)
    raw["curve"] = {"artifacts": str(curves_root), "org": "dup"}
    with pytest.raises(InputDataError, match="ambiguous"):
        _validate(raw)


def test_validate_series_inputs_accepts_bare_unique_name(prepared, curves_root):
    make_curve(curves_root, "solo", FLAT)
    raw = copy.deepcopy(prepared)
    raw["curve"] = {"artifacts": str(curves_root), "org": "solo"}
    _validate(raw)  # must not raise


# --- provenance ---------------------------------------------------------------------

def test_bound_run_manifest_has_curve_section_and_snapshots_exist(prepared, curves_root, tmp_path):
    flat_ref = make_curve(curves_root, "flat", FLAT)
    name, curve_id = flat_ref.split(":")
    raw = copy.deepcopy(prepared)
    raw["curve"] = {"artifacts": str(curves_root), "org": flat_ref,
                     "teams": {"team1": flat_ref}, "agents": {"claude": flat_ref}}
    result = run_series(SeriesConfig.model_validate(raw), tmp_path / "bound")
    manifest = json.loads((result.output_dir / "manifest.json").read_text())
    curve_section = manifest["curve"]
    for entry in (curve_section["org"], curve_section["teams"]["team1"], curve_section["agents"]["claude"]):
        assert entry["name"] == name
        assert entry["id"] == curve_id
        snapshot_dir = result.output_dir / entry["snapshot"]
        assert (snapshot_dir / "manifest.json").is_file()
        config_bytes = (snapshot_dir / "config.yaml").read_bytes()
        assert hashlib.sha256(config_bytes).hexdigest() == entry["config_sha256"]


# --- edges ----------------------------------------------------------------------

def test_series_days_equal_one_uses_the_first_anchor_level(prepared, curves_root, tmp_path):
    raw = copy.deepcopy(prepared)
    raw["series"]["days"] = 1
    raw["events"] = {"spike_rules": [], "zero_periods": []}
    flat_out = tmp_path / "flat-one-day"
    run_series(SeriesConfig.model_validate(copy.deepcopy(raw)), flat_out)

    ramp_ref = make_curve(curves_root, "ramp", [[0, "1.0"], [1, "5.0"]])
    raw["curve"] = {"artifacts": str(curves_root), "org": ramp_ref}
    bound_out = tmp_path / "bound-one-day"
    run_series(SeriesConfig.model_validate(raw), bound_out)

    def total(path):
        return sum(sum(json.loads(line)["tokens"][name] for name in (
                       "input", "cache_write_5m", "cache_write_1h", "cache_read"))
                   for line in path.read_text().splitlines())

    # n == 1 evaluates the curve at normalized position 0.0 -- exactly the FIRST
    # anchor's level (1.0), never touching the second anchor (5.0).
    assert total(bound_out / "usage.jsonl") == total(flat_out / "usage.jsonl")


def test_bound_run_is_deterministic_on_repeat(prepared, curves_root, tmp_path):
    ramp_ref = make_curve(curves_root, "ramp-det", [[0, "1.0"], [1, "3.0"]])
    raw = copy.deepcopy(prepared)
    raw["curve"] = {"artifacts": str(curves_root), "org": ramp_ref,
                     "teams": {"team2": ramp_ref}, "agents": {"codex": ramp_ref}}
    out1, out2 = tmp_path / "run1", tmp_path / "run2"
    run_series(SeriesConfig.model_validate(copy.deepcopy(raw)), out1)
    run_series(SeriesConfig.model_validate(copy.deepcopy(raw)), out2)
    for name in ("usage.jsonl", "coverage.jsonl", "generation_events.jsonl"):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes()
