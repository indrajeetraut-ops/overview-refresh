"""A small, sound `dashboard-forecast/1.0` document for the loader tests, built by hand so the
tests stay independent of the forecast package (mainline never imports `forecast.*`).

Shape: 4 dates, 2 periods (the first has one forecast day, the second two), a 21-point grid,
2 presets and (by default) 3 scopes: org, team, repo. Every number is a distinct small integer
so a payload that is copied, truncated or swapped between scopes is caught by equality."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from sqlalchemy import text

GRID = list(range(0, 101, 5))
DATES = ["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"]
PERIODS = [
    {"start": "2026-10-01", "end": "2026-10-31", "from": "2026-10-31"},   # 1 forecast day
    {"start": "2026-11-01", "end": "2026-11-02", "from": "2026-11-01"},   # 2 forecast days
]
PERIOD_LENGTHS = [1, 2]
PRESETS = [
    {"id": "baseline", "label": "Current trajectory", "group": None,
     "description": "No change.", "assumptions": ["fixed_mix_estimate"]},
    {"id": "usage-up-20", "label": "Usage grows 20%", "group": "Usage",
     "description": "Tokens up 20%.", "assumptions": ["scenario_constant_tokens"]},
]
DEFAULT_SCOPES = [
    ("org", "default", "acme.dev"),
    ("team", "development", "Development"),
    ("repo", "github:acme/auth-service", "acme/auth-service"),
]


def make_series_entry(seed: int, status: str = "complete") -> dict:
    """One scope x preset entry; `seed` makes every number distinct."""
    if status == "incomplete":
        return {"status": "incomplete"}
    base = 1000 * seed
    periods = []
    for p, length in enumerate(PERIOD_LENGTHS):
        b = base + 100 * p
        quantiles = [[b + 10 * d + g for g in range(len(GRID))] for d in range(length)]
        periods.append({
            "cumulative": {"expected": [b + 10 * d + 7 for d in range(length)],
                           "quantiles": quantiles},
            "total": {"expected": b + 10 * (length - 1) + 7, "quantiles": list(quantiles[-1])},
            "tokens_by_model": [{"model": "model-a", "expected": b + 1, "p90": b + 2}],
        })
    return {"status": status,
            "daily": {"expected": [base + i for i in range(len(DATES))],
                      "low": [base + i - 1 for i in range(len(DATES))],
                      "high": [base + i + 1 for i in range(len(DATES))]},
            "periods": periods}


def make_scope(scope_type: str, scope_id: str, name: str, index: int,
               presets=PRESETS, status: str = "complete") -> dict:
    return {"scope_type": scope_type, "scope_id": scope_id, "name": name,
            "series": {p["id"]: make_series_entry(10 * (index + 1) + j, status)
                       for j, p in enumerate(presets)}}


def make_export(scopes=None, *, generated_at: str = "2026-10-31T10:00:00Z") -> dict:
    scope_specs = DEFAULT_SCOPES if scopes is None else scopes
    return {
        "schema": "dashboard-forecast/1.0",
        "generated_at": generated_at,
        "timezone": "Asia/Kolkata",
        "origin": "2026-09-30",
        "data_through": "2026-10-30",
        "band": {"low": 10, "high": 90},
        "quantile_grid": list(GRID),
        "dates": list(DATES),
        "periods": copy.deepcopy(PERIODS),
        "presets": copy.deepcopy(PRESETS),
        "scopes": [make_scope(t, i, n, k) for k, (t, i, n) in enumerate(scope_specs)],
    }


def write_export(path: Path, doc: dict) -> Path:
    """Canonical JSON like the exporter's (sorted keys, no spaces, trailing newline)."""
    path.write_text(json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return path


def export_for_dataset(dataset_dir: Path, *, generated_at: str = "2026-10-31T10:00:00Z",
                       team_ids=None, repo_ids=None) -> dict:
    """An export whose scope ids match a generated dataset: its org, its teams, its repos."""
    manifest = json.loads((dataset_dir / "manifest.json").read_text())
    teams = [json.loads(line)["team_id"] for line in
             (dataset_dir / "directory_teams.jsonl").read_text().splitlines() if line.strip()]
    repos = [json.loads(line)["repo_id"] for line in
             (dataset_dir / "repositories.jsonl").read_text().splitlines() if line.strip()]
    specs = [("org", manifest["directory_org_id"], "org")]
    specs += [("team", t, f"team {t}") for t in (teams if team_ids is None else team_ids)]
    specs += [("repo", r, r) for r in (repos if repo_ids is None else repo_ids)]
    return make_export(specs, generated_at=generated_at)


def source_payload(doc: dict, scope_type: str, scope_id: str, preset_id: str) -> dict:
    """What the loader must store as `payload`: the source entry without `status`."""
    for scope in doc["scopes"]:
        if scope["scope_type"] == scope_type and scope["scope_id"] == scope_id:
            return {k: v for k, v in scope["series"][preset_id].items() if k != "status"}
    raise KeyError((scope_type, scope_id))


def dump_forecast_tables(engine) -> dict:
    """Every column of every row of both tables, ordered -- for before/after equality."""
    with engine.connect() as conn:
        runs = [tuple(r) for r in conn.execute(text(
            "SELECT run_id, origin, data_through, generated_at, meta, loaded_at "
            "FROM forecast_runs ORDER BY run_id"))]
        series = [tuple(r) for r in conn.execute(text(
            "SELECT run_id, scope_type, scope_id, preset_id, status, payload "
            "FROM forecast_scope_series ORDER BY run_id, scope_type, scope_id, preset_id"))]
    return {"runs": runs, "series": series}
