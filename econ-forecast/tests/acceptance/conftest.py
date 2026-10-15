"""Independent black-box fixtures for the frozen forecasting contract."""
from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

import pytest
import yaml
import numpy as np


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write_jsonl(path: Path, rows: list[dict]) -> dict:
    payload = b"".join(_canonical_bytes(row) for row in rows)
    path.write_bytes(payload)
    return {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload), "rows": len(rows)}


def _write_json(path: Path, value: object) -> dict:
    payload = _canonical_bytes(value)
    path.write_bytes(payload)
    return {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}


def make_daily_dataset(
    root: Path,
    *,
    start: date = date(2026, 7, 1),
    days: int = 60,
    org_id: str = "org-a",
    users: tuple[str, ...] = ("u1",),
    models: tuple[tuple[str, str], ...] = (("model-a", "agent-a"),),
    token_fn=None,
    coverage_fn=None,
    startup_evidence: list[dict] | None = None,
) -> Path:
    """Create the neutral wire format without importing production helpers."""
    root.mkdir()
    token_fn = token_fn or (lambda day_index, model_index, user_index: (100 + day_index, 300, 50))
    coverage_fn = coverage_fn or (lambda day_index, model_index, user_index: "complete")
    usage: list[dict] = []
    coverage: list[dict] = []
    for day_index in range(days):
        current = start + timedelta(days=day_index)
        for model_index, (model, agent) in enumerate(models):
            for user_index, user in enumerate(users):
                coverage.append({"date": current.isoformat(), "org_id": org_id,
                                 "user_id": user, "agent_type": agent,
                                 "status": coverage_fn(day_index, model_index, user_index)})
                tokens = token_fn(day_index, model_index, user_index)
                if tokens is not None:
                    usage.append({"date": current.isoformat(), "org_id": org_id,
                                  "team_id": "team-a", "user_id": user,
                                  "project_id": "project-a", "agent_type": agent,
                                  "model": model, "billing_mode": "api",
                                  "tokens": _token_row(tokens)})
    keys = [{"user_id": user, "agent_type": agent}
            for _, agent in models for user in users]
    metadata = {"schema_version": 1, "organizations": [{
        "org_id": org_id, "name": "Organization A", "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=days - 1)).isoformat(), "timezone": "UTC",
        "models": [{"model": model, "agent_types": [agent]} for model, agent in models],
        "scopes": [
            {"type": "organization", "id": org_id, "name": "Organization A", "coverage_keys": keys},
            {"type": "team", "id": "team-a", "name": "Team A", "coverage_keys": keys},
            {"type": "project", "id": "project-a", "name": "Project A", "coverage_keys": keys},
        ], "startup_evidence": startup_evidence or [],
    }]}
    files = {
        "usage.jsonl": _write_jsonl(root / "usage.jsonl", usage),
        "coverage.jsonl": _write_jsonl(root / "coverage.jsonl", coverage),
        "metadata.json": _write_json(root / "metadata.json", metadata),
    }
    _write_json(root / "manifest.json", {"schema_version": 2, "operation": "daily",
                                          "status": "complete", "files": files})
    return root


def _token_row(tokens):
    """`token_fn` returns (input, cache_read, output) -- writes are zero -- or the five
    values (input, cache_write_5m, cache_write_1h, cache_read, output)."""
    if len(tokens) == 3:
        tokens = (tokens[0], 0, 0, tokens[1], tokens[2])
    return dict(zip(("input", "cache_write_5m", "cache_write_1h", "cache_read", "output"), tokens))


def write_forecast_config(path: Path, dataset: Path, *, scopes=None, **overrides) -> Path:
    config = {
        "schema_version": 1, "input": {"dataset_dir": str(dataset)},
        "default_org_id": "org-a", "scopes": scopes or [{"type": "organization"}],
        "history": {"lookback_days": 90, "minimum_days": 14, "minimum_active_days": 4},
        "model": {"method": "ets", "seasonal": "auto", "trend": "auto"},
        "allocation": {"lookback_days": 28},
        "uncertainty": {"paths": 32, "seed": 42, "max_negative_correction_ratio": 0.05},
        "runtime": {"workers": 1},
    }
    for section, values in overrides.items():
        if isinstance(values, dict) and section in config:
            config[section].update(values)
        else:
            config[section] = values
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def make_saved_run(root: Path, *, values=None, status="qualified") -> Path:
    """Write a minimal saved run directly from the frozen artifact schema."""
    root.mkdir(); (root / "paths").mkdir()
    values = np.asarray(values if values is not None else [[100., 140., 80.], [110., 90., 95.], [90., 130., 85.]], dtype="float64")
    identity_tuple = ["org-a", "team", "team-a", "model-a"]
    series_id = hashlib.sha256(json.dumps(identity_tuple, separators=(",", ":")).encode()).hexdigest()
    array_name = f"team-team-a__model-a__{series_id[:16]}.npy"
    np.save(root / "paths" / array_name, values, allow_pickle=False)
    index = {"schema_version": 1, "dates": ["2026-09-01", "2026-09-02", "2026-09-03"],
             "sample_ids": [0, 1, 2],
             "scopes": [{"org_id": "org-a", "scope_type": "team", "scope_id": "team-a",
                         "scope_name": "Team A", "status": "complete", "risk_ready": True,
                         "assumptions": ["uncalibrated_model_estimate"], "reasons": []}],
             "series": [{"series_id": series_id, "org_id": "org-a", "scope_type": "team",
                         "scope_id": "team-a", "scope_name": "Team A", "model": "model-a",
                         "status": status, "path": f"paths/{array_name}", "shape": [3, 3],
                         "dtype": "float64", "assumptions": [], "reasons": []}]}
    metas = {"paths/index.json": _write_json(root / "paths/index.json", index)}
    for name, value in {
        "request.json": {"schema_version": 1}, "allocations.json": {"schema_version": 2, "entries": []},
        "period_summary.json": {"schema_version": 1, "entries": []},
        "diagnostics.json": {"schema_version": 1},
    }.items():
        metas[name] = _write_json(root / name, value)
    (root / "resolved_config.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (root / "daily_expected.jsonl").write_bytes(b"")
    (root / "report.md").write_text("# Fixture\n", encoding="utf-8")
    for name in ("resolved_config.yaml", "daily_expected.jsonl", "report.md"):
        payload = (root / name).read_bytes()
        metas[name] = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
        if name.endswith(".jsonl"): metas[name]["rows"] = 0
    _write_json(root / "manifest.json", {"schema_version": 2, "operation": "forecast",
        "completion": "complete", "status": "complete", "run_id": "fixture", "created_at": "2026-09-13T00:00:00Z",
        "versions": {}, "input_sources": {}, "counts": {"requested_scopes": 1, "completed_scopes": 1, "rejected_scopes": 0},
        "report_path": "report.md", "files": {**metas,
        f"paths/{array_name}": {"sha256": hashlib.sha256((root/'paths'/array_name).read_bytes()).hexdigest(),
                                "bytes": (root/'paths'/array_name).stat().st_size}}})
    return root


def make_resource_dataset(root: Path) -> Path:
    """Frozen 100-series/90-history benchmark: exactly 9,000 rows per JSONL file."""
    root.mkdir(); start = date(2026, 3, 5); usage=[]; coverage=[]; models=[]; keys=[]
    for model_index in range(100):
        model=f"model-{model_index:03}"; agent=f"agent-{model_index:03}"; user=f"user-{model_index:03}"
        models.append({"model":model,"agent_types":[agent]}); keys.append({"user_id":user,"agent_type":agent})
        for day_index in range(90):
            current=start+timedelta(days=day_index)
            # Positive weekly signal plus deterministic, nonperiodic integer perturbation.
            total=10_000 + 97*(day_index % 7) + ((day_index*day_index + 17*model_index) % 101)
            usage.append({"date":current.isoformat(),"org_id":"benchmark-org","team_id":None,
                "user_id":user,"project_id":None,"agent_type":agent,"model":model,"billing_mode":"api",
                "tokens":{"input":total//5,"cache_write_5m":0,"cache_write_1h":0,
                          "cache_read":3*total//5,"output":total-total//5-3*total//5}})
            coverage.append({"date":current.isoformat(),"org_id":"benchmark-org","user_id":user,
                             "agent_type":agent,"status":"complete"})
    metadata={"schema_version":1,"organizations":[{"org_id":"benchmark-org","name":"Benchmark",
        "start_date":start.isoformat(),"end_date":"2026-06-02","timezone":"UTC","models":models,
        "scopes":[{"type":"organization","id":"benchmark-org","name":"Benchmark","coverage_keys":keys}],
        "startup_evidence":[]}]}
    files={"usage.jsonl":_write_jsonl(root/"usage.jsonl",usage),
           "coverage.jsonl":_write_jsonl(root/"coverage.jsonl",coverage),
           "metadata.json":_write_json(root/"metadata.json",metadata)}
    _write_json(root/"manifest.json",{"schema_version":2,"operation":"daily","status":"complete","files":files})
    return root


@pytest.fixture
def neutral_dataset(tmp_path):
    return make_daily_dataset(tmp_path / "daily")


@pytest.fixture
def forecast_config(tmp_path, neutral_dataset):
    return write_forecast_config(tmp_path / "forecast.yaml", neutral_dataset)


@pytest.fixture
def daily_dataset_factory():
    return make_daily_dataset


@pytest.fixture
def forecast_config_factory():
    return write_forecast_config


@pytest.fixture
def saved_run_factory():
    return make_saved_run


@pytest.fixture
def resource_dataset_factory():
    return make_resource_dataset
