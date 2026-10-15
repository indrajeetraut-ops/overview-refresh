"""Budget cycles -- the org's budget cadence and the periods it generates (Overview refresh,
Track A1; plan docs/plan/2026-10-14-overview-ux-refresh.md §3).

A cycle says what a budget "period" is: a calendar month/quarter/half/year, a numbered sprint of
N weeks, or a custom date range (one-time or recurring). This module is the ONLY place calendar
and sprint arithmetic happens; the front end asks for periods and never computes them. Dates are
UTC calendar dates. The arithmetic is a port of the reference prototype's `budget-cycles.ts`
(read-only idea source) with these deliberate differences:
  * sprints extend backwards from the anchor (numbers stay >= 1), so history is not cut at the
    anchor; with today before the anchor there are no past periods and `next` is the anchor sprint;
  * a period that has not started has `days_elapsed` 0 (the reference reports 1);
  * `one-time` exists only for sprint and custom (a one-time calendar month would move with today);
  * `anchor_date` is required for sprint and custom (the reference silently used today);
  * custom periods are labelled by their dates (no name column).

Framework-free (ground rule 1): plain `Session` + `QueryCatalog` in, plain dicts out; SQL lives in
`config/queries/manager.yaml`. `app.py`'s routes are thin wrappers, same as `budgets.py`.

No row in `budget_cycle` means the default cycle: a recurring calendar month with the label
`config.budget_period` ("monthly"), which is exactly the behaviour before cycles existed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy.orm import Session

from server.storage.queries import QueryCatalog

from ..errors import BadRequestError

log = structlog.get_logger(module="api.manager.budget_cycles")

KINDS = ("month", "quarter", "half", "year", "sprint", "custom")
RECURRENCES = ("recurring", "one-time")
CALENDAR_KINDS = ("month", "quarter", "half", "year")

#: The ONE place a cycle kind becomes the label stored in `budgets.period`.
CYCLE_LABELS = {
    "month": "monthly",
    "quarter": "quarterly",
    "half": "half-yearly",
    "year": "yearly",
    "sprint": "sprint",
    "custom": "custom",
}
DEFAULT_LABEL = CYCLE_LABELS["month"]

#: Months per calendar period.
_MONTHS = {"month": 1, "quarter": 3, "half": 6, "year": 12}
_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

MAX_SPRINT_WEEKS = 52
MAX_ANCHOR_NUMBER = 100_000
#: Stored dates must be sane: a typo like year 0202 or 20260 would otherwise generate periods
#: that overflow `date` or run for centuries.
MIN_DATE = date(2000, 1, 1)
MAX_DATE = date(2100, 12, 31)
DEFAULT_SPRINT_WEEKS = 2
MAX_HISTORY = 24
DEFAULT_HISTORY = 6


@dataclass(frozen=True)
class Cycle:
    """A validated cycle. Fields a kind does not use are None."""

    kind: str = "month"
    recurrence: str = "recurring"
    sprint_weeks: int | None = None      # sprint: length in weeks
    anchor_date: date | None = None      # sprint: start of sprint `anchor_number`; custom: first day
    anchor_number: int | None = None     # sprint: number of the sprint starting on anchor_date
    custom_end: date | None = None       # custom: last day, inclusive

    @property
    def label(self) -> str:
        return CYCLE_LABELS[self.kind]


DEFAULT_CYCLE = Cycle()


@dataclass(frozen=True)
class ActiveCycle:
    """The org's cycle resolved once per request: the label budgets are stored under, and the
    cycle that defines the periods."""

    label: str
    cycle: Cycle


def label_for(kind: str) -> str:
    try:
        return CYCLE_LABELS[kind]
    except KeyError:
        raise BadRequestError(f"unknown cycle kind {kind!r}; expected one of {KINDS}") from None


# ------------------------------------------------------------------------------ validation

def _as_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BadRequestError(f"{name} must be an integer")
    return value


def _as_date(value: Any, name: str) -> date:
    parsed: date | None = None
    if isinstance(value, datetime):
        parsed = None
    elif isinstance(value, date):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            parsed = None
    if parsed is None:
        raise BadRequestError(f"{name} must be a calendar date (YYYY-MM-DD)")
    if not MIN_DATE <= parsed <= MAX_DATE:
        raise BadRequestError(f"{name} must be between {MIN_DATE.isoformat()} and "
                              f"{MAX_DATE.isoformat()}")
    return parsed


def utc_date(value: date | datetime) -> date:
    """A calendar date as the period arithmetic needs it: an aware datetime is taken in UTC, a
    naive one is already UTC (the storage layer's own convention), a date stays as it is."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.date()
    return value


def validate(kind: Any, recurrence: Any = "recurring", *, sprint_weeks: Any = None,
             anchor_date: Any = None, anchor_number: Any = None, custom_end: Any = None) -> Cycle:
    """Check a cycle definition and return it normalised: fields the kind does not use are
    dropped (None), `sprint_weeks` defaults to 2 and `anchor_number` to 1. Any problem is a
    `BadRequestError`."""
    if kind not in KINDS:
        raise BadRequestError(f"unknown cycle kind {kind!r}; expected one of {KINDS}")
    if recurrence not in RECURRENCES:
        raise BadRequestError(f"unknown recurrence {recurrence!r}; expected one of {RECURRENCES}")

    if kind in CALENDAR_KINDS:
        if recurrence != "recurring":
            raise BadRequestError(
                f"a {kind} cycle is always recurring; one-time applies to sprint and custom only")
        return Cycle(kind=kind, recurrence=recurrence)

    if anchor_date is None:
        raise BadRequestError(f"a {kind} cycle needs anchor_date"
                              + (" (the first day)" if kind == "custom" else " (the sprint start)"))
    start = _as_date(anchor_date, "anchor_date")

    if kind == "sprint":
        weeks = DEFAULT_SPRINT_WEEKS if sprint_weeks is None else _as_int(sprint_weeks, "sprint_weeks")
        if not 1 <= weeks <= MAX_SPRINT_WEEKS:
            raise BadRequestError(f"sprint_weeks must be between 1 and {MAX_SPRINT_WEEKS}")
        number = 1 if anchor_number is None else _as_int(anchor_number, "anchor_number")
        if not 1 <= number <= MAX_ANCHOR_NUMBER:
            raise BadRequestError(f"anchor_number must be between 1 and {MAX_ANCHOR_NUMBER}")
        return Cycle(kind=kind, recurrence=recurrence, sprint_weeks=weeks,
                     anchor_date=start, anchor_number=number)

    # custom
    if custom_end is None:
        raise BadRequestError("a custom cycle needs custom_end (the last day, inclusive)")
    end = _as_date(custom_end, "custom_end")
    if end < start:
        raise BadRequestError("custom_end must not be before anchor_date")
    return Cycle(kind=kind, recurrence=recurrence, anchor_date=start, custom_end=end)


# ------------------------------------------------------------------------ period arithmetic

def _custom_label(start: date, end_inclusive: date) -> str:
    def fmt(d: date) -> str:
        return f"{d.day:02d} {_MONTH_NAMES[d.month - 1]} {d.year}"
    return f"{fmt(start)} – {fmt(end_inclusive)}"


def _period(key: str, label: str, start: date, end_exclusive: date, today: date) -> dict:
    total = (end_exclusive - start).days
    if today < start:
        elapsed = 0
    elif today >= end_exclusive:
        elapsed = total
    else:
        elapsed = (today - start).days + 1          # today counts as an elapsed day
    return {
        "key": key,
        "label": label,
        "start": start,
        "end_exclusive": end_exclusive,
        "is_current": start <= today < end_exclusive,
        "days_total": total,
        "days_elapsed": elapsed,
    }


def _cycle_length_days(cycle: Cycle) -> int:
    """Fixed period length in days (sprint and custom only)."""
    if cycle.kind == "sprint":
        return (cycle.sprint_weeks or DEFAULT_SPRINT_WEEKS) * 7
    assert cycle.anchor_date is not None and cycle.custom_end is not None
    return (cycle.custom_end - cycle.anchor_date).days + 1


def _calendar_index(cycle: Cycle, d: date) -> int:
    per_year = 12 // _MONTHS[cycle.kind]
    return d.year * per_year + (d.month - 1) // _MONTHS[cycle.kind]


def _index_of(cycle: Cycle, d: date) -> int:
    """The index of the period containing `d`. Calendar: an absolute running index. Sprint and
    custom: the offset from the anchor period (negative before it). One-time cycles have only
    offset 0."""
    if cycle.recurrence == "one-time":
        return 0
    if cycle.kind in CALENDAR_KINDS:
        return _calendar_index(cycle, d)
    assert cycle.anchor_date is not None
    return (d - cycle.anchor_date).days // _cycle_length_days(cycle)   # floor, also for d < anchor


def _make(cycle: Cycle, index: int, today: date) -> dict:
    kind = cycle.kind
    if kind in CALENDAR_KINDS:
        per_year = 12 // _MONTHS[kind]
        year, slot = divmod(index, per_year)
        months = _MONTHS[kind]
        start = date(year, slot * months + 1, 1)
        end = date(year + 1, 1, 1) if slot == per_year - 1 else date(year, (slot + 1) * months + 1, 1)
        if kind == "month":
            label, key = f"{_MONTH_NAMES[slot]} {year}", f"month-{year}-{slot + 1:02d}"
        elif kind == "quarter":
            label, key = f"Q{slot + 1} {year}", f"quarter-{year}-{slot + 1}"
        elif kind == "half":
            label, key = f"H{slot + 1} {year}", f"half-{year}-{slot + 1}"
        else:
            label, key = f"{year}", f"year-{year}"
        return _period(key, label, start, end, today)

    assert cycle.anchor_date is not None
    length = timedelta(days=_cycle_length_days(cycle))
    start = cycle.anchor_date + index * length
    end = start + length
    if kind == "sprint":
        number = (cycle.anchor_number or 1) + index
        return _period(f"sprint-{number}", f"Sprint {number}", start, end, today)
    return _period(f"custom-{start.isoformat()}", _custom_label(start, end - timedelta(days=1)),
                   start, end, today)


def periods(cycle: Cycle, today: date, history: int = DEFAULT_HISTORY) -> dict:
    """`{"periods": [...], "next": {...} | None}`: the last `history` periods that have started
    (oldest first, the current one last) plus the next one. Each period is `{key, label, start,
    end_exclusive, is_current, days_total, days_elapsed}` (UTC dates). `today` is passed in, so
    callers and tests control "now"; a datetime is reduced to its UTC date."""
    today = utc_date(today)
    if isinstance(history, bool) or not isinstance(history, int) or not 1 <= history <= MAX_HISTORY:
        raise BadRequestError(f"history must be an integer between 1 and {MAX_HISTORY}")

    if cycle.recurrence == "one-time":
        only = _make(cycle, 0, today)
        if only["start"] <= today:
            return {"periods": [only], "next": None}
        return {"periods": [], "next": only}

    cur = _index_of(cycle, today)
    if cycle.kind in CALENDAR_KINDS:
        lo = cur - history + 1
    elif cycle.kind == "sprint":
        if cur < 0:                              # today is before the anchor sprint
            return {"periods": [], "next": _make(cycle, 0, today)}
        lo = max(cur - history + 1, 1 - (cycle.anchor_number or 1))   # sprint numbers stay >= 1
    else:                                        # recurring custom: nothing exists before its start
        if cur < 0:
            return {"periods": [], "next": _make(cycle, 0, today)}
        lo = max(cur - history + 1, 0)
    return {"periods": [_make(cycle, i, today) for i in range(lo, cur + 1)],
            "next": _make(cycle, cur + 1, today)}


def period_containing(cycle: Cycle, d: date) -> dict:
    """The period that contains `d`, with `is_current`/`days_elapsed` as of `d`. Always answers,
    which is what proration and burn-up horizons need (the period's real length): a date before a
    sprint's anchor or a recurring custom's start extends the arithmetic backwards (a sprint
    number there may drop below 1), and a date outside a one-time cycle gets the cycle's single
    period (check `start <= d < end_exclusive` to know whether `d` is really inside it). A
    datetime `d` is reduced to its UTC date."""
    d = utc_date(d)
    return _make(cycle, _index_of(cycle, d), d)


def _instant(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _period_view(period: dict, now: datetime) -> dict:
    """A period as the API reports it: exact UTC instants. `to` is the period's end, except for
    the current period where it is `min(now, end)` -- so `[from, to)` is the period-to-date."""
    end = _instant(period["end_exclusive"])
    return {
        "key": period["key"], "label": period["label"],
        "from": _instant(period["start"]),
        "to": min(now, end) if period["is_current"] else end,
        "end_exclusive": end,
        "is_current": period["is_current"],
        "days_elapsed": period["days_elapsed"], "days_total": period["days_total"],
    }


def periods_view(active: ActiveCycle, now: datetime, history: int = DEFAULT_HISTORY) -> dict:
    """`GET /v1/budget-cycle/periods`: the cycle's periods as of `now` (UTC), see `periods`."""
    now = now.astimezone(timezone.utc) if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    found = periods(active.cycle, now.date(), history)
    return {
        "period_label": active.label,
        "periods": [_period_view(p, now) for p in found["periods"]],
        "next": _period_view(found["next"], now) if found["next"] else None,
    }


# --------------------------------------------------------------------------------- storage

def _cycle_from_row(row) -> Cycle:
    return validate(row.kind, row.recurrence, sprint_weeks=row.sprint_weeks,
                    anchor_date=row.anchor_date, anchor_number=row.anchor_number,
                    custom_end=row.custom_end)


def load_cycle(s: Session, catalog: QueryCatalog, org_id: str) -> Cycle | None:
    """The org's stored cycle, or `None` when none was saved (the default applies). A stored row
    that no longer validates (hand-edited, or written by a different version) is treated as no
    row and logged, so one bad row degrades to the default month instead of failing every page
    that reads a budget; the next PUT replaces it."""
    row = s.execute(catalog.get("manager", "get_budget_cycle"), {"org_id": org_id}).first()
    if row is None:
        return None
    try:
        return _cycle_from_row(row)
    except BadRequestError as exc:
        log.warning("budget_cycle.stored_row_invalid", org_id=org_id, kind=row.kind,
                    recurrence=row.recurrence, reason=exc.reason)
        return None


def active(s: Session, catalog: QueryCatalog, org_id: str, default_label: str) -> ActiveCycle:
    """Resolve the org's cycle ONCE per request. With no row: a recurring calendar month under
    `default_label` (`config.budget_period`), which must be the monthly label -- a default label
    that names another cadence has no cycle definition to go with it."""
    cycle = load_cycle(s, catalog, org_id)
    if cycle is not None:
        return ActiveCycle(cycle.label, cycle)
    if default_label != DEFAULT_LABEL:
        raise BadRequestError(
            f"unsupported budget period {default_label!r}; with no saved cycle expected "
            f"{DEFAULT_LABEL!r}")
    return ActiveCycle(DEFAULT_LABEL, DEFAULT_CYCLE)


def cycle_view(cycle: Cycle, updated_at: datetime | None = None) -> dict:
    """The cycle as the API reports it: its fields, the label budgets are stored under, and
    `updated_at` (None for the default, which was never saved)."""
    return {
        "kind": cycle.kind,
        "recurrence": cycle.recurrence,
        "sprint_weeks": cycle.sprint_weeks,
        "anchor_date": cycle.anchor_date,
        "anchor_number": cycle.anchor_number,
        "custom_end": cycle.custom_end,
        "period_label": cycle.label,
        "updated_at": updated_at,
    }


def get_cycle(s: Session, catalog: QueryCatalog, org_id: str) -> dict:
    """The org's cycle for display; the default month when none was saved."""
    cycle = load_cycle(s, catalog, org_id)
    if cycle is None:
        return cycle_view(DEFAULT_CYCLE)
    row = s.execute(catalog.get("manager", "get_budget_cycle"), {"org_id": org_id}).first()
    return cycle_view(cycle, row.updated_at)


def put_cycle(s: Session, catalog: QueryCatalog, org_id: str, cycle: Cycle) -> dict:
    """Replace the org's cycle (a full replace, not a patch) and report what that did to budget
    amounts, which are never converted:
      * a kind change swaps the label amounts are read under, so the old amounts stay stored but
        unread until the cadence is set back (`label_changed`);
      * a same-kind edit (sprint length, anchor, custom dates) keeps the label, so existing
        amounts now mean "per period" of the changed cycle.
    `note` says which, in words the UI can show as is; `None` when nothing changed."""
    previous = load_cycle(s, catalog, org_id) or DEFAULT_CYCLE
    s.execute(catalog.get("manager", "upsert_budget_cycle"), {
        "org_id": org_id, "kind": cycle.kind, "recurrence": cycle.recurrence,
        "sprint_weeks": cycle.sprint_weeks, "anchor_date": cycle.anchor_date,
        "anchor_number": cycle.anchor_number, "custom_end": cycle.custom_end})
    label_changed = previous.label != cycle.label
    if label_changed:
        note = (f"Budget amounts are not converted. Amounts saved for {previous.label!r} periods "
                f"are kept but not used; set budgets for {cycle.label!r} periods to see them.")
    elif previous != cycle:
        note = ("Existing budget amounts were kept and now apply per period of the changed "
                "cycle.")
    else:
        note = None
    log.info("budget_cycle.saved", org_id=org_id, kind=cycle.kind, recurrence=cycle.recurrence,
             label=cycle.label, previous_label=previous.label, label_changed=label_changed)
    return {**get_cycle(s, catalog, org_id), "previous_period_label": previous.label,
            "label_changed": label_changed, "note": note}
