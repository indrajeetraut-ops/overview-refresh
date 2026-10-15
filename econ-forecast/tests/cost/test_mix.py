"""T5 accept: §5 estimator (4 days, 10%/3% -> 5.5%), ratio-of-sums vs mean-of-ratios,
outage-day exclusion, and the Organization having no parent."""

from datetime import date, timedelta

import pytest

from forecast.forecasting.dataset import PreparedDataset, ScopeMeta

from forecast.cost.errors import InputError
from forecast.cost.schemas import TOKEN_TYPES
from forecast.cost.mix import (
    ancestors_of,
    attribution_share,
    compute_mix,
    required_team_of_project,
    team_of_project,
)


def _dataset(*, scopes, models, usage, coverage) -> PreparedDataset:
    return PreparedDataset(
        start=date(2026, 1, 1),
        end=date(2026, 12, 31),
        timezone="UTC",
        models=models,
        scopes=scopes,
        usage=usage,
        coverage=coverage,
        startup=[],
        hashes={},
        source_state={},
    )


def _team_and_org_scopes(org_id="org-a", team_id="team1", users=("u1", "u2"), agent="agent-a"):
    keys = frozenset((u, agent) for u in users)
    return {
        (org_id, "team", team_id): ScopeMeta(org_id, "team", team_id, "Team A", keys),
        (org_id, "organization", org_id): ScopeMeta(org_id, "organization", org_id, "Org A", keys),
    }


def test_design_example_4_active_days_10_percent_3_percent_blends_to_5_5_percent():
    org_id, team_id, model, agent = "org-a", "team1", "model-a", "agent-a"
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(4)]
    origin = days[-1]
    scopes = _team_and_org_scopes(org_id, team_id)
    models = {org_id: {model: frozenset([agent])}}
    usage = {}
    coverage = {}
    for day in days:
        usage[(org_id, "team", team_id, model, day, agent, "output")] = 100.0
        usage[(org_id, "team", team_id, model, day, agent, "input")] = 900.0
        usage[(org_id, "organization", org_id, model, day, agent, "output")] = 300.0
        usage[(org_id, "organization", org_id, model, day, agent, "input")] = 9700.0
        for user in ("u1", "u2"):
            coverage[(day, org_id, user, agent)] = "complete"
    dataset = _dataset(scopes=scopes, models=models, usage=usage, coverage=coverage)

    result = compute_mix(
        dataset, org_id, "team", team_id, model, origin, max_age_days=30,
        parent=("organization", org_id),
    )

    assert result.active_days == 4
    assert result.own_weight == pytest.approx(4 / 11)
    shares = {row["agent_type"]: row for row in result.shares}
    assert shares[agent]["output"] == pytest.approx(0.0554545454545, abs=1e-9)
    assert shares[agent]["input"] == pytest.approx(0.9445454545454, abs=1e-9)
    assert shares[agent]["cache_read"] == 0.0
    total = sum(shares[agent][tt] for tt in TOKEN_TYPES)
    assert total == pytest.approx(1.0)


def test_ratio_of_weighted_sums_not_mean_of_daily_ratios():
    org_id, scope_id, model, agent = "org-a", "org-a", "model-a", "agent-a"
    day1, day2 = date(2026, 9, 1), date(2026, 9, 2)
    scopes = {
        (org_id, "organization", org_id): ScopeMeta(
            org_id, "organization", org_id, "Org A", frozenset([("u1", agent)])
        )
    }
    models = {org_id: {model: frozenset([agent])}}
    usage = {
        (org_id, "organization", org_id, model, day1, agent, "output"): 50.0,
        (org_id, "organization", org_id, model, day1, agent, "input"): 50.0,
        (org_id, "organization", org_id, model, day2, agent, "output"): 90.0,
        (org_id, "organization", org_id, model, day2, agent, "input"): 810.0,
    }
    coverage = {
        (day1, org_id, "u1", agent): "complete",
        (day2, org_id, "u1", agent): "complete",
    }
    dataset = _dataset(scopes=scopes, models=models, usage=usage, coverage=coverage)

    # Huge half-life -> day weights ~equal -> ratio of sums, not mean of ratios.
    result = compute_mix(
        dataset, org_id, "organization", org_id, model, day2, max_age_days=30,
        half_life_days=10_000_000,
    )

    mean_of_ratios = (0.5 + 0.1) / 2
    ratio_of_sums = (50.0 + 90.0) / (100.0 + 900.0)
    shares = {row["agent_type"]: row for row in result.shares}
    assert shares[agent]["output"] == pytest.approx(ratio_of_sums, abs=1e-6)
    assert shares[agent]["output"] != pytest.approx(mean_of_ratios, abs=1e-3)


def test_outage_day_is_excluded_from_both_sums():
    org_id, team_id, model, agent = "org-a", "team1", "model-a", "agent-a"
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(4)]
    outage_day = days[-1] + timedelta(days=1)
    origin = outage_day
    scopes = _team_and_org_scopes(org_id, team_id)
    models = {org_id: {model: frozenset([agent])}}
    usage = {}
    coverage = {}
    for day in days:
        usage[(org_id, "team", team_id, model, day, agent, "output")] = 100.0
        usage[(org_id, "team", team_id, model, day, agent, "input")] = 900.0
        usage[(org_id, "organization", org_id, model, day, agent, "output")] = 300.0
        usage[(org_id, "organization", org_id, model, day, agent, "input")] = 9700.0
        for user in ("u1", "u2"):
            coverage[(day, org_id, user, agent)] = "complete"
    # Outage day: usage recorded but coverage is not "complete" -> must not count.
    usage[(org_id, "team", team_id, model, outage_day, agent, "output")] = 10_000.0
    coverage[(outage_day, org_id, "u1", agent)] = "partial"
    coverage[(outage_day, org_id, "u2", agent)] = "complete"
    dataset = _dataset(scopes=scopes, models=models, usage=usage, coverage=coverage)

    result = compute_mix(
        dataset, org_id, "team", team_id, model, origin, max_age_days=30,
        parent=("organization", org_id),
    )

    assert result.active_days == 4
    assert result.window_end == days[-1]
    shares = {row["agent_type"]: row for row in result.shares}
    assert shares[agent]["output"] == pytest.approx(0.0554545454545, abs=1e-9)


def test_organization_has_no_parent_and_uses_its_own_mix():
    org_id, model, agent = "org-a", "model-a", "agent-a"
    day = date(2026, 9, 1)
    scopes = {
        (org_id, "organization", org_id): ScopeMeta(
            org_id, "organization", org_id, "Org A", frozenset([("u1", agent)])
        )
    }
    models = {org_id: {model: frozenset([agent])}}
    usage = {
        (org_id, "organization", org_id, model, day, agent, "output"): 30.0,
        (org_id, "organization", org_id, model, day, agent, "input"): 970.0,
    }
    coverage = {(day, org_id, "u1", agent): "complete"}
    dataset = _dataset(scopes=scopes, models=models, usage=usage, coverage=coverage)

    result = compute_mix(dataset, org_id, "organization", org_id, model, day, max_age_days=30)

    assert result.own_weight == 1.0
    shares = {row["agent_type"]: row for row in result.shares}
    assert shares[agent]["output"] == pytest.approx(0.03)


def test_attribution_share_matches_design_example():
    org_id, team_id, model, agent = "org-a", "team1", "opus", "agent-a"
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(2)]
    origin = days[-1]
    scopes = _team_and_org_scopes(org_id, team_id)
    models = {org_id: {model: frozenset([agent])}}
    usage = {}
    coverage = {}
    for day in days:
        usage[(org_id, "team", team_id, model, day, agent, "output")] = 400.0
        usage[(org_id, "organization", org_id, model, day, agent, "output")] = 1000.0
        for user in ("u1", "u2"):
            coverage[(day, org_id, user, agent)] = "complete"
    dataset = _dataset(scopes=scopes, models=models, usage=usage, coverage=coverage)

    share = attribution_share(
        dataset, org_id, "team", team_id, "organization", org_id, model, origin, max_age_days=30
    )
    assert share == pytest.approx(0.4)


def test_attribution_share_is_zero_when_parent_denominator_is_zero():
    org_id, team_id, model, agent = "org-a", "team1", "opus", "agent-a"
    day = date(2026, 9, 1)
    scopes = _team_and_org_scopes(org_id, team_id)
    models = {org_id: {model: frozenset([agent])}}
    usage = {}
    coverage = {(day, org_id, "u1", agent): "complete", (day, org_id, "u2", agent): "complete"}
    dataset = _dataset(scopes=scopes, models=models, usage=usage, coverage=coverage)

    share = attribution_share(
        dataset, org_id, "team", team_id, "organization", org_id, model, day, max_age_days=30
    )
    assert share == 0.0


def _org_team_project_scopes(org_id="org-a"):
    team_keys = frozenset([("u1", "agent-a"), ("u2", "agent-a")])
    project_keys = frozenset([("u1", "agent-a")])
    return {
        (org_id, "organization", org_id): ScopeMeta(org_id, "organization", org_id, "Org A", team_keys),
        (org_id, "team", "team1"): ScopeMeta(org_id, "team", "team1", "Team 1", team_keys),
        (org_id, "project", "proj1"): ScopeMeta(org_id, "project", "proj1", "Proj 1", project_keys),
    }


def test_team_of_project_finds_the_unique_superset_team():
    scopes = _org_team_project_scopes()
    dataset = PreparedDataset(
        start=date(2026, 1, 1), end=date(2026, 12, 31), timezone="UTC",
        models={}, scopes=scopes, usage={}, coverage={}, startup=[], hashes={}, source_state={},
    )
    assert team_of_project(dataset, "org-a", "proj1") == "team1"
    assert required_team_of_project(dataset, "org-a", "proj1") == "team1"
    assert ancestors_of(dataset, "org-a", "project", "proj1") == [
        ("team", "team1"), ("organization", "org-a"),
    ]
    assert ancestors_of(dataset, "org-a", "project", "proj1", strict=True) == [
        ("team", "team1"), ("organization", "org-a"),
    ]


def test_team_of_project_is_none_when_undetermined_non_strict_omits_it():
    scopes = dict(_org_team_project_scopes())
    # A second team also covers proj1's users -> ambiguous.
    scopes[("org-a", "team", "team2")] = ScopeMeta(
        "org-a", "team", "team2", "Team 2", frozenset([("u1", "agent-a")])
    )
    dataset = PreparedDataset(
        start=date(2026, 1, 1), end=date(2026, 12, 31), timezone="UTC",
        models={}, scopes=scopes, usage={}, coverage={}, startup=[], hashes={}, source_state={},
    )
    assert team_of_project(dataset, "org-a", "proj1") is None
    # Fix 2: mix.json's attribution (non-strict) omits the team key rather than raising.
    assert ancestors_of(dataset, "org-a", "project", "proj1") == [("organization", "org-a")]


def test_required_team_of_project_raises_naming_the_project_when_undetermined():
    scopes = dict(_org_team_project_scopes())
    scopes[("org-a", "team", "team2")] = ScopeMeta(
        "org-a", "team", "team2", "Team 2", frozenset([("u1", "agent-a")])
    )
    dataset = PreparedDataset(
        start=date(2026, 1, 1), end=date(2026, 12, 31), timezone="UTC",
        models={}, scopes=scopes, usage={}, coverage={}, startup=[], hashes={}, source_state={},
    )
    with pytest.raises(InputError, match="proj1"):
        required_team_of_project(dataset, "org-a", "proj1")
    # Fix 2: scenario propagation (strict) raises instead of silently dropping the
    # team level.
    with pytest.raises(InputError, match="proj1"):
        ancestors_of(dataset, "org-a", "project", "proj1", strict=True)
