"""End-to-end black-box acceptance for the dated known-event overlay (design §7).

Builds a small neutral dataset with two Teams and two Projects, one of which maps
cleanly to a single Team and one of which (like the current synthetic data) spans two
Teams, so scope precedence (direct match / project->team fallback / skip-to-org on
ambiguity) and the agent axis can all be exercised end to end through `run_forecast`.

Local to this file per the implementation brief: the shared `make_daily_dataset` fixture
in conftest.py hardcodes a single team/project pair, so a bespoke builder lives here
rather than changing that shared default.
"""

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from forecast.forecasting.artifacts import load_run
from forecast.forecasting.errors import InputError
from forecast.forecasting.request import ForecastRequest
from forecast.forecasting.runner import run_forecast


def _canonical_bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write_jsonl(path: Path, rows: list[dict]) -> dict:
    payload = b"".join(_canonical_bytes(row) for row in rows)
    path.write_bytes(payload)
    return {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload), "rows": len(rows)}


def _write_json(path: Path, value: object) -> dict:
    payload = _canonical_bytes(value)
    path.write_bytes(payload)
    return {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}


ORG = "org-a"
START = date(2026, 7, 1)
DAYS = 60
MODELS = (("model-a", "agent-a"), ("model-b", "agent-b"))
# user -> (team_id, team_name, project_id)
USERS = {
    "u1": ("team-a", "Team A", "project-shared"),
    "u2": ("team-a", "Team A", "project-solo"),
    "u3": ("team-b", "Team B", "project-shared"),
}
TOKENS = (100, 0, 0)  # constant, all-input tokens: exact-constant analytic ETS branch


def make_known_events_dataset(root: Path) -> Path:
    """Two Teams, two Projects: "project-solo" is used only by Team A ("u2"); the
    (design-violating) "project-shared" is used by both Team A ("u1") and Team B ("u3"),
    so it must resolve to > 1 team and fall back straight to org (decision 2).
    """
    root.mkdir()
    usage: list[dict] = []
    coverage: list[dict] = []
    for day_index in range(DAYS):
        current = START + timedelta(days=day_index)
        for user, (team_id, _team_name, project_id) in USERS.items():
            for model, agent in MODELS:
                coverage.append({"date": current.isoformat(), "org_id": ORG,
                                  "user_id": user, "agent_type": agent, "status": "complete"})
                usage.append({"date": current.isoformat(), "org_id": ORG, "team_id": team_id,
                              "user_id": user, "project_id": project_id, "agent_type": agent,
                              "model": model, "billing_mode": "api",
                              "tokens": {"input": TOKENS[0], "cache_write_5m": 0,
                                         "cache_write_1h": 0, "cache_read": TOKENS[1],
                                         "output": TOKENS[2]}})
    all_keys = [{"user_id": u, "agent_type": a} for u in USERS for _m, a in MODELS]
    team_keys = {
        team_id: [{"user_id": u, "agent_type": a}
                  for u, (t, _n, _p) in USERS.items() if t == team_id
                  for _m, a in MODELS]
        for team_id in {t for t, _n, _p in USERS.values()}
    }
    project_keys = {
        project_id: [{"user_id": u, "agent_type": a}
                     for u, (_t, _n, p) in USERS.items() if p == project_id
                     for _m, a in MODELS]
        for project_id in {p for _t, _n, p in USERS.values()}
    }
    metadata = {"schema_version": 1, "organizations": [{
        "org_id": ORG, "name": "Organization A", "start_date": START.isoformat(),
        "end_date": (START + timedelta(days=DAYS - 1)).isoformat(), "timezone": "UTC",
        "models": [{"model": model, "agent_types": [agent]} for model, agent in MODELS],
        "scopes": [
            {"type": "organization", "id": ORG, "name": ORG, "coverage_keys": all_keys},
            {"type": "team", "id": "team-a", "name": "Team A", "coverage_keys": team_keys["team-a"]},
            {"type": "team", "id": "team-b", "name": "Team B", "coverage_keys": team_keys["team-b"]},
            {"type": "project", "id": "project-solo", "name": "Project Solo",
             "coverage_keys": project_keys["project-solo"]},
            {"type": "project", "id": "project-shared", "name": "Project Shared",
             "coverage_keys": project_keys["project-shared"]},
        ], "startup_evidence": [],
    }]}
    files = {
        "usage.jsonl": _write_jsonl(root / "usage.jsonl", usage),
        "coverage.jsonl": _write_jsonl(root / "coverage.jsonl", coverage),
        "metadata.json": _write_json(root / "metadata.json", metadata),
    }
    _write_json(root / "manifest.json", {"schema_version": 2, "operation": "daily",
                                          "status": "complete", "files": files})
    return root


def make_known_events_config(path: Path, dataset: Path, *, known_events: dict) -> Path:
    config = {
        "schema_version": 1, "input": {"dataset_dir": str(dataset)},
        "default_org_id": ORG,
        "scopes": [
            {"type": "organization"},
            {"type": "team", "name": "Team A"},
            {"type": "team", "name": "Team B"},
            {"type": "project", "name": "Project Solo"},
            {"type": "project", "name": "Project Shared"},
        ],
        "history": {"lookback_days": 90, "minimum_days": 14, "minimum_active_days": 4},
        "model": {"method": "ets", "seasonal": "auto", "trend": "auto"},
        "allocation": {"lookback_days": 28},
        "uncertainty": {"paths": 500, "seed": 42, "max_negative_correction_ratio": 0.05},
        "known_events": known_events,
        "runtime": {"workers": 1},
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


REQUEST = ForecastRequest(date(2026, 8, 29), "month")  # requested horizon: Sept 2026
EVENT_WINDOW = {"start_date": "2026-09-05", "end_date": "2026-09-10"}
KNOWN_EVENTS = {
    "org": {**EVENT_WINDOW, "multiplier": 1.5, "spread": 0.0},
    "teams": {"Team A": {**EVENT_WINDOW, "multiplier": 2.0, "spread": 0.0}},
    "projects": {},
    "agents": {"agent-a": {**EVENT_WINDOW, "multiplier": 3.0, "spread": 0.2}},
}


def _entries(run, scope_type, scope_id, model=None):
    return [
        e for e in run.index["series"]
        if e["scope_type"] == scope_type and e["scope_id"] == scope_id
        and (model is None or e["model"] == model)
    ]


def test_scope_precedence_and_agent_axis_end_to_end(tmp_path):
    dataset = make_known_events_dataset(tmp_path / "daily")
    config = make_known_events_config(tmp_path / "config.yaml", dataset, known_events=KNOWN_EVENTS)
    output = tmp_path / "out"
    result = run_forecast(config, REQUEST, output)
    assert result.status == "complete", result.diagnostics

    run = load_run(output)
    diag = json.loads((output / "diagnostics.json").read_text())
    applied = {(e["level"], e["name"]): e for e in diag["known_events"]}
    assert set(applied) == {("org", "org"), ("team", "Team A"), ("agent", "agent-a")}
    assert applied[("org", "org")]["multiplier"] == 1.5
    assert applied[("agent", "agent-a")]["factor_min"] >= 3.0 - 0.2 - 1e-9
    assert applied[("agent", "agent-a")]["factor_max"] <= 3.0 + 0.2 + 1e-9

    window_idx = [i for i, d in enumerate(run.index["dates"])
                  if "2026-09-05" <= d <= "2026-09-10"]
    outside_idx = [i for i, d in enumerate(run.index["dates"]) if d == "2026-09-01"]

    def path_for(scope_type, scope_id, model):
        entry = _entries(run, scope_type, scope_id, model)[0]
        return np.asarray(run.load_paths(entry))

    baseline = 100.0  # constant tokens/day per user/model; the exact-constant analytic
    # ETS branch (design §3, numerical preflight) fits with zero residuals, so
    # pre-overlay values are exactly the historical constant on every day/path.

    # Team A's own series uses its direct team event (x2.0), not the org event (x1.5).
    team_a = path_for("team", "team-a", "model-b")  # agent-b: no agent-axis interference
    np.testing.assert_allclose(team_a[window_idx], baseline * 2.0 * 2, rtol=1e-9)  # 2 users
    np.testing.assert_allclose(team_a[outside_idx], baseline * 2, rtol=1e-9)

    # project-solo (only Team A's u2) falls back to Team A's event, same x2.0 factor.
    solo = path_for("project", "project-solo", "model-b")
    np.testing.assert_allclose(solo[window_idx], baseline * 2.0, rtol=1e-9)
    np.testing.assert_allclose(solo[outside_idx], baseline, rtol=1e-9)

    # project-shared spans Team A and Team B -> ambiguous -> skip to org (x1.5), not x2.0.
    shared = path_for("project", "project-shared", "model-b")
    np.testing.assert_allclose(shared[window_idx], baseline * 1.5 * 2, rtol=1e-9)  # u1+u3
    np.testing.assert_allclose(shared[outside_idx], baseline * 2, rtol=1e-9)

    # Team B has no declared team event -> falls back to org (x1.5).
    team_b = path_for("team", "team-b", "model-b")
    np.testing.assert_allclose(team_b[window_idx], baseline * 1.5, rtol=1e-9)

    # Organization's own model-b series: org event only (x1.5), summed over all 3 users.
    org_b = path_for("organization", ORG, "model-b")
    np.testing.assert_allclose(org_b[window_idx], baseline * 1.5 * 3, rtol=1e-9)

    # Organization's model-a series additionally composes the agent-a event on top of
    # whichever scope factor applies to each contributor -> org(x1.5) x agent-a fans in
    # [2.8, 3.2] per path, but every path is internally coherent (same factor every day).
    org_a = path_for("organization", ORG, "model-a")
    ratio = org_a[window_idx[0]] / (baseline * 1.5 * 3)
    assert ratio.min() >= 2.8 - 1e-6 and ratio.max() <= 3.2 + 1e-6
    assert ratio.std() > 0  # spread=0.2 fans the agent-a band
    for row in org_a[window_idx[1:]]:
        np.testing.assert_allclose(row / (baseline * 1.5 * 3), ratio)  # coherent per path

    # known_event_applied assumption tag reaches exactly the modified qualified series.
    tagged = {(e["scope_type"], e["scope_id"], e["model"])
              for e in run.index["series"] if "known_event_applied" in e["assumptions"]}
    assert tagged == {
        ("organization", ORG, "model-a"), ("organization", ORG, "model-b"),
        ("team", "team-a", "model-a"), ("team", "team-a", "model-b"),
        ("team", "team-b", "model-a"), ("team", "team-b", "model-b"),
        ("project", "project-solo", "model-a"), ("project", "project-solo", "model-b"),
        ("project", "project-shared", "model-a"), ("project", "project-shared", "model-b"),
    }


def test_determinism_same_seed_and_data_yields_identical_paths(tmp_path):
    dataset = make_known_events_dataset(tmp_path / "daily")
    config = make_known_events_config(tmp_path / "config.yaml", dataset, known_events=KNOWN_EVENTS)
    run_forecast(config, REQUEST, tmp_path / "out-one")
    run_forecast(config, REQUEST, tmp_path / "out-two")
    one, two = load_run(tmp_path / "out-one"), load_run(tmp_path / "out-two")
    for entry in one.index["series"]:
        if entry["path"] is None:
            continue
        match = _entries(two, entry["scope_type"], entry["scope_id"], entry["model"])[0]
        np.testing.assert_array_equal(
            np.asarray(one.load_paths(entry)), np.asarray(two.load_paths(match))
        )


def test_validate_only_rejects_unknown_team_name_before_any_output(tmp_path):
    dataset = make_known_events_dataset(tmp_path / "daily")
    bad_events = {"org": None, "teams": {"Typo Team": {**EVENT_WINDOW, "multiplier": 1.0, "spread": 0.0}},
                  "projects": {}, "agents": {}}
    config = make_known_events_config(tmp_path / "config.yaml", dataset, known_events=bad_events)
    with pytest.raises(InputError, match="unknown team"):
        run_forecast(config, REQUEST, validate_only=True)


def test_validate_only_rejects_event_window_outside_horizon(tmp_path):
    dataset = make_known_events_dataset(tmp_path / "daily")
    bad_events = {"org": {"start_date": "2026-06-01", "end_date": "2026-06-05",
                          "multiplier": 1.2, "spread": 0.0},
                  "teams": {}, "projects": {}, "agents": {}}
    config = make_known_events_config(tmp_path / "config.yaml", dataset, known_events=bad_events)
    with pytest.raises(InputError, match="forecast horizon"):
        run_forecast(config, REQUEST, validate_only=True)
