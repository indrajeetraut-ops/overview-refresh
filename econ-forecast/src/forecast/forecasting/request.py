"""Immutable forecast requests and calendar-period resolution."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal
from .errors import InputError


@dataclass(frozen=True)
class ForecastRequest:
    origin: date | None
    unit: Literal["week", "month"]
    offset: int = 1
    periods: int = 1

    def __post_init__(self):
        if self.origin is not None and type(self.origin) is not date:
            raise InputError("origin must be a calendar date")
        if self.unit not in ("week", "month"):
            raise InputError("unit must be week or month")
        if type(self.offset) is not int or self.offset < 1:
            raise InputError("offset must be a positive integer")
        if type(self.periods) is not int or self.periods < 1:
            raise InputError("periods must be a positive integer")


@dataclass(frozen=True)
class ResolvedWindow:
    requested_dates: tuple[date, ...]
    internal_dates: tuple[date, ...]
    periods: tuple[tuple[date, date], ...]


def _month_shift(day: date, n: int) -> date:
    index = day.year * 12 + day.month - 1 + n
    return date(index // 12, index % 12 + 1, 1)


def resolve_window(request: ForecastRequest) -> ResolvedWindow:
    if request.origin is None:
        raise InputError("origin must be resolved from the dataset before the calendar window")
    periods = []
    try:
        if request.unit == "week":
            base = request.origin + timedelta(days=(7 - request.origin.weekday()))
            first = base + timedelta(days=7 * (request.offset - 1))
            final_end = first + timedelta(days=7 * request.periods - 1)
        else:
            base = _month_shift(request.origin.replace(day=1), 1)
            first = _month_shift(base, request.offset - 1)
            final_end = _month_shift(first, request.periods) - timedelta(days=1)
    except (OverflowError, ValueError) as exc:
        raise InputError("requested calendar window exceeds supported dates") from exc
    if (final_end - request.origin).days > 366:
        raise InputError("internal forecast horizon exceeds 366 days")
    if request.unit == "week":
        for n in range(request.periods):
            start = first + timedelta(days=7 * n)
            periods.append((start, start + timedelta(days=6)))
    else:
        for n in range(request.periods):
            start = _month_shift(first, n)
            periods.append((start, _month_shift(start, 1) - timedelta(days=1)))
    start, end = periods[0][0], periods[-1][1]
    requested = tuple(start + timedelta(days=i) for i in range((end - start).days + 1))
    internal = tuple(
        request.origin + timedelta(days=i)
        for i in range(1, (end - request.origin).days + 1)
    )
    return ResolvedWindow(requested, internal, tuple(periods))
