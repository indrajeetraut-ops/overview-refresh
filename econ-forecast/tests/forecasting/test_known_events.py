"""Direct oracles for the dated known-event overlay (design doc §7): scope precedence,
project->team fallback, agent-axis composition, no-washout coherence, spread fan, and
dataset/horizon validation. Mirrors the SimpleNamespace-based "direct oracle" style used
in test_core_numerics.py.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from forecast.forecasting.config import KnownEvent, KnownEvents
from forecast.forecasting.dataset import ScopeMeta
from forecast.forecasting.errors import InputError
from forecast.forecasting.known_events import (
    _agent_for_model,
    _draw_factor,
    _scope_event,
    apply_known_events,
    resolve_team_for_project,
    validate_known_events,
)


def _event(start="2026-09-05", end="2026-09-10", multiplier=1.4, spread=0.15):
    return KnownEvent(
        start_date=date.fromisoformat(start),
        end_date=date.fromisoformat(end),
        multiplier=multiplier,
        spread=spread,
    )


def _project(keys, org_id="org-a", name="Project P", scope_id="p1"):
    return ScopeMeta(org_id, "project", scope_id, name, frozenset(keys))


def _team(name, scope_id, org_id="org-a"):
    return ScopeMeta(org_id, "team", scope_id, name, frozenset())


def _org(org_id="org-a"):
    return ScopeMeta(org_id, "organization", org_id, org_id, frozenset())


# --- project -> team resolution (decision 2) --------------------------------------


def test_project_resolves_to_its_single_team():
    project = _project([("u1", "agent-a"), ("u2", "agent-a")])
    team_by_user = {("org-a", "u1"): "Team A", ("org-a", "u2"): "Team A"}
    assert resolve_team_for_project(project, team_by_user) == "Team A"


def test_project_spanning_two_teams_skips_the_middle_step():
    """Current synthetic data can violate one-team-per-project; skip to org (decision 2)."""
    project = _project([("u1", "agent-a"), ("u2", "agent-a")])
    team_by_user = {("org-a", "u1"): "Team A", ("org-a", "u2"): "Team B"}
    assert resolve_team_for_project(project, team_by_user) is None


def test_unattributed_null_user_does_not_count_toward_team_resolution():
    project = _project([(None, "agent-a"), ("u1", "agent-a")])
    team_by_user = {("org-a", "u1"): "Team A"}
    assert resolve_team_for_project(project, team_by_user) == "Team A"


# --- scope precedence (design §7 table) --------------------------------------------


def test_team_series_uses_its_own_team_event_not_org():
    known = KnownEvents(org=_event(multiplier=1.1), teams={"Team A": _event(multiplier=2.0)})
    match = _scope_event(known, _team("Team A", "team-a"), {})
    assert match == ("team", "Team A", known.teams["Team A"])


def test_project_prefers_its_own_event_over_team_fallback():
    known = KnownEvents(
        teams={"Team A": _event(multiplier=2.0)},
        projects={"Project P": _event(multiplier=3.0)},
    )
    project = _project([("u1", "agent-a")], name="Project P")
    match = _scope_event(known, project, {("org-a", "u1"): "Team A"})
    assert match == ("project", "Project P", known.projects["Project P"])


def test_project_falls_back_to_its_unambiguous_team():
    known = KnownEvents(teams={"Team A": _event(multiplier=2.0)})
    project = _project([("u1", "agent-a")], name="Project P")
    match = _scope_event(known, project, {("org-a", "u1"): "Team A"})
    assert match == ("team", "Team A", known.teams["Team A"])


def test_project_with_ambiguous_team_falls_back_to_org():
    known = KnownEvents(
        org=_event(multiplier=1.5), teams={"Team A": _event(multiplier=2.0)}
    )
    project = _project([("u1", "agent-a"), ("u2", "agent-a")], name="Project P")
    team_by_user = {("org-a", "u1"): "Team A", ("org-a", "u2"): "Team B"}
    match = _scope_event(known, project, team_by_user)
    assert match == ("org", "org", known.org)


def test_team_series_with_no_declared_team_event_falls_back_to_org():
    known = KnownEvents(org=_event(multiplier=1.5))
    match = _scope_event(known, _team("Team B", "team-b"), {})
    assert match == ("org", "org", known.org)


def test_organization_series_only_ever_uses_the_org_event():
    known = KnownEvents(
        org=_event(multiplier=1.5), teams={"Team A": _event(multiplier=2.0)}
    )
    match = _scope_event(known, _org(), {})
    assert match == ("org", "org", known.org)


def test_no_declared_events_match_anything():
    assert _scope_event(KnownEvents(), _org(), {}) is None
    assert _scope_event(KnownEvents(), _team("Team A", "team-a"), {}) is None


# --- agent resolution (skip-on-ambiguity, mirrors decision 2's posture) ------------


def test_agent_for_model_resolves_unique_agent():
    data = SimpleNamespace(models={"org-a": {"model-a": frozenset({"agent-a"})}})
    assert _agent_for_model(data, "org-a", "model-a") == "agent-a"


def test_agent_for_model_is_none_when_dataset_leaves_it_ambiguous():
    data = SimpleNamespace(
        models={"org-a": {"model-x": frozenset({"agent-a", "agent-b"})}}
    )
    assert _agent_for_model(data, "org-a", "model-x") is None


# --- per-path factor: determinism, spread=0 vs spread>0 ----------------------------


def test_zero_spread_is_deterministic_full_multiplier():
    factor = _draw_factor(42, ("known-event-scope", "org", "org"), _event(spread=0.0), 500)
    assert np.array_equal(factor, np.full(500, _event(spread=0.0).multiplier))


def test_positive_spread_fans_within_the_declared_band():
    event = _event(multiplier=1.4, spread=0.15)
    factor = _draw_factor(42, ("known-event-scope", "org", "org"), event, 5000)
    assert factor.min() >= 1.25 and factor.max() <= 1.55
    assert len(np.unique(factor)) > 1000  # genuinely fanned, not collapsed


def test_same_seed_and_key_reproduce_identical_factor():
    event = _event()
    a = _draw_factor(7, ("known-event-scope", "team", "Team A"), event, 200)
    b = _draw_factor(7, ("known-event-scope", "team", "Team A"), event, 200)
    np.testing.assert_array_equal(a, b)


def test_different_keys_draw_independently():
    event = _event()
    a = _draw_factor(7, ("known-event-scope", "team", "Team A"), event, 200)
    b = _draw_factor(7, ("known-event-agent", "agent-a"), event, 200)
    assert not np.array_equal(a, b)


# --- apply_known_events: no-washout coherence + composition ------------------------


def _apply_item(model="model-a", org_id="org-a", scope_type="organization", scope_id="org-a",
                 base=100.0, days=14, paths=2000, status="qualified"):
    scope = SimpleNamespace(org_id=org_id, scope_type=scope_type, scope_id=scope_id, name=scope_id)
    history = SimpleNamespace(scope=scope, model=model)
    item = {
        "history": history,
        "status": status,
        "paths": np.full((days, paths), base, dtype=np.float64),
    }
    return item, scope


def _dates(days=14, start="2026-09-01"):
    first = date.fromisoformat(start)
    return [first + timedelta(days=i) for i in range(days)]


def test_scope_overlay_is_coherent_per_path_not_washed_out():
    """A per-path draw applied to every masked day must move mean AND band, and every
    masked day for a given path must carry the exact same factor (the anti-washout
    contract) - independently recomputed here via the same public seeding path.
    """
    days = _dates(14)
    item, scope = _apply_item(days=14, paths=2000)
    event = _event(start="2026-09-01", end="2026-09-14", multiplier=1.4, spread=0.15)
    known = KnownEvents(org=event)
    applied = apply_known_events([item], scope, SimpleNamespace(models={}), known, {}, days, seed=42)

    expected_factor = _draw_factor(42, ("known-event-scope", "org", "org"), event, 2000)
    expected = np.broadcast_to(100.0 * expected_factor, item["paths"].shape)
    np.testing.assert_allclose(item["paths"], expected)
    # Anti-washout: every day within the window shares the same per-path ratio.
    ratios = item["paths"] / 100.0
    for row in ratios[1:]:
        np.testing.assert_array_equal(row, ratios[0])
    # Mean is lifted...
    assert item["paths"].mean() == pytest.approx(100.0 * 1.4, rel=0.02)
    # ...and the band is fanned (nonzero spread across paths), not collapsed to a point.
    assert item["paths"][0].std() > 0
    assert len(applied) == 1 and applied[0]["level"] == "org"
    assert item["known_event_applied"] is True


def test_deterministic_spread_zero_shifts_the_band_without_fanning_it():
    days = _dates(5)
    item, scope = _apply_item(days=5, paths=500)
    event = _event(start="2026-09-01", end="2026-09-05", multiplier=1.4, spread=0.0)
    known = KnownEvents(org=event)
    apply_known_events([item], scope, SimpleNamespace(models={}), known, {}, days, seed=1)
    np.testing.assert_allclose(item["paths"], 140.0)
    assert item["paths"][0].std() == 0.0


def test_zero_inactive_paths_stay_zero_under_any_multiplier():
    days = _dates(5)
    item, scope = _apply_item(days=5, paths=50, base=0.0, status="inactive")
    event = _event(start="2026-09-01", end="2026-09-05", multiplier=2.5, spread=0.0)
    known = KnownEvents(org=event)
    apply_known_events([item], scope, SimpleNamespace(models={}), known, {}, days, seed=1)
    assert np.all(item["paths"] == 0.0)
    assert "known_event_applied" not in item  # not tagged: inactive paths never change


def test_window_outside_event_dates_is_left_unmodified():
    days = _dates(10, start="2026-09-01")
    item, scope = _apply_item(days=10, paths=50)
    event = _event(start="2026-09-08", end="2026-09-09", multiplier=3.0, spread=0.0)
    known = KnownEvents(org=event)
    apply_known_events([item], scope, SimpleNamespace(models={}), known, {}, days, seed=1)
    np.testing.assert_array_equal(item["paths"][0], np.full(50, 100.0))  # before window
    np.testing.assert_array_equal(item["paths"][7], np.full(50, 300.0))  # 09-08, in window
    np.testing.assert_array_equal(item["paths"][9], np.full(50, 100.0))  # after window


def test_agent_axis_composes_multiplicatively_with_scope_and_only_touches_its_agent():
    days = _dates(6)
    data = SimpleNamespace(
        models={"org-a": {"model-a": frozenset({"agent-a"}), "model-b": frozenset({"agent-b"})}}
    )
    item_a, scope = _apply_item(model="model-a", days=6, paths=1000)
    item_b, _ = _apply_item(model="model-b", days=6, paths=1000)
    scope_event = _event(start="2026-09-01", end="2026-09-06", multiplier=1.4, spread=0.0)
    agent_event = _event(start="2026-09-01", end="2026-09-06", multiplier=2.0, spread=0.0)
    known = KnownEvents(org=scope_event, agents={"agent-a": agent_event})
    applied = apply_known_events(
        [item_a, item_b], scope, data, known, {}, days, seed=9
    )
    np.testing.assert_allclose(item_a["paths"], 100.0 * 1.4 * 2.0)  # scope x agent-a
    np.testing.assert_allclose(item_b["paths"], 100.0 * 1.4)  # scope only, no agent-b event
    levels = {(e["level"], e["name"]) for e in applied}
    assert levels == {("org", "org"), ("agent", "agent-a")}
    # The example from design §7: mix 60/40 claude/codex with org x1.4, claude x2.0 ->
    # claude x2.8, codex x1.4. Here (equal 50/50 weight) the aggregate lands halfway.
    aggregate = item_a["paths"] + item_b["paths"]
    np.testing.assert_allclose(aggregate, 100.0 * 1.4 * 2.0 + 100.0 * 1.4)


def test_no_declared_events_is_a_no_op_and_returns_no_provenance():
    days = _dates(3)
    item, scope = _apply_item(days=3, paths=10)
    original = item["paths"].copy()
    applied = apply_known_events([item], scope, SimpleNamespace(models={}), KnownEvents(), {}, days, seed=1)
    np.testing.assert_array_equal(item["paths"], original)
    assert applied == []
    assert "known_event_applied" not in item


# --- dataset/horizon validation (decision 3) ---------------------------------------


def _dataset(org_id="org-a"):
    scopes = {
        (org_id, "organization", org_id): _org(org_id),
        (org_id, "team", "team-a"): _team("Team A", "team-a", org_id),
        (org_id, "project", "p1"): _project([("u1", "agent-a")], org_id, "Project P", "p1"),
    }
    models = {org_id: {"model-a": frozenset({"agent-a"})}}
    return SimpleNamespace(scopes=scopes, models=models)


def test_validate_known_events_accepts_well_formed_declarations():
    known = KnownEvents(
        org=_event(),
        teams={"Team A": _event()},
        projects={"Project P": _event()},
        agents={"agent-a": _event()},
    )
    validate_known_events(known, _dataset(), "org-a", _dates(30, "2026-09-01"))


def test_validate_known_events_rejects_unknown_team_name():
    known = KnownEvents(teams={"Typo Team": _event()})
    with pytest.raises(InputError, match="unknown team"):
        validate_known_events(known, _dataset(), "org-a", _dates(30, "2026-09-01"))


def test_validate_known_events_rejects_unknown_project_name():
    known = KnownEvents(projects={"Typo Project": _event()})
    with pytest.raises(InputError, match="unknown project"):
        validate_known_events(known, _dataset(), "org-a", _dates(30, "2026-09-01"))


def test_validate_known_events_rejects_unknown_agent_name():
    known = KnownEvents(agents={"agent-z": _event()})
    with pytest.raises(InputError, match="unknown agent"):
        validate_known_events(known, _dataset(), "org-a", _dates(30, "2026-09-01"))


def test_validate_known_events_rejects_window_outside_horizon():
    known = KnownEvents(org=_event(start="2026-08-01", end="2026-08-05"))
    with pytest.raises(InputError, match="forecast horizon"):
        validate_known_events(known, _dataset(), "org-a", _dates(30, "2026-09-01"))


def test_validate_known_events_rejects_ambiguous_team_name():
    org_id = "org-a"
    dataset = _dataset(org_id)
    dataset.scopes[(org_id, "team", "team-a-dup")] = _team("Team A", "team-a-dup", org_id)
    known = KnownEvents(teams={"Team A": _event()})
    with pytest.raises(InputError, match="ambiguous"):
        validate_known_events(known, dataset, org_id, _dates(30, "2026-09-01"))


def test_validate_known_events_is_a_no_op_when_empty():
    validate_known_events(KnownEvents(), _dataset(), "org-a", [])
