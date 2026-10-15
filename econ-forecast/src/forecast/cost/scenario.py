"""Scenario application (design §6): `model_transfer`, `agent_transfer`, `growth`,
each applied in order (every change acts on the previous result, §6.2) to the
declared scope, its sub-scopes (`k = 1`), and its parent scopes (`k` = attribution
share, §6.3). Produces a mutated `kernel.SeriesInput` list; `kernel.price_one_series`
prices it exactly like the baseline (§2.2) -- this module never computes a rate.

Memory (§2.2's "one scope at a time" bound, applied to the *pricing* loop): only the
declared scope, its descendants, and its ancestors get writable in-memory token
arrays here (the design's scenario-affected subtree, never the whole org); every
other scope is left on the forecast's own memory-mapped arrays untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np

from forecast.forecasting.dataset import PreparedDataset

from .errors import InputError
from .kernel import SeriesInput
# Ancestor discovery (a team's parent Organization; a project's team + Organization)
# lives in `mix.py` as `ancestors_of`, since `artifacts.py`'s mix.json writer needs
# the same scope-hierarchy discovery this module uses for §6.3 propagation.
from .mix import ancestors_of as _ancestors
from .mix import MixResult, attribution_share, compute_mix, teams_matching_project, weighted_total
from .request import ResolvedRequest
from .schemas import HALF_LIFE_DAYS, OWN_MIX_HALF_WEIGHT_DAYS, TOKEN_TYPES

ScopeKey = tuple[str, str]  # (scope_type, scope_id)


def _descendants(dataset: PreparedDataset, org_id: str, scope_type: str, scope_id: str) -> list[ScopeKey]:
    """Scopes strictly below the given one (§6.3, k = 1): a team's own projects; an
    Organization's every Team and Project; a Project has none. A candidate project
    whose team is genuinely ambiguous (matches more than one team, one of them this
    declared team) raises rather than silently deciding it either is or is not a
    sub-scope (fix 2: "never silently skip the team level")."""
    if scope_type == "project":
        return []
    if scope_type == "team":
        result = []
        for (o, t, sid) in dataset.scopes:
            if o != org_id or t != "project":
                continue
            matches = teams_matching_project(dataset, org_id, sid)
            if scope_id not in matches:
                continue
            if len(matches) > 1:
                raise InputError(
                    f"project {sid!r} does not have a uniquely determined team "
                    f"({len(matches)} candidate teams, including {scope_id!r}); "
                    "cannot determine its sub-scope membership for the scenario"
                )
            result.append(("project", sid))
        return sorted(result)
    return sorted((t, sid) for (o, t, sid) in dataset.scopes if o == org_id and t in ("team", "project"))


def _find_scope_by_name(saved, org_id: str, scope_type: str, name: str | None) -> dict:
    if scope_type == "organization":
        matches = [s for s in saved.index["scopes"] if s["org_id"] == org_id and s["scope_type"] == "organization"]
    else:
        matches = [
            s
            for s in saved.index["scopes"]
            if s["org_id"] == org_id and s["scope_type"] == scope_type and s["scope_name"] == name
        ]
    return matches[0]


def _scope_name(saved, scope_key: ScopeKey) -> str:
    scope_type, scope_id = scope_key
    for s in saved.index["scopes"]:
        if s["scope_type"] == scope_type and s["scope_id"] == scope_id:
            return s["scope_name"]
    return scope_id


def _agent_share(mix_shares: tuple[dict, ...], agent_type: str) -> float:
    return sum(row.get(tt, 0.0) for row in mix_shares if row["agent_type"] == agent_type for tt in TOKEN_TYPES)


def _to_agent_model_split(
    dataset: PreparedDataset,
    org_id: str,
    to_agent: str,
    origin: date,
    max_age_days: int,
    half_life_days: int,
    own_mix_half_weight_days: int,
) -> dict[str, float]:
    """§6.2 agent_transfer: the `to` agent's org-level split across its own models,
    weighted the same way as mix (§5) -- each model's volume is its org-level
    weighted total times the `to` agent's own-mix share of it."""
    volumes: dict[str, float] = {}
    for model, agents in dataset.models.get(org_id, {}).items():
        if to_agent not in agents:
            continue
        mix_result = compute_mix(
            dataset, org_id, "organization", org_id, model, origin, max_age_days,
            half_life_days=half_life_days, own_mix_half_weight_days=own_mix_half_weight_days,
        )
        share = _agent_share(mix_result.shares, to_agent)
        if share <= 0:
            continue
        total = weighted_total(dataset, org_id, "organization", org_id, model, origin, max_age_days, half_life_days=half_life_days)
        volumes[model] = share * total
    grand_total = sum(volumes.values())
    if grand_total <= 0:
        return {}
    return {model: v / grand_total for model, v in volumes.items() if v > 0}


@dataclass
class _Ledger:
    """Writable per-(scope, model) token arrays for exactly the scenario-affected
    subtree (§2.2). `arrays` starts as a copy of every qualified/inactive series in
    an affected scope; a transfer's destination model is lazily created at zero."""

    shape: tuple[int, int]
    arrays: dict[ScopeKey, dict[str, np.ndarray]] = field(default_factory=dict)
    added: set[tuple[str, str, str]] = field(default_factory=set)
    touched: dict[tuple[str, str, str], str] = field(default_factory=dict)
    propagated: set[tuple[str, str, str]] = field(default_factory=set)
    #: The full `MixResult` of the *source* series a borrowed-mix series drew its
    #: shares from (§6.2, §8.2 `mix.json`: the borrowed entry reports the source's
    #: window, not its own).
    borrowed: dict[tuple[str, str, str], MixResult] = field(default_factory=dict)

    def get_or_zero(self, scope_key: ScopeKey, model: str) -> np.ndarray:
        models = self.arrays.setdefault(scope_key, {})
        if model not in models:
            models[model] = np.zeros(self.shape, dtype=np.float64)
            self.added.add((*scope_key, model))
        return models[model]


def _load_ledger(saved, affected: set[ScopeKey], shape: tuple[int, int]) -> _Ledger:
    ledger = _Ledger(shape=shape, arrays={key: {} for key in affected})
    for entry in saved.index["series"]:
        key = (entry["scope_type"], entry["scope_id"])
        if key not in affected or entry["status"] not in ("qualified", "inactive"):
            continue
        ledger.arrays[key][entry["model"]] = np.array(saved.load_paths(entry), copy=True)
    return ledger


def _label(token_factor: float) -> str:
    return "scenario_constant_tokens" if token_factor == 1.0 else "scenario_token_factor_manual"


def _apply_model_transfer(ledger: _Ledger, change, scope_key: ScopeKey, k: float, mask: np.ndarray) -> None:
    models = ledger.arrays.get(scope_key, {})
    if change.from_ not in models:
        return  # scope has no `from` series (§6.2: "a scope without the from model ... is skipped")
    from_arr = models[change.from_]
    moved = np.zeros_like(from_arr)
    moved[mask] = k * change.fraction * from_arr[mask]
    models[change.from_] = from_arr - moved
    to_arr = ledger.get_or_zero(scope_key, change.to)
    ledger.arrays[scope_key][change.to] = to_arr + change.token_factor * moved
    label = _label(change.token_factor)
    ledger.touched[(*scope_key, change.from_)] = label
    ledger.touched[(*scope_key, change.to)] = label


def _apply_agent_transfer(
    ledger: _Ledger,
    dataset: PreparedDataset,
    org_id: str,
    change,
    scope_key: ScopeKey,
    k_for_model,
    mask: np.ndarray,
    origin: date,
    max_age_days: int,
    half_life_days: int,
    own_mix_half_weight_days: int,
) -> None:
    """§6.2: `moved_m` is computed per model `m` (`a_from[m]` is model-specific);
    at an ancestor scope, `k_for_model(m)` is that model's own attribution share
    (§6.3: "s is per model" for agent_transfer), not one blanket k for the scope."""
    scope_type, scope_id = scope_key
    models = ledger.arrays.get(scope_key, {})
    if not models:
        return
    parent = None if scope_type == "organization" else ("organization", org_id)
    moved_total = np.zeros(ledger.shape, dtype=np.float64)
    any_from_usage = False
    for model in list(models):
        mix_result = compute_mix(
            dataset, org_id, scope_type, scope_id, model, origin, max_age_days,
            parent=parent, half_life_days=half_life_days, own_mix_half_weight_days=own_mix_half_weight_days,
        )
        a_from = _agent_share(mix_result.shares, change.from_)
        if a_from <= 0:
            continue
        any_from_usage = True
        k = k_for_model(model)
        arr = models[model]
        moved_m = np.zeros_like(arr)
        moved_m[mask] = k * change.fraction * a_from * arr[mask]
        models[model] = arr - moved_m
        moved_total += moved_m
        ledger.touched[(*scope_key, model)] = _label(change.token_factor)
    if not any_from_usage:
        return  # scope has no usage from this agent (§6.2)
    split = _to_agent_model_split(dataset, org_id, change.to, origin, max_age_days, half_life_days, own_mix_half_weight_days)
    for j, fraction in split.items():
        # `moved_total` already carries each source model's own k (folded in above);
        # the destination side only splits that already-scaled total across models.
        to_arr = ledger.get_or_zero(scope_key, j)
        ledger.arrays[scope_key][j] = to_arr + change.token_factor * fraction * moved_total
        ledger.touched[(*scope_key, j)] = _label(change.token_factor)


def _apply_growth(ledger: _Ledger, change, scope_key: ScopeKey, k_for_model, mask: np.ndarray) -> None:
    for model, arr in list(ledger.arrays.get(scope_key, {}).items()):
        k = k_for_model(model)
        factor = np.ones(arr.shape[0], dtype=np.float64)
        factor[mask] = 1 + (change.factor - 1) * k
        ledger.arrays[scope_key][model] = arr * factor[:, None]


def _effective_mask(change, dates: tuple[date, ...]) -> np.ndarray:
    effective_from = change.effective_from or dates[0]
    return np.array([d >= effective_from for d in dates])


def _apply_change_at_scope(
    ledger: _Ledger,
    dataset: PreparedDataset,
    org_id: str,
    change,
    scope_key: ScopeKey,
    *,
    is_ancestor: bool,
    declared_key: ScopeKey,
    dates: tuple[date, ...],
    origin: date,
    max_age_days: int,
    half_life_days: int,
    own_mix_half_weight_days: int,
) -> None:
    mask = _effective_mask(
        change.model_transfer or change.agent_transfer or change.growth, dates
    )

    def _share(model: str) -> float:
        return attribution_share(
            dataset, org_id, declared_key[0], declared_key[1], scope_key[0], scope_key[1],
            model, origin, max_age_days, half_life_days=half_life_days,
        )

    if change.model_transfer is not None:
        m = change.model_transfer
        k = _share(m.from_) if is_ancestor else 1.0
        _apply_model_transfer(ledger, m, scope_key, k, mask)
        if is_ancestor:
            ledger.propagated.add((*scope_key, m.from_))
            ledger.propagated.add((*scope_key, m.to))
    elif change.agent_transfer is not None:
        a = change.agent_transfer
        if is_ancestor:
            k_for_model = lambda model: _share(model)  # noqa: E731
        else:
            k_for_model = lambda model: 1.0  # noqa: E731
        _apply_agent_transfer(
            ledger, dataset, org_id, a, scope_key, k_for_model, mask, origin, max_age_days,
            half_life_days, own_mix_half_weight_days,
        )
        if is_ancestor:
            for key in list(ledger.touched):
                if key[:2] == scope_key:
                    ledger.propagated.add(key)
    else:
        g = change.growth
        if is_ancestor:
            k_for_model = lambda model: _share(model)  # noqa: E731
        else:
            k_for_model = lambda model: 1.0  # noqa: E731
        _apply_growth(ledger, g, scope_key, k_for_model, mask)
        if is_ancestor:
            for model in ledger.arrays.get(scope_key, {}):
                ledger.propagated.add((*scope_key, model))


def apply_scenario(resolved: ResolvedRequest) -> list[SeriesInput]:
    """The scenario's full series list (§6.2, §6.3): baseline series, mutated where
    a change reached them, plus any newly-created destination series. Baseline and
    scenario runs share the same scope set (§6.3) -- only series within a scope can
    be added, never a scope."""
    scenario = resolved.scenario
    saved = resolved.forecast_run
    dataset = resolved.dataset
    org_id = resolved.org_id
    dates = resolved.dates
    origin = saved.origin
    max_age_days = resolved.history_lookback_days
    half_life_days = HALF_LIFE_DAYS
    own_mix_half_weight_days = OWN_MIX_HALF_WEIGHT_DAYS

    declared_entry = _find_scope_by_name(saved, org_id, scenario.scope.type, scenario.scope.name)
    declared_key = (declared_entry["scope_type"], declared_entry["scope_id"])
    descendant_keys = _descendants(dataset, org_id, *declared_key)
    # strict=True: the declared scope's own ancestors are genuinely needed for
    # propagation (fix 2) -- an undetermined project team must raise, not vanish.
    ancestor_keys = _ancestors(dataset, org_id, *declared_key, strict=True)
    k1_keys = [declared_key, *descendant_keys]
    affected = set(k1_keys) | set(ancestor_keys)

    shape = (len(dates), resolved.samples)
    ledger = _load_ledger(saved, affected, shape)

    for change in scenario.changes:
        for scope_key in k1_keys:
            _apply_change_at_scope(
                ledger, dataset, org_id, change, scope_key, is_ancestor=False,
                declared_key=declared_key, dates=dates, origin=origin, max_age_days=max_age_days,
                half_life_days=half_life_days, own_mix_half_weight_days=own_mix_half_weight_days,
            )
        for scope_key in ancestor_keys:
            _apply_change_at_scope(
                ledger, dataset, org_id, change, scope_key, is_ancestor=True,
                declared_key=declared_key, dates=dates, origin=origin, max_age_days=max_age_days,
                half_life_days=half_life_days, own_mix_half_weight_days=own_mix_half_weight_days,
            )

    # §6.2: "not used anywhere -> source mix, scenario_mix_borrowed". A touched
    # series with nowhere to draw a mix from (own usage and the Organization's both
    # empty) borrows another priced model's mix in the same scope rather than
    # silently pricing at a zero rate (rates.py's `used_types` would otherwise be
    # empty and the series would price to $0 despite carrying real tokens).
    for scope_type, scope_id, model in list(ledger.touched):
        scope_key = (scope_type, scope_id)
        arr = ledger.arrays.get(scope_key, {}).get(model)
        if arr is None or not np.any(arr):
            continue
        parent = None if scope_type == "organization" else ("organization", org_id)
        own_mix = compute_mix(
            dataset, org_id, scope_type, scope_id, model, origin, max_age_days,
            parent=parent, half_life_days=half_life_days, own_mix_half_weight_days=own_mix_half_weight_days,
        )
        if own_mix.shares:
            continue
        for other_model, other_arr in sorted(ledger.arrays.get(scope_key, {}).items()):
            if other_model == model:
                continue
            other_mix = compute_mix(
                dataset, org_id, scope_type, scope_id, other_model, origin, max_age_days,
                parent=parent, half_life_days=half_life_days, own_mix_half_weight_days=own_mix_half_weight_days,
            )
            if other_mix.shares:
                ledger.borrowed[(scope_type, scope_id, model)] = other_mix
                break

    seen: set[tuple] = set()
    series_inputs: list[SeriesInput] = []
    for entry in saved.index["series"]:
        scope_key = (entry["scope_type"], entry["scope_id"])
        key = (*scope_key, entry["model"])
        seen.add(key)
        assumptions = list(entry["assumptions"])
        reasons = list(entry["reasons"])
        if scope_key in ledger.arrays and entry["model"] in ledger.arrays[scope_key]:
            tokens = ledger.arrays[scope_key][entry["model"]]
            status = entry["status"]
        elif entry["status"] in ("qualified", "inactive"):
            tokens = saved.load_paths(entry)
            status = entry["status"]
        else:
            tokens = None
            status = entry["status"]
        if key in ledger.touched:
            assumptions.append(ledger.touched[key])
        if key in ledger.propagated:
            assumptions.append("scenario_propagated_by_share")
        borrowed = ledger.borrowed.get(key)
        if borrowed is not None:
            assumptions.append("scenario_mix_borrowed")
        series_inputs.append(
            SeriesInput(
                org_id=org_id, scope_type=scope_key[0], scope_id=scope_key[1],
                scope_name=entry["scope_name"], model=entry["model"], status=status,
                tokens=tokens, assumptions=tuple(assumptions), reasons=tuple(reasons),
                mix_override=borrowed.shares if borrowed is not None else None,
                mix_override_window=(
                    (borrowed.window_start, borrowed.window_end) if borrowed is not None else None
                ),
            )
        )

    for scope_key, models in ledger.arrays.items():
        for model, arr in models.items():
            key = (*scope_key, model)
            if key in seen:
                continue
            assumptions = ["scenario_added_series"]
            if key in ledger.touched:
                assumptions.append(ledger.touched[key])
            if key in ledger.propagated:
                assumptions.append("scenario_propagated_by_share")
            borrowed = ledger.borrowed.get(key)
            if borrowed is not None:
                assumptions.append("scenario_mix_borrowed")
            series_inputs.append(
                SeriesInput(
                    org_id=org_id, scope_type=scope_key[0], scope_id=scope_key[1],
                    scope_name=_scope_name(saved, scope_key), model=model, status="qualified",
                    tokens=arr, assumptions=tuple(assumptions), reasons=(),
                    mix_override=borrowed.shares if borrowed is not None else None,
                    mix_override_window=(
                        (borrowed.window_start, borrowed.window_end) if borrowed is not None else None
                    ),
                )
            )
    return series_inputs
