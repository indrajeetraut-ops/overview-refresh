"""F1: budgets -- the manager API's spend policy read/write (MVP plan F1). There is no seat/token
allowance to read or write: a seat's allowance is derived from its team budget at query time
(docs/plan/2026-09-26-cost-based-seat-utilization.md).

Framework-free (ground rule 1): plain `Session` + `QueryCatalog` in, plain dicts out.
`app.py`'s routes are thin wrappers, same as `single_user/service.py`.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from server.storage.queries import QueryCatalog

from ..errors import BadRequestError, NotFoundError
from . import budget_cycles

SCOPE_TYPES = ("org", "team", "repo")


#: Labels that name a calendar cadence on their own (no cycle configuration needed to resolve them).
_CALENDAR_BY_LABEL = {label: kind for kind, label in budget_cycles.CYCLE_LABELS.items()
                      if kind in budget_cycles.CALENDAR_KINDS}


def period_bounds(period: str | budget_cycles.Cycle | budget_cycles.ActiveCycle,
                  anchor: date) -> tuple[date, date]:
    """The half-open `[start, end)` calendar bounds of the budget period that CONTAINS `anchor` --
    `end` is the first day of the next period, so `(end - start).days` is the period's own length
    in days. `period` is a budget label (`"monthly"`, `"quarterly"`, `"half-yearly"`, `"yearly"`),
    or a cycle / `ActiveCycle` for any kind, sprint and custom included (their length is part of
    the cycle, not of the label). An unknown label, or `"sprint"`/`"custom"` given as a bare
    label, is a `BadRequestError` rather than a silent guess at a length. Used by E1's
    `budget_burn`, which must forecast to the period's own end, not the requested display
    window's."""
    if isinstance(period, budget_cycles.ActiveCycle):
        period = period.cycle
    if isinstance(period, str):
        kind = _CALENDAR_BY_LABEL.get(period)
        if kind is None:
            raise BadRequestError(
                f"unsupported budget period {period!r}; expected one of "
                f"{sorted(_CALENDAR_BY_LABEL)} (sprint and custom need the org's cycle)")
        period = budget_cycles.Cycle(kind=kind)
    found = budget_cycles.period_containing(period, anchor)
    return found["start"], found["end_exclusive"]


def _validate_scope(s: Session, catalog: QueryCatalog, scope_type: str, scope_id: str) -> None:
    if scope_type not in SCOPE_TYPES:
        raise BadRequestError(f"unknown scope_type {scope_type!r}; expected one of {SCOPE_TYPES}")
    if scope_type == "team":
        if not s.execute(catalog.get("manager", "team_scope_exists"), {"scope_id": scope_id}).first():
            raise NotFoundError(f"unknown team {scope_id!r}")
    elif scope_type == "repo":
        if not s.execute(catalog.get("manager", "repo_scope_exists"), {"scope_id": scope_id}).first():
            raise NotFoundError(f"unknown repo {scope_id!r}")
    # org: no existence check -- one global org scope in single-tenant MVP (directory posture).


def get_budget(s: Session, catalog: QueryCatalog, scope_type: str, scope_id: str,
              period: str) -> dict:
    """A scope with no budget row returns `amount_usd: None` -- callers compute "% of budget"
    only when it is non-null (F1's own rule: omit the comparison, never divide by zero)."""
    _validate_scope(s, catalog, scope_type, scope_id)
    budget = s.execute(catalog.get("manager", "get_budget"),
                       {"scope_type": scope_type, "scope_id": scope_id, "period": period}).first()
    return {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "period": period,
        "amount_usd": str(budget.amount_usd) if budget else None,
    }


def put_budget(s: Session, catalog: QueryCatalog, scope_type: str, scope_id: str, period: str,
               amount_usd: Decimal) -> dict:
    _validate_scope(s, catalog, scope_type, scope_id)
    s.execute(catalog.get("manager", "upsert_budget"),
             {"scope_type": scope_type, "scope_id": scope_id, "period": period,
              "amount_usd": amount_usd})
    return get_budget(s, catalog, scope_type, scope_id, period)


def budgets_for_scopes(s: Session, catalog: QueryCatalog, scope_type: str,
                       scope_ids: list[str], period: str) -> dict[str, Decimal]:
    """Every scope's budget in one round-trip (A1/A3/A4 need a whole roster's worth at
    once) -- a scope with no row is simply absent from the returned mapping."""
    if not scope_ids:
        return {}
    rows = s.execute(catalog.get("manager", "budgets_for_scopes"),
                     {"scope_type": scope_type, "scope_ids": scope_ids, "period": period})
    return {r.scope_id: r.amount_usd for r in rows}
