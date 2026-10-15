"""budgets -- the manager API's spend policy table (MVP plan F1).

Owned here under storage.md §3.1's single-definition rule though designed in
`docs/plan/2026-09-09-mvp-backend-implementation.md` F1. The roster/teams/roles this scopes
against live in Track G (`directory.py`) -- this module owns only the dollar policy.

There is no seat/token allowance table: a seat's allowance is derived at query time from its
team's budget / members (docs/plan/2026-09-26-cost-based-seat-utilization.md). The token
`allowances` table that used to live here was dropped by migration `c4e8a2f1d7b3`.

No FKs (storage.md §3.1): a budget on a team that is later renamed or removed is not enforced
away here -- `scope_id` is validated against the directory at the API layer (F1's own rule:
"a budget on an unknown scope is rejected"), not by a constraint.

Write path: `cli_budgets.py` (config import) and `PUT /v1/budgets/{scope_type}/{scope_id}`
(lead/admin, F2-gated) -- both go through `server.api.manager.budgets`.
"""
from decimal import Decimal

from sqlalchemy import Numeric
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, ts


class Budget(Base):
    """A dollar budget for one scope over one recurring period. `period` is a cadence label
    (the org's budget-cycle label: "monthly", "quarterly", "sprint", ... -- see
    `budget_cycle`), not a specific calendar month; a scope's budget is replaced
    in place when re-imported, not versioned by month."""

    __tablename__ = "budgets"
    scope_type: Mapped[str] = mapped_column(primary_key=True)   # org | team | repo
    scope_id: Mapped[str] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(primary_key=True)        # the org's budget-cycle label
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    updated_at: Mapped[ts]
