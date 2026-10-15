"""Dated known-event overlay: a post-simulation, model-agnostic path multiplier.

Design doc §7. A declared, planned future event (a release, a migration, a marketing
push) is not something the data-driven model can learn (a one-off future event has no
historical signal), so the operator declares it explicitly and this module applies it,
after path simulation and before allocation/summaries, directly to the saved
`(days x samples)` path arrays.

Anti-washout rule: the factor is drawn **once per path** (never independently per
(path, day)), so the event moves the window's mean *and* widens/shifts its band instead
of averaging away.

Two orthogonal, independently drawn axes compose multiplicatively:
    effective(day) = scope_event(day) * agent_event(day)      # inactive event -> 1.0
- scope: most-specific-wins precedence on the Organization/Team/Project tree.
- agent: one event per agent, applied to that agent's models everywhere (exact, since
  Models partition an Agent cleanly).
"""

from __future__ import annotations

import hashlib
import json

import numpy as np

from .errors import InputError


def _seed(seed: int, *parts: object) -> int:
    encoded = json.dumps([seed, *parts], ensure_ascii=True, separators=(",", ":")).encode()
    return int.from_bytes(hashlib.sha256(encoded).digest()[:16], "big")


def team_names_by_user(data) -> dict[tuple[str, str], str]:
    """Map attributed users to their one saved Team; null users use defaults."""
    result = {}
    for scope in data.scopes.values():
        if scope.scope_type != "team":
            continue
        for user, _ in scope.keys:
            if user is None:
                continue
            key = (scope.org_id, user)
            previous = result.setdefault(key, scope.name)
            if previous != scope.name:
                raise InputError("user belongs to multiple Teams")
    return result


def resolve_team_for_project(project_scope, team_by_user) -> str | None:
    """Best-effort Project -> Team resolution from declared population overlap.

    A Project belongs to exactly one Team per the structural model (design §1), but the
    *current* synthetic data can violate that (a Project's population can include users
    from more than one Team). When a project's declared population resolves to exactly
    one Team, that Team is used for the middle precedence step (project -> team -> org);
    when it resolves to more than one (today's violating data), the middle step is
    skipped and precedence falls straight through to org. Explicit team/project/agent
    *events* still apply to their own series regardless of this fallback. This becomes
    unconditionally reliable once Phase 3 enforces one-team-per-project.
    """
    teams = {
        team_by_user[(project_scope.org_id, user)]
        for user, _agent in project_scope.keys
        if user is not None and (project_scope.org_id, user) in team_by_user
    }
    return next(iter(teams)) if len(teams) == 1 else None


def _agent_for_model(data, org_id, model) -> str | None:
    """Return the model's unique agent, or None when the dataset leaves it ambiguous.

    The structural model (design §1) has each Model belong to exactly one Agent, and the
    synthetic dataset loader enforces that. The neutral loader currently accepts a model
    catalog entry naming more than one agent_type; when that happens the agent axis of a
    known event cannot be unambiguously targeted, so it is skipped for that series (the
    scope axis still applies).
    """
    agents = data.models.get(org_id, {}).get(model)
    if not agents or len(agents) != 1:
        return None
    return next(iter(agents))


def _match_scope(data, org_id, scope_type, name):
    matches = [
        s for s in data.scopes.values()
        if s.org_id == org_id and s.scope_type == scope_type and s.name == name
    ]
    if not matches:
        raise InputError(
            f"known_events references unknown {scope_type} name {name!r} "
            f"in Organization {org_id!r}"
        )
    if len(matches) != 1:
        raise InputError(
            f"known_events {scope_type} name {name!r} is ambiguous "
            f"in Organization {org_id!r}"
        )
    return matches[0]


def validate_known_events(known_events, data, default_org_id, requested_dates) -> None:
    """Cross-reference known_events names/dates against the dataset and forecast horizon.

    Static shape/range checks (dates well-formed, multiplier/spread bounds) already ran
    in `config.py`. This function performs the checks that need the loaded dataset and
    the resolved request window, so it must run in the runner after both are available -
    on the full-run path *and* the --validate-only path (validate-only exists precisely
    to surface configuration errors like these without running a full simulation).

    Name resolution is scoped to `default_org_id` (Phase 1 requires it to be set when
    known_events is non-empty; see `ForecastConfig.defaults`). The known_events surface
    carries no per-event org key, so genuinely multi-org known_events is out of scope for
    Phase 1.
    """
    if known_events.is_empty():
        return
    if not requested_dates:
        raise InputError("known_events requires a nonempty forecast horizon")
    horizon_start, horizon_end = requested_dates[0], requested_dates[-1]

    def _check_window(label, event):
        if event.start_date < horizon_start or event.end_date > horizon_end:
            raise InputError(
                f"known_events {label} window {event.start_date.isoformat()}.."
                f"{event.end_date.isoformat()} falls outside the forecast horizon "
                f"{horizon_start.isoformat()}..{horizon_end.isoformat()}"
            )

    if known_events.org is not None:
        _check_window("org", known_events.org)
    for name, event in known_events.teams.items():
        _match_scope(data, default_org_id, "team", name)
        _check_window(f"team {name!r}", event)
    for name, event in known_events.projects.items():
        _match_scope(data, default_org_id, "project", name)
        _check_window(f"project {name!r}", event)
    known_agents = {
        agent
        for agents_for_model in data.models.get(default_org_id, {}).values()
        for agent in agents_for_model
    }
    for name, event in known_events.agents.items():
        if name not in known_agents:
            raise InputError(
                f"known_events references unknown agent {name!r} "
                f"in Organization {default_org_id!r}"
            )
        _check_window(f"agent {name!r}", event)


def _draw_factor(seed, parts, event, paths) -> np.ndarray:
    """Per-path multiplier, drawn once per path (never independently per day)."""
    if event.spread == 0:
        return np.full(paths, event.multiplier, dtype=np.float64)
    rng = np.random.default_rng(_seed(seed, *parts))
    return rng.uniform(
        event.multiplier - event.spread, event.multiplier + event.spread, size=paths
    )


def _scope_event(known_events, scope, team_by_user):
    """Most-specific-wins scope resolution (design §7 precedence table)."""
    if scope.scope_type == "project":
        event = known_events.projects.get(scope.name)
        if event is not None:
            return "project", scope.name, event
        team_name = resolve_team_for_project(scope, team_by_user)
        if team_name is not None:
            event = known_events.teams.get(team_name)
            if event is not None:
                return "team", team_name, event
    elif scope.scope_type == "team":
        event = known_events.teams.get(scope.name)
        if event is not None:
            return "team", scope.name, event
    if known_events.org is not None:
        return "org", "org", known_events.org
    return None


def _provenance(level, name, event, factor):
    return {
        "level": level,
        "name": name,
        "start_date": event.start_date.isoformat(),
        "end_date": event.end_date.isoformat(),
        "multiplier": event.multiplier,
        "spread": event.spread,
        "factor_mean": float(np.mean(factor)),
        "factor_min": float(np.min(factor)),
        "factor_max": float(np.max(factor)),
    }


def apply_known_events(
    items, scope, data, known_events, team_by_user, requested_dates, seed
) -> list[dict]:
    """Mutate qualified/inactive items' `paths` in place with the two-axis overlay.

    `items` is the per-scope list of runner working dicts (as built by
    `runner.process_scope`); only entries with `item["paths"]` already set (qualified or
    inactive; `None` for not-yet-simulated/sampling_failed entries) are touched. Zero
    (inactive) paths stay zero under any multiplier. Returns one provenance dict per
    (level, name) actually applied within this scope call; callers dedupe across scopes
    (the factor for a given (level, name) is a pure function of it, so duplicates carry
    identical stats).
    """
    if known_events.is_empty():
        return []
    paths_n = next(
        (item["paths"].shape[1] for item in items if item.get("paths") is not None),
        None,
    )
    if paths_n is None:
        return []
    provenance: dict[tuple[str, str], dict] = {}
    scope_match = _scope_event(known_events, scope, team_by_user)
    scope_mask = scope_factor = None
    if scope_match is not None:
        level, name, event = scope_match
        scope_mask = np.asarray(
            [event.start_date <= d <= event.end_date for d in requested_dates]
        )
        scope_factor = _draw_factor(seed, ("known-event-scope", level, name), event, paths_n)
        provenance[(level, name)] = _provenance(level, name, event, scope_factor)
    agent_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for item in items:
        arr = item.get("paths")
        if arr is None:
            continue
        combined = np.ones_like(arr)
        matched = False
        if scope_match is not None:
            combined[scope_mask, :] *= scope_factor
            matched = True
        agent = _agent_for_model(data, scope.org_id, item["history"].model)
        if agent is not None and agent in known_events.agents:
            if agent not in agent_cache:
                aevent = known_events.agents[agent]
                amask = np.asarray(
                    [aevent.start_date <= d <= aevent.end_date for d in requested_dates]
                )
                afactor = _draw_factor(seed, ("known-event-agent", agent), aevent, paths_n)
                agent_cache[agent] = (amask, afactor)
                provenance[("agent", agent)] = _provenance("agent", agent, aevent, afactor)
            amask, afactor = agent_cache[agent]
            combined[amask, :] *= afactor
            matched = True
        if matched:
            item["paths"] = arr * combined
            if item.get("status") == "qualified":
                item["known_event_applied"] = True
    return list(provenance.values())
