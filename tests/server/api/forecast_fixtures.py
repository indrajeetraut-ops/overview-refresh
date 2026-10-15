"""Hand-built `dashboard-forecast/1.0` exports and database seeding for the T6 API tests.

Mainline never imports `forecast.*`, so the exports are plain dicts built here. Unlike the
storage tests' tiny builder, this one is parametric: any `data_through`, any timezone, any
scopes and per-scope daily level, and every number follows a simple, hand-checkable rule:

* a scope has a `base` (usd_micros per forecast day) and each preset a `factor`;
* the cumulative row of forecast day `k` (0-based) at grid position `i` is
  `(k + 1) * base * factor * (100 + 5 * i) // 100` (non-decreasing across `i` and `k`);
* `cumulative.expected[k]` is the mean: 110% of that row's median (`i = 10`, 150% of
  `(k + 1) * base * factor`), i.e. a right-skewed distribution whose mean is above its p50;
* `total` is the last cumulative entry, and every day's `daily` mean is `base * factor`.
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from server.directory import DirectoryRecord, ExternalKey, RoleGrant, TeamMembership, TeamRecord, sync
from server.storage import session_scope
from server.storage.forecast_load import parse_export, replace_run
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo

GRID = list(range(0, 101, 5))
PRESETS = [
    {"id": "baseline", "label": "Current trajectory", "group": None,
     "description": "No change.", "assumptions": ["fixed_mix_estimate"], "factor": 100},
    {"id": "usage-up-20", "label": "Usage grows 20%", "group": "Usage",
     "description": "Tokens up 20%.", "assumptions": ["scenario_constant_tokens"], "factor": 120},
    {"id": "opus-to-sonnet-30", "label": "Move 30% of Opus work to Sonnet", "group": "Model mix",
     "description": "30% of Opus on Sonnet.", "assumptions": ["scenario_constant_tokens"],
     "factor": 90},
]
#: (scope_type, scope_id, name, base usd_micros per forecast day)
DEFAULT_SCOPES = [
    ("org", "default", "acme.dev", 300_000_000),
    ("team", "platform", "Platform", 100_000_000),
    ("team", "qa", "QA", 40_000_000),
    ("repo", "github:acme/auth-service", "acme/auth-service", 50_000_000),
]
MIDDLE = 10      # grid position of the 50th percentile


def month_end(year: int, month: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def periods_for(data_through: date) -> tuple[date, list[dict]]:
    """(origin, the two calendar months of the run) by the export's rule: origin is the last day
    of the month before the data ends, or the data's last day when it is a month end."""
    if data_through == month_end(data_through.year, data_through.month):
        origin = data_through
        first = data_through + timedelta(days=1)
    else:
        first = data_through.replace(day=1)
        origin = first - timedelta(days=1)
    second = month_end(first.year, first.month) + timedelta(days=1)
    out = []
    for start in (first, second):
        end = month_end(start.year, start.month)
        from_ = data_through + timedelta(days=1) if start <= data_through < end else start
        out.append({"start": start.isoformat(), "end": end.isoformat(), "from": from_.isoformat()})
    return origin, out


def _row(base: int, factor: int, k: int) -> list[int]:
    return [(k + 1) * base * factor * (100 + 5 * i) // 10_000 for i in range(len(GRID))]


def make_series_entry(base: int, factor: int, dates: list[str], periods: list[dict],
                      status: str = "complete") -> dict:
    if status == "incomplete":
        return {"status": "incomplete"}
    index = {d: i for i, d in enumerate(dates)}
    entry_periods = []
    for p in periods:
        length = index[p["end"]] - index[p["from"]] + 1
        rows = [_row(base, factor, k) for k in range(length)]
        expected = [r[MIDDLE] * 11 // 10 for r in rows]       # mean 10% above the median
        entry_periods.append({
            "cumulative": {"expected": expected, "quantiles": rows},
            "total": {"expected": expected[-1], "quantiles": list(rows[-1])},
            "tokens_by_model": [
                {"model": "model-a", "expected": 1000 * length * factor // 100, "p90": 1500 * length},
                {"model": "model-b", "expected": 10 * length, "p90": 20 * length},
            ],
        })
    level = base * factor // 100
    return {"status": status,
            "daily": {"expected": [level for _ in dates], "low": [level // 2 for _ in dates],
                      "high": [level * 2 for _ in dates]},
            "periods": entry_periods}


def make_export(*, data_through: str = "2026-10-08", tz: str = "Asia/Kolkata", scopes=None,
                presets=None, statuses: dict | None = None,
                generated_at: str = "2026-10-08T10:00:00Z") -> dict:
    """`statuses` maps scope_id -> status for scopes that should not be `complete`."""
    through = date.fromisoformat(data_through)
    origin, periods = periods_for(through)
    first = date.fromisoformat(periods[0]["start"])
    if first <= origin:                 # month end: the first period starts after the origin
        first = origin + timedelta(days=1)
    last = date.fromisoformat(periods[-1]["end"])
    dates = [(first + timedelta(days=k)).isoformat() for k in range((last - first).days + 1)]
    preset_specs = copy.deepcopy(PRESETS if presets is None else presets)
    statuses = statuses or {}
    out_scopes = []
    for stype, sid, name, base in (DEFAULT_SCOPES if scopes is None else scopes):
        out_scopes.append({
            "scope_type": stype, "scope_id": sid, "name": name,
            "series": {p["id"]: make_series_entry(base, p["factor"], dates, periods,
                                                  statuses.get(sid, "complete"))
                       for p in preset_specs}})
    return {
        "schema": "dashboard-forecast/1.0", "generated_at": generated_at, "timezone": tz,
        "origin": origin.isoformat(), "data_through": data_through,
        "band": {"low": 10, "high": 90}, "quantile_grid": list(GRID), "dates": dates,
        "periods": periods,
        "presets": [{k: v for k, v in p.items() if k != "factor"} for p in preset_specs],
        "scopes": out_scopes,
    }


def load_export(doc: dict, tmp_path: Path, catalog) -> str:
    """Write `doc` to a file, run the real loader's `parse_export` and `replace_run`; returns
    the run id."""
    path = tmp_path / "forecast_export.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    parsed = parse_export(path)
    with session_scope() as s:
        replace_run(s, catalog, parsed)
    return parsed.run_id


# ------------------------------------------------------------------------------ directory + sessions

def seed_directory(s, catalog) -> None:
    """admin1 (org admin), lead1 (leads platform), dev1 (platform), dev2 (qa)."""
    sync(s, catalog, [
        DirectoryRecord(external_keys=(ExternalKey("csv", "admin1"),), email="admin1@x.com",
                        roles=(RoleGrant("admin", "org", "default"),)),
        DirectoryRecord(external_keys=(ExternalKey("csv", "lead1"),), email="lead1@x.com",
                        teams=(TeamMembership("platform"),),
                        roles=(RoleGrant("lead", "team", "platform"),)),
        DirectoryRecord(external_keys=(ExternalKey("csv", "dev1"),), email="dev1@x.com",
                        teams=(TeamMembership("platform"),)),
        DirectoryRecord(external_keys=(ExternalKey("csv", "dev2"),), email="dev2@x.com",
                        teams=(TeamMembership("qa"),)),
    ], teams=[TeamRecord("platform", "default", "Platform"), TeamRecord("qa", "default", "QA")])


def utc(year, month, day, hour=0, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def add_session(s, sid: str, user_id: str, cost: int, started_at: datetime, *,
                repo_id: str | None = None, ended_at: datetime | None = None,
                parent: str | None = None, datasource: str = "both") -> None:
    ended = ended_at or started_at + timedelta(minutes=5)
    s.add(SessionRow(session_id=sid, producer_id="p", org_id="default", user_id=user_id,
                     agent="claude_code", status="ended", started_at=started_at,
                     last_event_at=ended, ended_at=ended, parent_session_id=parent,
                     signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfo(session_id=sid, created_at=started_at, updated_at=started_at, models={},
                      tools={}, files={}, skills=[], subagents={}, pr_list=[], turns=[],
                      turn_count=0, long_turn_count=0, info={}, otel={}, datasource=datasource,
                      cost_effective_usd_micros=cost, repo_id=repo_id,
                      repo_id_provenance="remote" if repo_id else None))
