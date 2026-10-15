"""budget_cycle -- the org's budget cadence (Overview refresh plan, Track A1).

One row per org says what a budget "period" is: a calendar month/quarter/half/year, a numbered
sprint of N weeks, or a custom date range (one-time or recurring). Budget amounts in `budgets`
are always "per period of this cycle"; `budgets.period` stores the cycle's label (`monthly`,
`quarterly`, `half-yearly`, `yearly`, `sprint`, `custom`), so changing the kind leaves the old
amounts stored but unread.

No row means the default cycle: a recurring calendar month, which is exactly the behaviour
before this table existed. Nothing seeds it.

Field use by kind (the API layer validates and nulls the irrelevant ones, no CHECKs here):
`sprint` -- `sprint_weeks`, `anchor_date` (start of the sprint numbered `anchor_number`);
`custom` -- `anchor_date` (first day) and `custom_end` (last day, inclusive); `month|quarter|
half|year` -- none, and always `recurring`.

No FKs (storage.md §3.1). `org_id` is the directory's default org in single-tenant MVP.
Read/written through `server.api.manager.budget_cycles`.
"""
from datetime import date

from sqlalchemy import Date, Integer
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, ts


class BudgetCycle(Base):
    __tablename__ = "budget_cycle"
    org_id: Mapped[str] = mapped_column(primary_key=True)
    kind: Mapped[str]                                    # month|quarter|half|year|sprint|custom
    recurrence: Mapped[str]                              # recurring | one-time
    sprint_weeks: Mapped[int | None] = mapped_column(Integer)
    anchor_date: Mapped[date | None] = mapped_column(Date)
    anchor_number: Mapped[int | None] = mapped_column(Integer)
    custom_end: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[ts]
