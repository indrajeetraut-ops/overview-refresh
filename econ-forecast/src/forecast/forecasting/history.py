"""Coverage-aware aggregation, startup qualification, and allocation windows."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
from collections import defaultdict
import numpy as np
from .dataset import PreparedDataset, ScopeMeta
from .errors import InputError


@dataclass
class SeriesHistory:
    scope: ScopeMeta
    model: str
    dates: list[date]
    values: np.ndarray
    categories: dict[tuple[str, str], float]
    category_window_start: date | None
    startup_start: date | None
    startup_source: str | None
    last_active: date | None
    status: str
    reasons: list[str]


def _days(start, end):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def build_histories(
    data: PreparedDataset,
    selected: list[ScopeMeta],
    origin: date,
    *,
    lookback: int,
    minimum: int,
    active_days: int,
    allocation_days: int,
):
    if origin < data.start or origin > data.end:
        raise InputError("origin lies outside declared dataset interval")
    out = []
    not_applicable = []
    for scope in selected:
        for model, agents in sorted(data.models[scope.org_id].items()):
            keys = frozenset(k for k in scope.keys if k[1] in agents)
            if not keys:
                not_applicable.append(
                    {
                        "org_id": scope.org_id,
                        "scope_type": scope.scope_type,
                        "scope_id": scope.scope_id,
                        "model": model,
                        "reason": "no coverage keys for model agents",
                    }
                )
                continue
            covered = []
            for day in _days(data.start, origin):
                if all(
                    data.coverage.get((day, scope.org_id, u, a)) == "complete"
                    for u, a in keys
                ):
                    covered.append(day)
            totals = defaultdict(float)
            by_cat = defaultdict(float)
            for (
                org,
                typ,
                sid,
                stored_model,
                day,
                agent,
                category,
            ), value in data.usage.items():
                if day > origin or (org, typ, sid, stored_model) != (
                    scope.org_id,
                    scope.scope_type,
                    scope.scope_id,
                    model,
                ):
                    continue
                totals[day] += value
                by_cat[(day, agent, category)] += value
            # Earliest covered consecutive-positive streak across all supplied pre-origin history.
            streak = []
            qualified = None
            covered_set = set(covered)
            for day in _days(data.start, origin):
                if day in covered_set and totals[day] > 0:
                    streak.append(day)
                else:
                    streak = []
                if len(streak) >= active_days and qualified is None:
                    qualified = streak[0]
            source = "observed" if qualified else None
            for ev in data.startup:
                if (
                    ev.get("org_id") == scope.org_id
                    and ev.get("scope_type") == scope.scope_type
                    and ev.get("scope_id") == scope.scope_id
                    and ev.get("model") == model
                ):
                    try:
                        a = date.fromisoformat(ev["positive_streak_start"])
                        b = date.fromisoformat(ev["positive_streak_end"])
                    except Exception as exc:
                        raise InputError("invalid startup evidence") from exc
                    if not ev.get("source") or b < a or (b - a).days + 1 < active_days:
                        raise InputError("invalid startup evidence")
                    if b > origin:
                        continue
                    if qualified is None or a < qualified:
                        qualified = a
                        source = ev["source"]
            # Continuous covered suffix ends at origin; never bridge the latest outage.
            suffix = []
            day = origin
            while day >= data.start and day in covered_set and len(suffix) < lookback:
                suffix.append(day)
                day -= timedelta(days=1)
            suffix.reverse()
            if qualified is not None:
                suffix = [d for d in suffix if d >= qualified]
            values = np.asarray([totals[d] for d in suffix], dtype=np.float64)
            positive = sorted(
                d for d, value in totals.items() if d <= origin and value > 0
            )
            status = "qualified"
            reasons = []
            if len(suffix) < minimum:
                status = "insufficient_history"
                reasons = [
                    "fewer than minimum consecutive covered days ending at origin"
                ]
            elif qualified is None:
                if not positive and len(suffix) >= minimum:
                    status = "inactive"
                    reasons = []
                else:
                    status = "insufficient_startup"
                    reasons = [
                        "no qualifying consecutive positive-day streak or valid prior evidence"
                    ]
            alloc_dates = suffix[-allocation_days:]
            recent = sum(totals[d] for d in alloc_dates)
            if status == "qualified" and recent == 0:
                status = "inactive"
            categories = defaultdict(float)
            for d in alloc_dates:
                for (rd, a, c), v in by_cat.items():
                    if rd == d:
                        categories[(a, c)] += v
            if status == "inactive":
                categories.clear()
            out.append(
                SeriesHistory(
                    scope,
                    model,
                    suffix,
                    values,
                    dict(categories),
                    alloc_dates[0] if alloc_dates else None,
                    qualified,
                    source,
                    max(positive) if positive else None,
                    status,
                    reasons,
                )
            )
    return out, not_applicable
