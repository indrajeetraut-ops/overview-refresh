"""Per-profile personal spikes (`spike_probability`, `spike_multiplier_min/max` on a Profile).

Layer 1 only: validation, draw semantics (active weekdays only, one draw per user-day keyed by
usage_seed/domain/user/date), composition with the other multipliers, events and report, and the
guarantee that a config without the fields is byte-identical to the generator before the feature.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from forecast.synthetic import runner
from forecast.synthetic.config import (RosterConfig, SeriesConfig, UsersConfig, load_series_config)
from forecast.synthetic.errors import ConfigError, InputDataError
from forecast.synthetic.models import (ForcedZeroEvent, GenerationEvent, PersonalSpikeEvent,
                                       SpikeEvent, TeamRecord, UserRecord)
from forecast.synthetic.profiles import assign_profiles
from forecast.synthetic.randomness import rng_for
from forecast.synthetic.runner import run_roster, run_series, run_users
from forecast.synthetic.series import CurveLevels, _half_up, generate_days

ROOT = Path(__file__).parents[2]
SPIKE = {"spike_probability": 0.5, "spike_multiplier_min": 1.5, "spike_multiplier_max": 2.5}
FIELDS = tuple(SPIKE)
SATURDAY = "2026-07-04"
MONDAY = "2026-07-06"


# --- builders ------------------------------------------------------------------------------

def raw(*, days=1, start=MONDAY, usage_seed=7, variation=0, weekend_probability=0,
        weekend_factor=1, zero_probability=0, profiles=None, mix=None, events=None):
    profiles = profiles if profiles is not None else {
        "plain": {"daily_input_tokens": 1_000_000},
        "power": {"daily_input_tokens": 1_000_000, **SPIKE}}
    return {
        "schema_version": 3,
        "series": {"users_file": "unused", "roster_file": "unused", "start_date": start,
                   "days": days, "timezone": "UTC", "profile_seed": 8, "usage_seed": usage_seed},
        "defaults": {"daily_variation_pct": variation, "output_ratio": .03, "cache_hit_ratio": .8,
                     "cache_hit_variation_pp": 0, "cache_write_ratio": .997,
                     "cache_write_1h_share": .97, "mix_variation_pct": 0,
                     "daily_zero_probability": zero_probability,
                     "weekend_reduction_probability": weekend_probability,
                     "weekend_reduction_factor": weekend_factor},
        "profiles": profiles,
        "team_profiles": {"team": {"profile_mix": mix or {"plain": .5, "power": .5},
                                   "project_mix": {"Project": 1}}},
        "projects": [{"name": "Project"}],
        "agents": {"claude": {"billing_mode": "api", "usage_share": .6,
                              "models": {"opus": .25, "sonnet": .75}},
                   "codex": {"billing_mode": "api", "usage_share": .4,
                             "models": {"sol": .25, "terra": .75}}},
        "events": events or {"spike_rules": [], "zero_periods": []},
    }


def cfg(**kwargs) -> SeriesConfig:
    return SeriesConfig.model_validate(raw(**kwargs))


def population(n, team="team", prefix="u"):
    users = [UserRecord(user_id=f"{prefix}{i:03}", org_id="org", name=f"User {i}",
                        email=f"{prefix}{i}@org.test") for i in range(n)]
    return users, TeamRecord(team_id=f"id-{team}", org_id="org", team_name=team,
                             user_ids=[u.user_id for u in users])


def generate(config, n=10, curve_levels=None):
    users, team = population(n)
    teams = [team]
    assignments = assign_profiles(config, teams)
    return list(generate_days(config, users, teams, assignments, curve_levels=curve_levels)), assignments


def processed(row):
    t = row.tokens
    return t.input + t.cache_write_5m + t.cache_write_1h + t.cache_read


def per_user_day(days):
    """{(date, user): processed input} over all usage rows."""
    totals = {}
    for day in days:
        for row in day.usage:
            key = (day.date, row.user_id)
            totals[key] = totals.get(key, 0) + processed(row)
    return totals


def personal_events(days):
    return [e for day in days for e in day.events if isinstance(e, PersonalSpikeEvent)]


def usage_dump(days):
    return [row.model_dump(mode="json") for day in days for row in day.usage]


def users_of(assignments, profile):
    return {a.user_id for a in assignments if a.profile == profile}


# --- validation ------------------------------------------------------------------------------

def profile_error(**fields):
    with pytest.raises(ValidationError) as caught:
        cfg(profiles={"p": {"daily_input_tokens": 1_000, **fields}}, mix={"p": 1})
    return str(caught.value)


@pytest.mark.parametrize("fields", [
    SPIKE,
    {"spike_probability": 0, "spike_multiplier_min": 1, "spike_multiplier_max": 1},
    {"spike_probability": 1, "spike_multiplier_min": 1, "spike_multiplier_max": 1},
    {"spike_probability": 1.0, "spike_multiplier_min": 3, "spike_multiplier_max": 3},
    {"spike_probability": 0.15, "spike_multiplier_min": 1.0, "spike_multiplier_max": 10},
    {"spike_probability": 0.2, "spike_multiplier_min": 1.5, "spike_multiplier_max": 1.5000001},
])
def test_valid_combinations_and_boundaries(fields):
    config = cfg(profiles={"p": {"daily_input_tokens": 1_000, **fields}}, mix={"p": 1})
    profile = config.profiles["p"]
    assert profile.has_personal_spikes
    assert {k: getattr(profile, k) for k in FIELDS} == {k: float(v) for k, v in fields.items()}


def test_absent_profile_has_no_personal_spikes():
    config = cfg(profiles={"p": {"daily_input_tokens": 1_000}}, mix={"p": 1})
    assert not config.profiles["p"].has_personal_spikes


@pytest.mark.parametrize("present", [
    ("spike_probability",), ("spike_multiplier_min",), ("spike_multiplier_max",),
    ("spike_probability", "spike_multiplier_min"),
    ("spike_probability", "spike_multiplier_max"),
    ("spike_multiplier_min", "spike_multiplier_max"),
])
def test_all_or_nothing(present):
    message = profile_error(**{k: SPIKE[k] for k in present})
    assert "must be set together" in message


def test_probability_zero_still_requires_and_validates_the_multipliers():
    assert "must be set together" in profile_error(spike_probability=0)
    assert "greater than or equal to 1" in profile_error(
        spike_probability=0, spike_multiplier_min=0.5, spike_multiplier_max=2)
    assert "must not be below" in profile_error(
        spike_probability=0, spike_multiplier_min=3, spike_multiplier_max=2)


@pytest.mark.parametrize("probability", [-0.01, 1.01, 2, -1])
def test_probability_outside_unit_interval_rejected(probability):
    assert "spike_probability" in profile_error(
        spike_probability=probability, spike_multiplier_min=1.5, spike_multiplier_max=2.5)


@pytest.mark.parametrize("low,high", [(0.99, 2.5), (0, 2.5), (-1, 2.5), (1.5, 0.5), (1.5, 1.4999)])
def test_multiplier_bounds_rejected(low, high):
    profile_error(spike_probability=0.5, spike_multiplier_min=low, spike_multiplier_max=high)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field", FIELDS)
def test_non_finite_rejected(field, bad):
    fields = {**SPIKE, field: bad}
    assert field in profile_error(**fields)


@pytest.mark.parametrize("bad", ["0.5", True, [0.5], {"a": 1}])
@pytest.mark.parametrize("field", FIELDS)
def test_wrong_types_rejected(field, bad):
    fields = {**SPIKE, field: bad}
    if field != "spike_probability":
        fields[field] = bad if bad != "0.5" else "2"
    assert field in profile_error(**fields)


def test_non_finite_yaml_scalars_rejected(tmp_path):
    for token in (".inf", ".nan", "-.inf"):
        document = raw()
        path = tmp_path / "series.yaml"
        text = yaml.safe_dump(document).replace(
            "spike_multiplier_max: 2.5", f"spike_multiplier_max: {token}")
        path.write_text(text)
        with pytest.raises(ConfigError):
            load_series_config(path)


def test_unknown_profile_fields_still_rejected():
    assert "spike_prob" in profile_error(spike_prob=0.5)
    assert "extra_forbidden" in profile_error(**SPIKE, spike_multiplier_mean=2)


def test_explicit_null_is_the_same_as_absent():
    nulls = cfg(profiles={"p": {"daily_input_tokens": 1_000, **dict.fromkeys(FIELDS)}}, mix={"p": 1})
    absent = cfg(profiles={"p": {"daily_input_tokens": 1_000}}, mix={"p": 1})
    assert nulls.model_dump(mode="json") == absent.model_dump(mode="json")
    assert not nulls.profiles["p"].has_personal_spikes
    # one non-null value with two nulls is a partial set
    assert "must be set together" in profile_error(
        spike_probability=0.5, spike_multiplier_min=None, spike_multiplier_max=None)


def test_dump_omits_unset_fields_and_round_trips_set_ones():
    plain = cfg(profiles={"p": {"daily_input_tokens": 1_000}}, mix={"p": 1})
    assert plain.model_dump(mode="json")["profiles"] == {"p": {"daily_input_tokens": 1_000}}
    spiky = cfg()
    dumped = spiky.model_dump(mode="json")
    assert dumped["profiles"]["plain"] == {"daily_input_tokens": 1_000_000}
    assert dumped["profiles"]["power"] == {"daily_input_tokens": 1_000_000, **SPIKE}
    assert SeriesConfig.model_validate(dumped) == spiky


# --- absent == byte-identical ---------------------------------------------------------------

# Captured from the generator before the feature existed (same configs, run_id and clock fixed).
GOLDEN = {
    "series_small": {
        "config.yaml": "968d6a2d4a356765e263f5b08c222fdbff10b160029f4857d5410e496212aef2",
        "coverage.jsonl": "6439f452a63f6206f6cbe3ecb19b5bb47a6c3bf7646a876c688f80f36f1214da",
        "generation_events.jsonl": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "usage.jsonl": "992f3ab2080ac2fb54e468927fc7e0dffbe78b4f70d9add97b2e8bb6bb42753b",
        "user_profiles.jsonl": "b9a77856e6bdf07adc4dcd727a85f4f8f017392ae700d511bd05100918566fb6",
        "report.md": "16a0216de4c8c041eecd282bedc822f959c1bc54942da10931fa3b73f27c2314",
        "summary": "3e54bed7d98026f14c5eb94f7e17f9a609ce0f5685e36e50631229b26a9d1cb9",
    },
    "series_realistic": {
        "config.yaml": "ac9dce6724e443586c053e1c338dda27d1632aed4ab70ec8475e6f7f70632c40",
        "coverage.jsonl": "1967befd9682b71e95453a4aae7d62ad88bb6931ef3679232266eb68fd4a8439",
        "generation_events.jsonl": "1df6e264033b5cb90153f9a257a9f1e2653822734a1d6a2abecf3c49a0c04bc7",
        "usage.jsonl": "4e84ed9ca8fe4be321079b0cdb57a3cbfd47a679ae18ad0d9a8a1f6188f32890",
        "user_profiles.jsonl": "21633d8fc08b69b8d19f0e2b65e268494aa51c26059d16a5966ecd23527bdbb0",
        "report.md": "70640fb708636a4814efe2bc60c0299ae81b1e402b52504041c37c006fd48cfd",
        "summary": "4b7ed095c8efb1b0154704586af9a4cbaae873238aaf1be725e87894a5c01701",
    },
    "series_shipped": {
        "config.yaml": "c6b9f3817258dda84276cb3cec65f5967ef433b97e1f63d417ff89d9ac038976",
        "coverage.jsonl": "6a97b82514878822a3a619047b28febdc794ba6b723235836262962c23587082",
        "generation_events.jsonl": "426ca02da131036ff5e4ec75cfd013d31f27a408d36d93a649ab4d9748480c2e",
        "usage.jsonl": "3c66c1f0ea75e8c421bdc8a2800b2b3c10267f3d947efefc4c92c2472c375315",
        "user_profiles.jsonl": "786044a6f97da6a7c53767114170aa9bbb9339b9620bc9a58cd6bc11b82a8ca6",
        "report.md": "b77e9d7add6a56cdefa0bc2f4da6ab0e3a1cde1783b90f574240e260be79bfc7",
        "summary": "4c648bce7e41ec6dbecc68439587274ba7bc6e090d7911c5ef17c31a6b4120eb",
    },
}

_DEFAULTS = {"daily_variation_pct": 30, "output_ratio": 0.03, "cache_hit_ratio": 0.8,
             "cache_hit_variation_pp": 5, "cache_write_ratio": 0.997, "cache_write_1h_share": 0.97,
             "mix_variation_pct": 10, "daily_zero_probability": 0.1,
             "weekend_reduction_probability": 0.9, "weekend_reduction_factor": 0.25}
_SERIES_AGENT = {"claude_code": {"billing_mode": "api", "usage_share": 1.0,
                                 "models": {"claude-sonnet-5": 0.7, "claude-opus-5": 0.3}}}


def _small_chain():
    users = {"schema_version": 1, "users": {"count": 8, "org_domain": "org-conf.test", "seed": 4242,
                                            "locale": "en_US", "identity_style": "consistent"}}
    roster = {"schema_version": 1, "roster": {"users_file": "x", "seed": 4242, "teams": [
        {"team_name": "platform", "count": 4}, {"team_name": "payments", "count": 4}]}}
    series = {
        "schema_version": 3,
        "series": {"users_file": "x", "roster_file": "x", "start_date": "2026-01-05", "days": 14,
                   "timezone": "UTC", "profile_seed": 4243, "usage_seed": 4244},
        "defaults": dict(_DEFAULTS),
        "profiles": {"light_profile": {"daily_input_tokens": 200_000},
                     "mid_profile": {"daily_input_tokens": 900_000},
                     "heavy_profile": {"daily_input_tokens": 3_000_000}},
        "team_profiles": {
            "platform": {"profile_mix": {"light_profile": 0.5, "mid_profile": 0.5},
                         "project_mix": {"platform_infra": 0.8, "payments_api": 0.2}},
            "payments": {"profile_mix": {"mid_profile": 0.5, "heavy_profile": 0.5},
                         "project_mix": {"payments_api": 1.0}}},
        "projects": [{"name": "platform_infra"}, {"name": "payments_api"}],
        "agents": copy.deepcopy(_SERIES_AGENT),
        "events": {"spike_rules": [], "zero_periods": []},
    }
    return users, roster, series


def _realistic_chain():
    users = {"schema_version": 1, "users": {"count": 12, "org_domain": "talium.ai",
                                            "locale": "en_US", "seed": 42}}
    roster = {"schema_version": 1, "roster": {"users_file": "x", "seed": 43, "teams": [
        {"team_name": "team1", "count": 5}, {"team_name": "team2", "count": 5}]}}
    series = {
        "schema_version": 3,
        "series": {"users_file": "x", "roster_file": "x", "start_date": "2026-07-06", "days": 10,
                   "timezone": "UTC", "profile_seed": 100, "usage_seed": 123},
        "defaults": {"daily_variation_pct": 25, "output_ratio": .03, "cache_hit_ratio": .8,
                     "cache_hit_variation_pp": 5, "cache_write_ratio": 0.997,
                     "cache_write_1h_share": 0.97, "mix_variation_pct": 15,
                     "daily_zero_probability": .1, "weekend_reduction_probability": .5,
                     "weekend_reduction_factor": .3},
        "profiles": {"light": {"daily_input_tokens": 100_000},
                     "heavy": {"daily_input_tokens": 500_000}},
        "team_profiles": {
            "team1": {"profile_mix": {"light": .7, "heavy": .3}, "project_mix": {"Payments": 1}},
            "team2": {"profile_mix": {"light": .3, "heavy": .7}, "project_mix": {"Payments": 1}}},
        "projects": [{"name": "Payments"}],
        "agents": {"claude": {"billing_mode": "api", "usage_share": .6,
                              "models": {"sonnet": .75, "opus": .25}},
                   "codex": {"billing_mode": "api", "usage_share": .4,
                             "models": {"terra": .75, "sol": .25}}},
        "events": {"spike_rules": [{
            "id": "release", "probability": 1.0, "multiplier_min": 2, "multiplier_max": 2,
            "date_range": {"start_date": "2026-07-06", "end_date": "2026-07-10"},
            "targets": {"teams": [], "agents": [], "models": []}}], "zero_periods": []},
    }
    return users, roster, series


def _shipped_chain():
    base = ROOT / "config/synthetic"
    return tuple(yaml.safe_load((base / name).read_text())
                 for name in ("users.yaml", "roster.yaml", "series.yaml"))


CHAINS = {"series_small": _small_chain, "series_realistic": _realistic_chain,
          "series_shipped": _shipped_chain}


def _run_chain(root: Path, chain, monkeypatch, *, mutate_profiles=None) -> dict:
    """Run users -> roster -> series with a fixed clock and run id; hash the artifacts."""
    monkeypatch.setattr(runner, "_now", lambda: "2026-01-01T00:00:00+00:00")
    users, roster, series = chain()
    if mutate_profiles:
        for profile in series["profiles"].values():
            profile.update(mutate_profiles)
    run_users(UsersConfig.model_validate(users), root / "users", run_id="u")
    roster["roster"]["users_file"] = str(root / "users/users.jsonl")
    run_roster(RosterConfig.model_validate(roster), root / "roster", run_id="r")
    series["series"]["users_file"] = str(root / "users/users.jsonl")
    series["series"]["roster_file"] = str(root / "roster/teams.jsonl")
    path = root / "series.yaml"
    path.write_text(yaml.safe_dump(series))
    run_series(load_series_config(path), root / "series", run_id="s")
    out = root / "series"
    hashes = {name: hashlib.sha256((out / name).read_bytes()).hexdigest() for name in (
        "config.yaml", "coverage.jsonl", "generation_events.jsonl", "usage.jsonl",
        "user_profiles.jsonl")}
    report = (out / "report.md").read_text().replace(str(root), "<ROOT>")
    report = re.sub(r"- Completed: .*", "- Completed: X", report)
    hashes["report.md"] = hashlib.sha256(report.encode()).hexdigest()
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["config_sha256"] == hashes["config.yaml"]
    hashes["summary"] = hashlib.sha256(
        json.dumps(manifest["summary"], sort_keys=True).encode()).hexdigest()
    return hashes


@pytest.mark.parametrize("name", sorted(CHAINS))
def test_absent_fields_are_byte_identical_to_the_generator_before_the_feature(
        name, tmp_path, monkeypatch):
    """usage, coverage, events, profiles, config.yaml (and so config_sha256), report and the
    manifest summary all match hashes captured before the feature existed."""
    assert _run_chain(tmp_path, CHAINS[name], monkeypatch) == GOLDEN[name]


@pytest.mark.parametrize("name", ["series_small", "series_realistic"])
def test_explicit_null_fields_are_byte_identical_to_omitting_them(name, tmp_path, monkeypatch):
    hashes = _run_chain(tmp_path, CHAINS[name], monkeypatch,
                        mutate_profiles=dict.fromkeys(FIELDS))
    assert hashes == GOLDEN[name]


def test_probability_zero_changes_data_not_at_all_but_adds_report_rows(tmp_path, monkeypatch):
    """A configured but never-firing feature leaves every data artifact unchanged; only
    config.yaml (it now lists the fields), the summary and the report gain the feature."""
    hashes = _run_chain(
        tmp_path, CHAINS["series_small"], monkeypatch,
        mutate_profiles={"spike_probability": 0, "spike_multiplier_min": 1.5,
                         "spike_multiplier_max": 2.5})
    golden = GOLDEN["series_small"]
    for name in ("usage.jsonl", "coverage.jsonl", "generation_events.jsonl", "user_profiles.jsonl"):
        assert hashes[name] == golden[name]
    for name in ("config.yaml", "report.md", "summary"):
        assert hashes[name] != golden[name]
    report = (tmp_path / "series/report.md").read_text()
    assert "### Personal spikes by profile" in report
    assert "| heavy_profile | 0 | 0 | 0 |" in report
    manifest = json.loads((tmp_path / "series/manifest.json").read_text())
    assert manifest["summary"]["personal_spike_events"] == 0


def test_same_config_twice_is_deterministic():
    config = cfg(days=14, start=SATURDAY, variation=20, weekend_probability=.5,
                 weekend_factor=.3, zero_probability=.1)
    first, _ = generate(config, n=20)
    second, _ = generate(config, n=20)
    assert [d.model_dump(mode="json") for d in first] == [d.model_dump(mode="json") for d in second]
    assert personal_events(first)


# --- draw semantics ----------------------------------------------------------------------------

def test_probability_one_scales_every_active_weekday_of_the_profile_only():
    config = cfg(days=14, start=MONDAY, profiles={
        "plain": {"daily_input_tokens": 1_000_000},
        "power": {"daily_input_tokens": 1_000_000, "spike_probability": 1,
                  "spike_multiplier_min": 1.5, "spike_multiplier_max": 2.5}})
    days, assignments = generate(config, n=10)
    power = users_of(assignments, "power")
    assert len(power) == 5
    totals = per_user_day(days)
    weekdays = [d.date for d in days if d.date.weekday() < 5]
    assert len(weekdays) == 10
    for day in days:
        for uid in sorted(u.user_id for u in population(10)[0]):
            total = totals[(day.date, uid)]
            if uid in power and day.date.weekday() < 5:
                assert 1_500_000 <= total <= 2_500_000
                assert total != 1_000_000
            else:
                assert total == 1_000_000  # plain user, or power user on a weekend day
    events = personal_events(days)
    assert len(events) == len(power) * len(weekdays)
    assert {e.user_id for e in events} == power
    assert {e.profile for e in events} == {"power"}


def test_probability_zero_and_absent_produce_the_same_usage():
    base = {"plain": {"daily_input_tokens": 1_000_000}, "power": {"daily_input_tokens": 1_000_000}}
    zero = {"plain": base["plain"], "power": {**base["power"], "spike_probability": 0,
                                              "spike_multiplier_min": 1.5,
                                              "spike_multiplier_max": 2.5}}
    kwargs = dict(days=10, variation=20, zero_probability=.1)
    absent, _ = generate(cfg(profiles=base, **kwargs), n=10)
    never, _ = generate(cfg(profiles=zero, **kwargs), n=10)
    assert [d.model_dump(mode="json") for d in absent] == [d.model_dump(mode="json") for d in never]
    assert not personal_events(never)


def test_observed_rate_matches_the_probability():
    # 100 power users x 20 weekdays = 2,000 independent draws at p = 0.5: sd = 22.4, so
    # +-6 sd (135) is a deterministic-seed sanity band, not a flaky statistical test.
    config = cfg(days=28, start=MONDAY, profiles={
        "power": {"daily_input_tokens": 1_000_000, "spike_probability": 0.5,
                  "spike_multiplier_min": 1.5, "spike_multiplier_max": 2.5}}, mix={"power": 1})
    days, _ = generate(config, n=100)
    weekdays = sum(1 for d in days if d.date.weekday() < 5)
    draws = 100 * weekdays
    assert draws == 2_000
    assert abs(len(personal_events(days)) - draws * 0.5) < 135


def test_a_draw_equal_to_the_probability_does_not_spike(monkeypatch):
    """The decision is strictly `draw < spike_probability` (probability 0 can never fire)."""
    from forecast.synthetic import series

    class Edge:
        def __init__(self, value):
            self.value = value

        def random(self):
            return self.value

    real = series.rng_for
    for draw, expected in ((0.5, 0), (0.4999999, 1)):
        monkeypatch.setattr(series, "rng_for", lambda seed, domain, *keys, _draw=draw: (
            Edge(_draw) if domain == "profile_spike" else real(seed, domain, *keys)))
        days, _ = generate(cfg(days=1, profiles={"power": {
            "daily_input_tokens": 1_000_000, "spike_probability": 0.5,
            "spike_multiplier_min": 2, "spike_multiplier_max": 2}}, mix={"power": 1}), n=1)
        assert len(personal_events(days)) == expected


def test_multiplier_is_uniform_within_bounds_and_hits_both_halves():
    config = cfg(days=14, profiles={
        "power": {"daily_input_tokens": 1_000_000, "spike_probability": 1,
                  "spike_multiplier_min": 1.5, "spike_multiplier_max": 2.5}}, mix={"power": 1})
    days, _ = generate(config, n=50)
    multipliers = [e.multiplier for e in personal_events(days)]
    assert len(multipliers) == 50 * 10
    assert all(1.5 <= m <= 2.5 for m in multipliers)
    assert min(multipliers) < 1.6 and max(multipliers) > 2.4
    assert 1.9 < sum(multipliers) / len(multipliers) < 2.1
    for event in personal_events(days):
        assert event.baseline_processed_input == 1_000_000
        assert event.baseline_processed_input + event.incremental_processed_input == round(
            event.multiplier * 1_000_000)


def test_min_equals_max_gives_a_fixed_multiplier():
    config = cfg(days=5, profiles={
        "power": {"daily_input_tokens": 1_000_000, "spike_probability": 1,
                  "spike_multiplier_min": 3, "spike_multiplier_max": 3}}, mix={"power": 1})
    days, _ = generate(config, n=4)
    assert set(per_user_day(days).values()) == {3_000_000}
    assert {e.multiplier for e in personal_events(days)} == {3.0}


def test_weekends_never_spike_with_or_without_weekend_reduction():
    for weekend_probability in (0, 1):
        config = cfg(days=14, start=SATURDAY, weekend_probability=weekend_probability,
                     weekend_factor=.5, profiles={
                         "power": {"daily_input_tokens": 1_000_000, "spike_probability": 1,
                                   "spike_multiplier_min": 2, "spike_multiplier_max": 2}},
                     mix={"power": 1})
        days, _ = generate(config, n=3)
        events = personal_events(days)
        assert events and all(e.date.weekday() < 5 for e in events)
        totals = per_user_day(days)
        for (day, _uid), total in totals.items():
            if day.weekday() >= 5:
                assert total == (500_000 if weekend_probability else 1_000_000)
            else:
                assert total == 2_000_000


def test_inactive_and_forced_zero_days_never_spike():
    zero = {"spike_rules": [], "zero_periods": [
        {"id": "z", "target_type": "team", "target": "team",
         "start_date": "2026-07-07", "end_date": "2026-07-08"}]}
    config = cfg(days=5, zero_probability=0.3, events=zero, profiles={
        "power": {"daily_input_tokens": 1_000_000, "spike_probability": 1,
                  "spike_multiplier_min": 2, "spike_multiplier_max": 2}}, mix={"power": 1})
    days, _ = generate(config, n=40)
    totals = per_user_day(days)
    events = {(e.date, e.user_id) for e in personal_events(days)}
    assert events == set(totals)  # a spike event exists exactly for each active user-day...
    assert set(totals.values()) == {2_000_000}  # ...and every active user-day is spiked
    assert all(date(2026, 7, 7) != d and date(2026, 7, 8) != d for d, _ in events)
    inactive = sum(1 for d in days for _ in range(40)) - len(totals)
    assert inactive > 0  # the 0.3 zero probability and the forced-zero days removed user-days


def test_draw_keys_are_usage_seed_domain_user_date_exactly():
    """Pins the domains and keys: an independent oracle must reproduce every event."""
    seed, p, lo, hi = 91, 0.4, 1.2, 3.0
    config = cfg(days=30, usage_seed=seed, profiles={
        "power": {"daily_input_tokens": 1_000_000, "spike_probability": p,
                  "spike_multiplier_min": lo, "spike_multiplier_max": hi}}, mix={"power": 1})
    days, _ = generate(config, n=6)
    actual = {(e.date, e.user_id): e.multiplier for e in personal_events(days)}
    expected = {}
    users, _team = population(6)
    for offset in range(30):
        day = date.fromisoformat(MONDAY) + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for user in users:
            iso = day.isoformat()
            if rng_for(seed, "profile_spike", user.user_id, iso).random() < p:
                expected[(day, user.user_id)] = rng_for(
                    seed, "profile_spike_multiplier", user.user_id, iso).uniform(lo, hi)
    assert actual == expected and len(actual) > 20


# --- composition ----------------------------------------------------------------------------------

def test_composes_with_volume_jitter_exactly():
    config = cfg(days=10, variation=30, usage_seed=5, profiles={
        "power": {"daily_input_tokens": 1_000_000, "spike_probability": 1,
                  "spike_multiplier_min": 1.5, "spike_multiplier_max": 2.5}}, mix={"power": 1})
    days, _ = generate(config, n=3)
    totals = per_user_day(days)
    checked = 0
    for (day, uid), total in totals.items():
        iso = day.isoformat()
        volume = rng_for(5, "volume", uid, iso).uniform(0.7, 1.3)
        if day.weekday() >= 5:
            assert total == _half_up(Fraction(1_000_000) * Fraction(str(volume)))
        else:
            m = rng_for(5, "profile_spike_multiplier", uid, iso).uniform(1.5, 2.5)
            assert total == _half_up(Fraction(1_000_000) * Fraction(str(volume)) * Fraction(str(m)))
            checked += 1
    assert checked == 3 * 8


def test_composes_with_a_curve_and_with_a_spike_rule_hand_computed():
    rule = {"spike_rules": [{
        "id": "org-day", "probability": 1, "multiplier_min": 2, "multiplier_max": 2,
        "targets": {"teams": [], "agents": [], "models": []}}], "zero_periods": []}
    config = cfg(days=1, events=rule, profiles={
        "power": {"daily_input_tokens": 1_000_000, "spike_probability": 1,
                  "spike_multiplier_min": 2, "spike_multiplier_max": 2}}, mix={"power": 1})
    # no curve: 1,000,000 x 2 (personal) = 2,000,000; the org rule then doubles that baseline
    days, _ = generate(config, n=1)
    (personal,) = personal_events(days)
    (rule_event,) = [e for e in days[0].events if isinstance(e, SpikeEvent)]
    assert (personal.baseline_processed_input, personal.incremental_processed_input) == (
        1_000_000, 1_000_000)
    assert (rule_event.baseline_processed_input, rule_event.incremental_processed_input) == (
        2_000_000, 2_000_000)
    assert sum(processed(r) for r in days[0].usage) == 4_000_000
    # with a curve level of 1.5: 1,000,000 x 1.5 x 2 = 3,000,000, rule doubles it to 6,000,000
    days, _ = generate(config, n=1, curve_levels=CurveLevels(org_levels=[1.5]))
    (personal,) = personal_events(days)
    (rule_event,) = [e for e in days[0].events if isinstance(e, SpikeEvent)]
    assert (personal.baseline_processed_input, personal.incremental_processed_input) == (
        1_500_000, 1_500_000)
    assert rule_event.baseline_processed_input == 3_000_000
    assert sum(processed(r) for r in days[0].usage) == 6_000_000


def test_personal_spike_scales_all_of_the_users_rows_together():
    """Applied before the agent/model split: agent shares of the day are unchanged."""
    spiky = cfg(days=1, profiles={"p": {"daily_input_tokens": 1_000_000, "spike_probability": 1,
                                        "spike_multiplier_min": 2, "spike_multiplier_max": 2}},
                mix={"p": 1})
    plain = cfg(days=1, profiles={"p": {"daily_input_tokens": 1_000_000}}, mix={"p": 1})
    (spiked,), _ = generate(spiky, n=1)
    (base,), _ = generate(plain, n=1)
    assert len(spiked.usage) == len(base.usage) == 4
    for a, b in zip(spiked.usage, base.usage):
        assert (a.agent_type, a.model) == (b.agent_type, b.model)
        assert processed(a) == 2 * processed(b)


def test_multiplier_conversion_is_exact_decimal_not_binary_float():
    """10 x 1.15 is 11.5 exactly, which rounds half up to 12; Fraction(1.15 as a float) is
    11.4999..., which would round to 11."""
    config = cfg(days=1, profiles={"p": {"daily_input_tokens": 10, "spike_probability": 1,
                                         "spike_multiplier_min": 1.15, "spike_multiplier_max": 1.15}},
                 mix={"p": 1})
    days, _ = generate(config, n=3)
    assert set(per_user_day(days).values()) == {12}
    assert [(e.baseline_processed_input, e.incremental_processed_input)
            for e in personal_events(days)] == [(10, 2)] * 3


def test_event_carries_the_users_team_and_profile():
    config = cfg(days=3, profiles={"power": {"daily_input_tokens": 1_000_000, **SPIKE,
                                             "spike_probability": 1}}, mix={"power": 1})
    users, team = population(4, team="team")
    assert team.team_id == "id-team" != team.org_id
    days = list(generate_days(config, users, [team], assign_profiles(config, [team])))
    events = personal_events(days)
    assert len(events) == 4 * 3
    assert {e.team_id for e in events} == {"id-team"}
    assert {e.profile for e in events} == {"power"}


def test_spike_pushing_a_day_past_signed_64_bit_raises_and_never_wraps():
    max_i64 = 2**63 - 1

    def run(tokens, multiplier=2):
        config = cfg(days=1, profiles={"p": {
            "daily_input_tokens": tokens, "spike_probability": 1,
            "spike_multiplier_min": multiplier, "spike_multiplier_max": multiplier}}, mix={"p": 1})
        return generate(config, n=1)[0]

    below = max_i64 // 2  # x2 = max_i64 - 1
    days = run(below)
    assert per_user_day(days) == {(date.fromisoformat(MONDAY), "u000"): below * 2}
    assert personal_events(days)[0].incremental_processed_input == below
    with pytest.raises(InputDataError, match="64-bit"):
        run(2**62)  # 2**62 alone fits; the doubled day is 2**63
    with pytest.raises(InputDataError, match="64-bit"):
        run(below, multiplier=3)
    plain = cfg(days=1, profiles={"p": {"daily_input_tokens": 2**62}}, mix={"p": 1})
    assert per_user_day(generate(plain, n=1)[0]) == {(date.fromisoformat(MONDAY), "u000"): 2**62}


def test_no_event_when_rounding_leaves_the_count_unchanged():
    tiny = cfg(days=3, profiles={"p": {"daily_input_tokens": 1, "spike_probability": 1,
                                       "spike_multiplier_min": 1.2, "spike_multiplier_max": 1.2}},
               mix={"p": 1})
    plain = cfg(days=3, profiles={"p": {"daily_input_tokens": 1}}, mix={"p": 1})
    days, _ = generate(tiny, n=2)
    base, _ = generate(plain, n=2)
    assert not personal_events(days)
    assert usage_dump(days) == usage_dump(base)
    one = cfg(days=3, profiles={"p": {"daily_input_tokens": 1_000, "spike_probability": 1,
                                      "spike_multiplier_min": 1, "spike_multiplier_max": 1}},
              mix={"p": 1})
    days, _ = generate(one, n=2)
    assert not personal_events(days)


# --- knob independence (I2b) ---------------------------------------------------------------------

def _user_rows(days, users):
    return [row.model_dump(mode="json") for d in days for row in d.usage if row.user_id in users]


def test_other_profiles_fields_do_not_move_an_unaffected_users_output():
    base = cfg(days=14, variation=20, zero_probability=.1)
    edited = cfg(days=14, variation=20, zero_probability=.1, profiles={
        "plain": {"daily_input_tokens": 777_000, "spike_probability": .9,
                  "spike_multiplier_min": 4, "spike_multiplier_max": 5},
        "power": SPIKE | {"daily_input_tokens": 1_000_000}})
    a, assignments = generate(base, n=20)
    b, _ = generate(edited, n=20)
    power = users_of(assignments, "power")
    assert _user_rows(a, power) == _user_rows(b, power)
    assert _user_rows(a, power) and personal_events(a)
    assert [e.model_dump() for e in personal_events(a)] == [
        e.model_dump() for e in personal_events(b) if e.profile == "power"]


def test_another_teams_profile_mix_does_not_move_a_users_output():
    profiles = {"plain": {"daily_input_tokens": 500_000},
                "power": {"daily_input_tokens": 1_000_000, **SPIKE}}

    def run(second_mix):
        raw_cfg = raw(days=14, variation=20, zero_probability=.1, profiles=profiles,
                      mix={"plain": .5, "power": .5})
        raw_cfg["team_profiles"]["other"] = {"profile_mix": second_mix,
                                             "project_mix": {"Project": 1}}
        config = SeriesConfig.model_validate(raw_cfg)
        users_a, team_a = population(10, "team", "a")
        users_b, team_b = population(10, "other", "b")
        teams = [team_a, team_b]
        assignments = assign_profiles(config, teams)
        days = list(generate_days(config, users_a + users_b, teams, assignments))
        return days, assignments, {u.user_id for u in users_a}

    days1, assignments1, team_a_users = run({"plain": .5, "power": .5})
    days2, assignments2, _ = run({"plain": .1, "power": .9})
    assert [a for a in assignments1 if a.user_id in team_a_users] == [
        a for a in assignments2 if a.user_id in team_a_users]
    assert _user_rows(days1, team_a_users) == _user_rows(days2, team_a_users)
    assert ({(e.date, e.user_id) for e in personal_events(days1) if e.user_id in team_a_users}
            == {(e.date, e.user_id) for e in personal_events(days2) if e.user_id in team_a_users})


def test_raising_the_probability_only_adds_spike_days_and_max_only_moves_the_value():
    def run(p, high=2.5):
        days, _ = generate(cfg(days=20, profiles={"power": {
            "daily_input_tokens": 1_000_000, "spike_probability": p,
            "spike_multiplier_min": 1.5, "spike_multiplier_max": high}}, mix={"power": 1}), n=15)
        return {(e.date, e.user_id): e.multiplier for e in personal_events(days)}

    low, high = run(0.2), run(0.6)
    assert low and set(low) < set(high)
    assert all(low[k] == high[k] for k in low)  # same draw, same multiplier
    wider = run(0.6, high=4.0)
    assert set(wider) == set(high)
    assert any(wider[k] != high[k] for k in high)


def test_dates_are_part_of_the_key_so_retiming_shifts_spikes_but_overlap_is_stable():
    profiles = {"power": {"daily_input_tokens": 1_000_000, **SPIKE}}
    full, _ = generate(cfg(days=20, start=MONDAY, profiles=profiles, mix={"power": 1}), n=8)
    tail_start = (date.fromisoformat(MONDAY) + timedelta(days=7)).isoformat()
    tail, _ = generate(cfg(days=13, start=tail_start, profiles=profiles, mix={"power": 1}), n=8)
    assert ([e.model_dump() for e in personal_events(full) if e.date >= date.fromisoformat(tail_start)]
            == [e.model_dump() for e in personal_events(tail)])
    head, _ = generate(cfg(days=10, start=MONDAY, profiles=profiles, mix={"power": 1}), n=8)
    assert usage_dump(head) == usage_dump(full[:10])  # extending a series keeps its prefix


# --- events, report, schema -----------------------------------------------------------------------

def test_event_schema_and_discriminator():
    adapter = TypeAdapter(GenerationEvent)
    good = {"date": "2026-07-06", "kind": "personal_spike", "user_id": "u", "team_id": "t",
            "profile": "power", "multiplier": 1.5, "baseline_processed_input": 10,
            "incremental_processed_input": 5}
    event = adapter.validate_python(good)
    assert isinstance(event, PersonalSpikeEvent)
    assert json.loads(event.model_dump_json()) == good
    for field, value in (("multiplier", 0.99), ("baseline_processed_input", 0),
                         ("incremental_processed_input", 0), ("user_id", " "), ("profile", "")):
        with pytest.raises(ValidationError):
            adapter.validate_python({**good, field: value})
    assert isinstance(adapter.validate_python(
        {"date": "2026-07-06", "kind": "forced_zero", "user_id": "u", "team_id": "t",
         "event_id": "e"}), ForcedZeroEvent)


def test_events_are_sorted_by_kind_then_user_alongside_other_kinds():
    mix_events = {"spike_rules": [{
        "id": "org-day", "probability": 1, "multiplier_min": 2, "multiplier_max": 2,
        "targets": {"teams": [], "agents": [], "models": []}}],
        "zero_periods": [{"id": "z", "target_type": "user", "target": "u000",
                          "start_date": MONDAY, "end_date": MONDAY}]}
    config = cfg(days=1, events=mix_events, profiles={"power": {
        "daily_input_tokens": 1_000_000, "spike_probability": 1,
        "spike_multiplier_min": 2, "spike_multiplier_max": 2}}, mix={"power": 1})
    days, _ = generate(config, n=4)
    kinds = [e.kind for e in days[0].events]
    assert kinds == ["forced_zero"] + ["personal_spike"] * 3 + ["spike"]
    assert [e.user_id for e in days[0].events[1:4]] == ["u001", "u002", "u003"]


def _chain_with_spikes(root, monkeypatch, *, profile_fields):
    return _run_chain(root, CHAINS["series_small"], monkeypatch, mutate_profiles=profile_fields)


def test_series_artifacts_record_and_report_personal_spikes(tmp_path, monkeypatch):
    fields = {"spike_probability": 1, "spike_multiplier_min": 2, "spike_multiplier_max": 2}
    _chain_with_spikes(tmp_path, monkeypatch, profile_fields=fields)
    out = tmp_path / "series"
    lines = [json.loads(line) for line in (out / "generation_events.jsonl").read_text().splitlines()]
    assert lines and {line["kind"] for line in lines} == {"personal_spike"}
    manifest = json.loads((out / "manifest.json").read_text())
    summary = manifest["summary"]
    assert summary["personal_spike_events"] == len(lines) == summary["event_rows"]
    assert summary["personal_spike_incremental_processed_input"] == sum(
        line["incremental_processed_input"] for line in lines)
    assert summary["spike_events"] == 0 and summary["spike_days"] == 0
    report = (out / "report.md").read_text()
    assert "### Personal spikes by profile" in report
    by_profile = {}
    for line in lines:
        by_profile.setdefault(line["profile"], []).append(line)
    for profile, rows in sorted(by_profile.items()):
        incremental = sum(r["incremental_processed_input"] for r in rows)
        baseline = sum(r["baseline_processed_input"] for r in rows)
        assert f"| {profile} | {len(rows)} | {baseline} | {incremental} |" in report
    assert "| None | 0 | 0 | 0 |" in report  # rule spikes table keeps its own meaning
    # events are read back by the artifact reader with the record schema
    from forecast.artifact_io import read_records
    parsed = read_records(out / "generation_events.jsonl", PersonalSpikeEvent, schema_version=2)
    assert len(parsed) == len(lines)


def test_personal_spike_report_rows_are_in_sorted_profile_order(tmp_path, monkeypatch):
    """Rows follow sorted profile names (not the config's light, mid, heavy order)."""
    fields = {"spike_probability": 1, "spike_multiplier_min": 2, "spike_multiplier_max": 2}
    _chain_with_spikes(tmp_path, monkeypatch, profile_fields=fields)
    report = (tmp_path / "series/report.md").read_text()
    table = report.split("### Personal spikes by profile")[1].split("###")[0]
    rows = [line.split("|")[1].strip() for line in table.splitlines()
            if line.startswith("| ") and not line.startswith("| Profile")]
    assert rows == ["heavy_profile", "light_profile", "mid_profile"] == sorted(rows)


def test_probability_zero_survives_the_saved_config_round_trip(tmp_path, monkeypatch):
    fields = {"spike_probability": 0, "spike_multiplier_min": 1.5, "spike_multiplier_max": 2.5}
    assert cfg(profiles={"p": {"daily_input_tokens": 1, **fields}}, mix={"p": 1}).model_dump(
        mode="json")["profiles"]["p"]["spike_probability"] == 0
    _chain_with_spikes(tmp_path, monkeypatch, profile_fields=fields)
    saved = yaml.safe_load((tmp_path / "series/config.yaml").read_text())
    assert all(p["spike_probability"] == 0 for p in saved["profiles"].values())
    replay = load_series_config(tmp_path / "series/config.yaml")
    assert all(p.has_personal_spikes and p.spike_probability == 0 for p in replay.profiles.values())


def test_saved_config_replays_to_identical_artifacts(tmp_path, monkeypatch):
    fields = {"spike_probability": .6, "spike_multiplier_min": 1.5, "spike_multiplier_max": 2.5}
    _chain_with_spikes(tmp_path, monkeypatch, profile_fields=fields)
    out = tmp_path / "series"
    replay = load_series_config(out / "config.yaml")
    assert all(p.has_personal_spikes for p in replay.profiles.values())
    run_series(replay, tmp_path / "replay", run_id="s")
    for name in ("usage.jsonl", "coverage.jsonl", "generation_events.jsonl",
                 "user_profiles.jsonl", "config.yaml"):
        assert (out / name).read_bytes() == (tmp_path / "replay" / name).read_bytes()
