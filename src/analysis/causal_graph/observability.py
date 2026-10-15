"""Retained process-local lifecycle reports and causal-graph metric snapshots."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
from threading import RLock
from typing import Any

_LOCK = RLock()
_REPORTS: dict[str, dict[str, Any]] = {}
_METRICS: dict[str, dict[tuple[str, str, str], dict[str, Any]]] = {}


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _labels_key(labels: dict[str, Any]) -> str:
    return json.dumps(labels, sort_keys=True, separators=(",", ":"), default=str)


def metric_add(
    master_session_id: str,
    name: str,
    value: float = 1,
    *,
    metric_type: str = "counter",
    labels: dict[str, Any] | None = None,
) -> None:
    labels = labels or {}
    key = (name, metric_type, _labels_key(labels))
    with _LOCK:
        series = _METRICS.setdefault(master_session_id, {}).setdefault(
            key,
            {"name": name, "type": metric_type, "labels": deepcopy(labels), "value": 0, "updated_at": utc_now()},
        )
        series["value"] += value
        series["updated_at"] = utc_now()


def metric_set(master_session_id: str, name: str, value: float, *, labels: dict[str, Any] | None = None) -> None:
    labels = labels or {}
    key = (name, "gauge", _labels_key(labels))
    with _LOCK:
        _METRICS.setdefault(master_session_id, {})[key] = {
            "name": name, "type": "gauge", "labels": deepcopy(labels),
            "value": value, "updated_at": utc_now(),
        }


def record_lifecycle(
    master_session_id: str,
    operation: str,
    *,
    started_at: str,
    completed_at: str,
    duration_ms: float,
    outcome: str,
    snapshot: dict[str, Any],
    affected_session_ids: list[str],
    error_code: str | None = None,
) -> dict[str, Any]:
    with _LOCK:
        now = completed_at
        report = _REPORTS.setdefault(
            master_session_id,
            {
                "version": "1", "master_session_id": master_session_id,
                "created_at": started_at, "last_updated_at": now,
                "state": "unloaded", "loaded_session_ids": [], "loaded_session_count": 0,
                "loaded_node_count": 0, "referenced_session_ids": [],
                "unresolved_session_ids": [], "operations": [],
            },
        )
        for field in (
            "state", "loaded_session_ids", "loaded_session_count", "loaded_node_count",
            "referenced_session_ids", "unresolved_session_ids",
        ):
            if field in snapshot:
                report[field] = deepcopy(snapshot[field])
        operation_record = {
            "operation": operation, "started_at": started_at, "completed_at": completed_at,
            "duration_ms": duration_ms, "outcome": outcome,
            "affected_session_ids": sorted(affected_session_ids),
        }
        if error_code is not None:
            operation_record["error_code"] = error_code
        report["operations"].append(operation_record)
        report["last_updated_at"] = now

        metric_add(master_session_id, "causal_graph.lifecycle.calls", labels={"operation": operation, "outcome": outcome})
        metric_add(master_session_id, "causal_graph.lifecycle.duration_ms", duration_ms, metric_type="observation", labels={"operation": operation})
        if error_code:
            metric_add(master_session_id, "causal_graph.failures", labels={"code": error_code, "operation": operation})
        metric_set(master_session_id, "causal_graph.loaded.sessions", snapshot.get("loaded_session_count", 0))
        metric_set(master_session_id, "causal_graph.loaded.nodes", snapshot.get("loaded_node_count", 0))
        metric_set(master_session_id, "causal_graph.unresolved.references", len(snapshot.get("unresolved_session_ids", [])))
        return deepcopy(report)


def get_load_report(master_session_id: str | None = None) -> dict[str, Any]:
    """Return one retained report, or the process-wide map when no master is supplied."""
    with _LOCK:
        if master_session_id is None:
            return deepcopy(_REPORTS)
        if master_session_id not in _REPORTS:
            from .context import GraphContextError
            raise GraphContextError(
                "master_session_not_found", "no retained load report exists for the master",
                master_session_id=master_session_id,
            )
        return deepcopy(_REPORTS[master_session_id])


def get_metric(master_session_id: str) -> dict[str, Any]:
    """Return retained metric series for exactly one master session."""
    with _LOCK:
        if master_session_id not in _METRICS:
            from .context import GraphContextError
            raise GraphContextError(
                "master_session_not_found", "no retained metrics exist for the master",
                master_session_id=master_session_id,
            )
        series = sorted(_METRICS[master_session_id].values(), key=lambda item: (item["name"], _labels_key(item["labels"])))
        return {"version": "1", "master_session_id": master_session_id, "generated_at": utc_now(), "metrics": deepcopy(series)}
