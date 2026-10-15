"""Public versioned QuerySpec entry point."""
from __future__ import annotations

from time import perf_counter

from .models import Family
from .observability import metric_add
from .query_config import load_query_config
from .query_engine import QueryEngine
from .query_models import QueryResult, QuerySpec

__all__ = ["query", "select"]


def query(family: Family, spec: QuerySpec | dict) -> QueryResult:
    started = perf_counter()
    try:
        parsed = spec if isinstance(spec, QuerySpec) else QuerySpec.model_validate(spec)
        result = QueryEngine(family, load_query_config()).execute(parsed)
    except Exception:
        metric_add(family.master.session_id, "causal_graph.query.calls", labels={"operation": "query", "outcome": "failure"})
        metric_add(family.master.session_id, "causal_graph.query.duration_ms", (perf_counter() - started) * 1000, metric_type="observation", labels={"operation": "query"})
        raise
    metric_add(family.master.session_id, "causal_graph.query.calls", labels={"operation": "query", "outcome": "success"})
    metric_add(family.master.session_id, "causal_graph.query.duration_ms", (perf_counter() - started) * 1000, metric_type="observation", labels={"operation": "query"})
    return result


def select(
    family: Family,
    node_type: str,
    *,
    where: str | None = None,
    scope: str | dict = "tree",
    fields: list[str] | None = None,
    order_by: list[dict] | None = None,
    limit: int | None = None,
    verbosity: str = "summary",
) -> QueryResult:
    """Readable node selection facade over the versioned QuerySpec engine."""
    if isinstance(scope, str):
        scope_spec = {"kind": scope} if scope in {"self", "tree", "subagents"} else {"kind": "session", "session_id": scope}
    else:
        scope_spec = scope
    spec = {
        "version": "1",
        "scope": scope_spec,
        "select": {"type": node_type, **({"where": where} if where is not None else {})},
        "return": {"kind": "nodes", "verbosity": verbosity, **({"fields": fields} if fields is not None else {})},
        **({"order_by": order_by} if order_by is not None else {}),
        **({"limit": limit} if limit is not None else {}),
    }
    started = perf_counter()
    try:
        result = query(family, spec)
    except Exception:
        metric_add(family.master.session_id, "causal_graph.query.calls", labels={"operation": "select", "outcome": "failure"})
        metric_add(family.master.session_id, "causal_graph.query.duration_ms", (perf_counter() - started) * 1000, metric_type="observation", labels={"operation": "select"})
        raise
    metric_add(family.master.session_id, "causal_graph.query.calls", labels={"operation": "select", "outcome": "success"})
    metric_add(family.master.session_id, "causal_graph.query.duration_ms", (perf_counter() - started) * 1000, metric_type="observation", labels={"operation": "select"})
    return result
