"""T8 accept: §6.3 example (40% x 30% -> 12% at org); siblings unchanged; k=1
sub-scopes; token_factor applied; tokens conserved when token_factor=1.0; baseline
scope set == scenario scope set."""

from dataclasses import dataclass
from datetime import date

import numpy as np
import pytest

from common.model_pricing import RateSheet

from forecast.forecasting.dataset import PreparedDataset, ScopeMeta

from forecast.cost.errors import InputError
from forecast.cost.request import ResolvedRequest
from forecast.cost.schemas import ScenarioConfig
from forecast.cost.scenario import apply_scenario

ORG, TEAM1, TEAM2, PROJ1 = "org-a", "team1", "team2", "proj1"
HISTORY_DAY = date(2026, 9, 30)
DATES = (date(2026, 10, 1), date(2026, 10, 2))
SAMPLES = 2


class _FakeSavedRun:
    """Just enough of `forecasting.SavedRun` for `apply_scenario`: `.index`,
    `.origin`, `.load_paths`. Avoids a real forecast run's file I/O for a
    scenario-mechanics-only test."""

    def __init__(self, index, origin, arrays):
        self.index = index
        self.origin = origin
        self._arrays = arrays

    def load_paths(self, entry):
        return self._arrays[(entry["scope_type"], entry["scope_id"], entry["model"])]


def _series_entry(scope_type, scope_id, scope_name, model, status="qualified"):
    return {
        "org_id": ORG, "scope_type": scope_type, "scope_id": scope_id,
        "scope_name": scope_name, "model": model, "status": status,
        "path": f"paths/{scope_type}-{scope_id}__{model}.npy", "shape": [len(DATES), SAMPLES],
        "dtype": "float64", "assumptions": [], "reasons": [],
    }


def _scope_entry(scope_type, scope_id, scope_name):
    return {
        "org_id": ORG, "scope_type": scope_type, "scope_id": scope_id,
        "scope_name": scope_name, "status": "complete", "risk_ready": True,
        "assumptions": [], "reasons": [],
    }


def _constant(value: float) -> np.ndarray:
    return np.full((len(DATES), SAMPLES), value, dtype=np.float64)


def _model_transfer_fixture(*, agent_split=False):
    """team1 = 40% of org's Opus (attribution example); proj1 is team1's only
    project; team2 is an unrelated sibling. `opus` is used by `claude` only,
    `sonnet` has zero historical usage anywhere (exercises the borrow fallback) --
    unless `agent_split`, which instead gives `sonnet` real `codex` usage at org
    level, for the agent_transfer test."""
    keys = frozenset([("u1", "claude"), ("u1", "codex")])
    scopes = {
        (ORG, "organization", ORG): ScopeMeta(ORG, "organization", ORG, "Org A", keys),
        (ORG, "team", TEAM1): ScopeMeta(ORG, "team", TEAM1, "Team1", keys),
        (ORG, "project", PROJ1): ScopeMeta(ORG, "project", PROJ1, "Proj1", frozenset([("u1", "claude")])),
    }
    models = {ORG: {"opus": frozenset(["claude"]), "sonnet": frozenset(["codex"])}}
    usage = {
        (ORG, "organization", ORG, "opus", HISTORY_DAY, "claude", "output"): 1000.0,
        (ORG, "team", TEAM1, "opus", HISTORY_DAY, "claude", "output"): 400.0,
        (ORG, "project", PROJ1, "opus", HISTORY_DAY, "claude", "output"): 150.0,
    }
    if agent_split:
        usage[(ORG, "organization", ORG, "sonnet", HISTORY_DAY, "codex", "output")] = 500.0
    coverage = {
        (HISTORY_DAY, ORG, "u1", "claude"): "complete",
        (HISTORY_DAY, ORG, "u1", "codex"): "complete",
    }
    dataset = PreparedDataset(
        start=date(2026, 1, 1), end=date(2026, 12, 31), timezone="UTC",
        models=models, scopes=scopes, usage=usage, coverage=coverage,
        startup=[], hashes={}, source_state={},
    )

    index = {
        "schema_version": 1, "dates": [d.isoformat() for d in DATES],
        "sample_ids": list(range(SAMPLES)),
        "scopes": [
            _scope_entry("organization", ORG, "Org A"),
            _scope_entry("team", TEAM1, "Team1"),
            _scope_entry("project", PROJ1, "Proj1"),
            _scope_entry("team", TEAM2, "Team2"),
        ],
        "series": [
            _series_entry("organization", ORG, "Org A", "opus"),
            _series_entry("team", TEAM1, "Team1", "opus"),
            _series_entry("project", PROJ1, "Proj1", "opus"),
            _series_entry("team", TEAM2, "Team2", "opus"),
        ],
    }
    arrays = {
        ("organization", ORG, "opus"): _constant(1000.0),
        ("team", TEAM1, "opus"): _constant(400.0),
        ("project", PROJ1, "opus"): _constant(150.0),
        ("team", TEAM2, "opus"): _constant(600.0),
    }
    saved = _FakeSavedRun(index, HISTORY_DAY, arrays)
    return saved, dataset


def _resolved(saved, dataset, scenario) -> ResolvedRequest:
    # apply_scenario never reads rate_sheet (only pricing does, kernel.py) -- an
    # empty sheet is enough here.
    empty_sheet = RateSheet(org_id=ORG, start=DATES[0], end=DATES[-1], price_version="test", models=[])
    return ResolvedRequest(
        forecast_run=saved, dataset=dataset, org_id=ORG, dates=DATES, samples=SAMPLES,
        periods=(), history_lookback_days=1, scenario=scenario, scenario_name=scenario.name,
        rate_sheet=empty_sheet,
    )


def _by_key(series_inputs):
    return {(s.scope_type, s.scope_id, s.model): s for s in series_inputs}


def _model_transfer_config(fraction=0.3, token_factor=1.0):
    return ScenarioConfig.model_validate(
        {
            "schema": "scenario/1.0",
            "name": "team1-opus-to-sonnet",
            "scope": {"type": "team", "name": "Team1"},
            "changes": [
                {
                    "model_transfer": {
                        "from": "opus", "to": "sonnet", "fraction": fraction,
                        "token_factor": token_factor,
                    }
                }
            ],
        }
    )


def test_design_example_40_percent_times_30_percent_equals_12_percent_at_org():
    saved, dataset = _model_transfer_fixture()
    resolved = _resolved(saved, dataset, _model_transfer_config(fraction=0.3))

    result = _by_key(apply_scenario(resolved))

    # team1 (declared, k=1): moves 30% of its own Opus.
    np.testing.assert_allclose(result[("team", TEAM1, "opus")].tokens, 400.0 * 0.7)
    np.testing.assert_allclose(result[("team", TEAM1, "sonnet")].tokens, 400.0 * 0.3)
    assert "scenario_added_series" in result[("team", TEAM1, "sonnet")].assumptions
    assert "scenario_constant_tokens" in result[("team", TEAM1, "opus")].assumptions

    # proj1 (team1's sub-scope, k=1): moves 30% of its own Opus.
    np.testing.assert_allclose(result[("project", PROJ1, "opus")].tokens, 150.0 * 0.7)
    np.testing.assert_allclose(result[("project", PROJ1, "sonnet")].tokens, 150.0 * 0.3)

    # organization (parent, k = attribution share = 0.4): moves 0.4*30% = 12%.
    np.testing.assert_allclose(result[("organization", ORG, "opus")].tokens, 1000.0 * 0.88)
    np.testing.assert_allclose(result[("organization", ORG, "sonnet")].tokens, 1000.0 * 0.12)
    assert "scenario_propagated_by_share" in result[("organization", ORG, "opus")].assumptions
    assert "scenario_propagated_by_share" in result[("organization", ORG, "sonnet")].assumptions

    # team2 (sibling): entirely unchanged.
    np.testing.assert_allclose(result[("team", TEAM2, "opus")].tokens, 600.0)
    assert result[("team", TEAM2, "opus")].assumptions == ()

    # Tokens conserved (token_factor = 1.0): each affected scope's total is the same
    # before and after (moved out of `opus` exactly equals moved into `sonnet`).
    for scope_type, scope_id, opus_total in (
        ("team", TEAM1, 400.0), ("project", PROJ1, 150.0), ("organization", ORG, 1000.0),
    ):
        total_after = result[(scope_type, scope_id, "opus")].tokens + result[(scope_type, scope_id, "sonnet")].tokens
        np.testing.assert_allclose(total_after, opus_total)

    # Baseline and scenario runs have the same scope set (§6.3).
    baseline_scopes = {(s["scope_type"], s["scope_id"]) for s in saved.index["scopes"]}
    scenario_scopes = {(s.scope_type, s.scope_id) for s in apply_scenario(resolved)}
    assert scenario_scopes == baseline_scopes


def test_token_factor_scales_destination_and_breaks_conservation():
    saved, dataset = _model_transfer_fixture()
    resolved = _resolved(saved, dataset, _model_transfer_config(fraction=0.3, token_factor=2.0))

    result = _by_key(apply_scenario(resolved))

    moved = 400.0 * 0.3
    np.testing.assert_allclose(result[("team", TEAM1, "opus")].tokens, 400.0 - moved)
    np.testing.assert_allclose(result[("team", TEAM1, "sonnet")].tokens, 2.0 * moved)
    assert "scenario_token_factor_manual" in result[("team", TEAM1, "sonnet")].assumptions
    total_after = result[("team", TEAM1, "opus")].tokens + result[("team", TEAM1, "sonnet")].tokens
    assert not np.allclose(total_after, 400.0)


def test_growth_propagates_by_attribution_share():
    saved, dataset = _model_transfer_fixture()
    scenario = ScenarioConfig.model_validate(
        {
            "schema": "scenario/1.0",
            "name": "team1-growth",
            "scope": {"type": "team", "name": "Team1"},
            "changes": [{"growth": {"factor": 1.2}}],
        }
    )
    resolved = _resolved(saved, dataset, scenario)

    result = _by_key(apply_scenario(resolved))

    np.testing.assert_allclose(result[("team", TEAM1, "opus")].tokens, 400.0 * 1.2)
    np.testing.assert_allclose(result[("project", PROJ1, "opus")].tokens, 150.0 * 1.2)
    # organization: 1 + (1.2-1)*0.4 = 1.08
    np.testing.assert_allclose(result[("organization", ORG, "opus")].tokens, 1000.0 * 1.08)
    np.testing.assert_allclose(result[("team", TEAM2, "opus")].tokens, 600.0)


def test_agent_transfer_moves_between_models_via_org_level_split():
    saved, dataset = _model_transfer_fixture(agent_split=True)
    scenario = ScenarioConfig.model_validate(
        {
            "schema": "scenario/1.0",
            "name": "claude-to-codex",
            "scope": {"type": "team", "name": "Team1"},
            "changes": [
                {"agent_transfer": {"from": "claude", "to": "codex", "fraction": 0.5}}
            ],
        }
    )
    resolved = _resolved(saved, dataset, scenario)

    result = _by_key(apply_scenario(resolved))

    # team1: 100% of opus is claude's -> moved = 1.0*0.5*400 = 200; codex's only
    # model is sonnet -> all 200 lands there.
    np.testing.assert_allclose(result[("team", TEAM1, "opus")].tokens, 200.0)
    np.testing.assert_allclose(result[("team", TEAM1, "sonnet")].tokens, 200.0)
    assert "scenario_added_series" in result[("team", TEAM1, "sonnet")].assumptions

    # organization: k = attribution share of opus (team1/org = 0.4) -> moved =
    # 0.4*0.5*1000 = 200. There is no pre-existing org/sonnet *forecast* series
    # (only historical dataset usage, used for the mix/split math above) -> the
    # moved amount is the whole result.
    np.testing.assert_allclose(result[("organization", ORG, "opus")].tokens, 800.0)
    np.testing.assert_allclose(result[("organization", ORG, "sonnet")].tokens, 200.0)


def test_declared_project_scope_requires_a_uniquely_determined_team():
    """Fix 2: a project's team is genuinely needed for scenario propagation (the
    declared scope's own ancestors) -- an undetermined team must raise naming the
    project, never silently drop the team level."""
    saved, dataset = _model_transfer_fixture()
    # proj1's population no longer overlaps team1's at all -> zero candidate teams.
    dataset.scopes[(ORG, "project", PROJ1)] = ScopeMeta(
        ORG, "project", PROJ1, "Proj1", frozenset([("u-nobody", "claude")])
    )
    scenario = ScenarioConfig.model_validate(
        {
            "schema": "scenario/1.0",
            "name": "proj1-growth",
            "scope": {"type": "project", "name": "Proj1"},
            "changes": [{"growth": {"factor": 1.1}}],
        }
    )
    resolved = _resolved(saved, dataset, scenario)
    with pytest.raises(InputError, match="Proj1|proj1"):
        apply_scenario(resolved)


def test_ambiguous_project_sub_scope_raises_during_team_declared_scenario():
    """Fix 2: a candidate sub-scope project matching more than one team (one of
    them the declared team) must raise, not be silently included or excluded."""
    saved, dataset = _model_transfer_fixture()
    # A second team whose population also covers proj1's users -> proj1 now
    # matches both team1 and team2 -> ambiguous.
    dataset.scopes[(ORG, "team", TEAM2)] = ScopeMeta(
        ORG, "team", TEAM2, "Team2", frozenset([("u1", "claude"), ("u1", "codex")])
    )
    resolved = _resolved(saved, dataset, _model_transfer_config(fraction=0.3))
    with pytest.raises(InputError, match=PROJ1):
        apply_scenario(resolved)
