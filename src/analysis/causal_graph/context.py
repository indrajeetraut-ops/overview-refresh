"""Source-neutral in-process lifecycle for causal-graph families."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from functools import wraps
from threading import RLock
from time import perf_counter
from typing import Any, Callable

import structlog

from .models import Family, Graph, NodeType, SCHEMA_VERSION
from .observability import metric_add, record_lifecycle, utc_now

__all__ = ["GraphContext", "GraphContextError", "get_graph_context"]

_log = structlog.get_logger(module="analysis.causal_graph.context")
_REGISTRY_LOCK = RLock()
_ACTIVE_CONTEXTS: dict[str, "GraphContext"] = {}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _schema_version_tuple(value: object) -> tuple[int, int, int] | None:
    """Return a strict three-part numeric graph schema version."""
    if not isinstance(value, str):
        return None
    parts = value.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1]), int(parts[2])


def _schema_version_supported(value: object) -> bool:
    """Accept current and older envelopes in the current major, never future schemas."""
    actual = _schema_version_tuple(value)
    current = _schema_version_tuple(SCHEMA_VERSION)
    return actual is not None and current is not None and actual[0] == current[0] and actual <= current


class GraphContextError(ValueError):
    """Lifecycle failure with a stable machine-readable envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        master_session_id: str | None = None,
        session_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.timestamp = _utc_now()
        self.master_session_id = master_session_id
        self.session_id = session_id
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "1",
            "code": self.code,
            "message": str(self),
            "timestamp": self.timestamp,
            "master_session_id": self.master_session_id,
            "session_id": self.session_id,
            "details": deepcopy(self.details),
        }


def _graph_from_dict(value: dict[str, Any]) -> Graph:
    if not isinstance(value, dict):
        raise GraphContextError("graph_validation_failed", "graph must be a dictionary")
    required = {"schema_version", "session_id", "parent_session_id", "vendor", "source", "built", "meta", "nodes", "roots"}
    missing = sorted(required - value.keys())
    if missing:
        raise GraphContextError("graph_validation_failed", "graph is missing required fields", details={"missing": missing})
    if not _schema_version_supported(value.get("schema_version")):
        raise GraphContextError(
            "graph_validation_failed", "graph schema version is unsupported",
            session_id=value.get("session_id") if isinstance(value.get("session_id"), str) else None,
            details={
                "supported_major": _schema_version_tuple(SCHEMA_VERSION)[0],
                "maximum": SCHEMA_VERSION,
                "actual": value.get("schema_version"),
            },
        )
    if not isinstance(value.get("session_id"), str) or not value["session_id"]:
        raise GraphContextError("graph_validation_failed", "session_id must be a non-empty string")
    if value.get("parent_session_id") is not None and not isinstance(value["parent_session_id"], str):
        raise GraphContextError("graph_validation_failed", "parent_session_id must be a string or null", session_id=value["session_id"])
    if not isinstance(value.get("nodes"), list) or not isinstance(value.get("roots"), list):
        raise GraphContextError("graph_validation_failed", "nodes and roots must be arrays", session_id=value["session_id"])
    node_ids = [node.get("id") for node in value["nodes"] if isinstance(node, dict)]
    if len(node_ids) != len(value["nodes"]) or len(node_ids) != len(set(node_ids)):
        raise GraphContextError("graph_validation_failed", "node IDs must be present and unique", session_id=value["session_id"])
    try:
        graph = Graph.from_dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise GraphContextError("graph_validation_failed", str(exc), session_id=value["session_id"]) from exc
    if len(graph.roots) != len(set(graph.roots)) or any(root not in graph.nodes for root in graph.roots):
        raise GraphContextError("graph_validation_failed", "roots must be unique existing node IDs", session_id=graph.session_id)
    return graph


def _spawn_refs(graph: Graph) -> set[str]:
    return {
        child
        for node in graph.nodes.values()
        if node.type is NodeType.SPAWN
        and isinstance((child := node.refs.get("child_session_id")), str)
        and child
    }


def get_graph_context(session_id: str) -> "GraphContext | None":
    """Return the active context owning a universal session ID."""
    with _REGISTRY_LOCK:
        return _ACTIVE_CONTEXTS.get(session_id)


def _observed(operation: str):
    """Record every public lifecycle attempt and return the retained report snapshot."""
    def decorate(function):
        @wraps(function)
        def wrapped(self: "GraphContext", *args, **kwargs):
            started_at = utc_now()
            started = perf_counter()
            before_ids = {graph.session_id for graph in self._family.all_graphs()} if self._family else set()
            before_nodes = sum(len(graph.nodes) for graph in self._family.all_graphs()) if self._family else 0
            candidate = self.master_session_id
            if candidate is None and operation == "load_self" and args and isinstance(args[0], dict):
                value = args[0].get("session_id")
                candidate = value if isinstance(value, str) and value else None
            try:
                snapshot = function(self, *args, **kwargs)
            except GraphContextError as exc:
                master = self.master_session_id or exc.master_session_id or candidate
                if master:
                    current = self._snapshot() if self._family else {
                        "state": "unloaded", "loaded_session_ids": [], "loaded_session_count": 0,
                        "loaded_node_count": 0, "referenced_session_ids": [], "unresolved_session_ids": [],
                    }
                    record_lifecycle(
                        master, operation, started_at=started_at, completed_at=utc_now(),
                        duration_ms=(perf_counter() - started) * 1000, outcome="failure",
                        snapshot=current, affected_session_ids=[], error_code=exc.code,
                    )
                raise
            master = snapshot["master_session_id"]
            after_ids = set(snapshot.get("loaded_session_ids", []))
            affected = snapshot.get("unloaded_session_ids") or sorted(after_ids - before_ids)
            graph_delta = len(after_ids) - len(before_ids)
            node_delta = int(snapshot.get("loaded_node_count", 0)) - before_nodes
            if graph_delta > 0:
                metric_add(master, "causal_graph.graphs.loaded", graph_delta)
            elif graph_delta < 0:
                metric_add(master, "causal_graph.graphs.unloaded", -graph_delta)
            if node_delta > 0:
                metric_add(master, "causal_graph.nodes.loaded", node_delta)
            elif node_delta < 0:
                metric_add(master, "causal_graph.nodes.unloaded", -node_delta)
            return record_lifecycle(
                master, operation, started_at=started_at, completed_at=utc_now(),
                duration_ms=(perf_counter() - started) * 1000,
                outcome=snapshot.get("outcome", "success"), snapshot=snapshot,
                affected_session_ids=list(affected),
            )
        return wrapped
    return decorate


class GraphContext:
    """Own one eagerly loaded Family while leaving graph retrieval to its caller."""

    def __init__(self) -> None:
        self._family: Family | None = None
        self._lock = RLock()

    @property
    def family(self) -> Family:
        if self._family is None:
            raise GraphContextError("context_not_ready", "the context has no loaded master Family")
        return self._family

    @property
    def master_session_id(self) -> str | None:
        return self._family.master.session_id if self._family else None

    def resolve(self, session_id: str) -> Graph | None:
        return self._family.resolve(session_id) if self._family else None

    def _refs(self) -> set[str]:
        return {child for graph in self.family.all_graphs() for child in _spawn_refs(graph)}

    def _snapshot(self, *, outcome: str = "success") -> dict[str, Any]:
        family = self.family
        loaded = [graph.session_id for graph in family.all_graphs()]
        refs = sorted(self._refs())
        unresolved = sorted(set(refs) - set(loaded))
        return {
            "version": "1",
            "master_session_id": family.master.session_id,
            "generated_at": _utc_now(),
            "state": "partial" if unresolved else "complete",
            "outcome": outcome,
            "loaded_session_ids": loaded,
            "loaded_session_count": len(loaded),
            "loaded_node_count": sum(len(graph.nodes) for graph in family.all_graphs()),
            "referenced_session_ids": refs,
            "unresolved_session_ids": unresolved,
        }

    def _ensure_unowned(self, session_ids: list[str]) -> None:
        conflicts = [sid for sid in session_ids if (owner := _ACTIVE_CONTEXTS.get(sid)) is not None and owner is not self]
        if conflicts:
            raise GraphContextError(
                "session_ownership_conflict", "one or more sessions are active in another context",
                master_session_id=self.master_session_id, session_id=conflicts[0], details={"session_ids": conflicts},
            )

    @_observed("load_self")
    def load_self(self, master_graph: dict[str, Any]) -> dict[str, Any]:
        """Load one master graph dictionary; repeated loading is idempotent."""
        graph = _graph_from_dict(master_graph)
        if graph.parent_session_id is not None:
            raise GraphContextError("invalid_master_graph", "load_self requires parent_session_id null", session_id=graph.session_id)
        with self._lock, _REGISTRY_LOCK:
            if self._family is not None and self._family.master.session_id != graph.session_id:
                raise GraphContextError(
                    "context_master_conflict", "the context already belongs to another master",
                    master_session_id=self._family.master.session_id, session_id=graph.session_id,
                )
            self._ensure_unowned([graph.session_id])
            if self._family is None:
                self._family = Family(master=graph)
                _ACTIVE_CONTEXTS[graph.session_id] = self
                _log.info("graph_context_load_self", master_session_id=graph.session_id, outcome="success")
            return self._snapshot(outcome="unchanged" if self._family.master is not graph else "success")

    @_observed("load_tree")
    def load_tree(self, resolve_graphs: Callable[[list[str]], dict[str, dict]]) -> dict[str, Any]:
        """Eagerly resolve every reachable descendant in breadth-first batches."""
        if not callable(resolve_graphs):
            raise GraphContextError("resolver_result_invalid", "resolve_graphs must be callable", master_session_id=self.master_session_id)
        with self._lock:
            family = self.family
            requested = sorted(self._refs() - {graph.session_id for graph in family.all_graphs()})
            while requested:
                expected_parents: dict[str, set[str]] = {session_id: set() for session_id in requested}
                for loaded_graph in family.all_graphs():
                    for child_id in _spawn_refs(loaded_graph):
                        if child_id in expected_parents:
                            expected_parents[child_id].add(loaded_graph.session_id)
                started = perf_counter()
                try:
                    resolved = resolve_graphs(list(requested))
                except Exception as exc:
                    metric_add(family.master.session_id, "causal_graph.resolver.calls", labels={"outcome": "failure"})
                    metric_add(family.master.session_id, "causal_graph.resolver.duration_ms", (perf_counter() - started) * 1000, metric_type="observation")
                    raise GraphContextError(
                        "resolver_failed", str(exc), master_session_id=family.master.session_id,
                        details={"requested_session_ids": requested},
                    ) from exc
                _log.debug(
                    "graph_context_resolver_batch", master_session_id=family.master.session_id,
                    requested_session_ids=requested, duration_ms=(perf_counter() - started) * 1000,
                )
                metric_add(family.master.session_id, "causal_graph.resolver.calls", labels={"outcome": "success"})
                metric_add(family.master.session_id, "causal_graph.resolver.duration_ms", (perf_counter() - started) * 1000, metric_type="observation")
                if not isinstance(resolved, dict) or any(key not in requested for key in resolved):
                    raise GraphContextError(
                        "resolver_result_invalid", "resolver must return a mapping containing only requested session IDs",
                        master_session_id=family.master.session_id,
                    )
                batch: dict[str, Graph] = {}
                for key, value in resolved.items():
                    graph = _graph_from_dict(value)
                    if key != graph.session_id:
                        raise GraphContextError(
                            "resolver_result_invalid", "resolver key does not match graph session_id",
                            master_session_id=family.master.session_id, session_id=graph.session_id,
                            details={"key": key},
                        )
                    if graph.parent_session_id not in expected_parents[key]:
                        raise GraphContextError(
                            "resolver_result_invalid", "resolved graph parent does not own its spawn reference",
                            master_session_id=family.master.session_id, session_id=graph.session_id,
                            details={"parent_session_id": graph.parent_session_id},
                        )
                    ancestors: set[str] = set()
                    parent = graph.parent_session_id
                    while parent:
                        if parent in ancestors:
                            raise GraphContextError("resolver_result_invalid", "session ancestry contains a cycle", session_id=graph.session_id)
                        ancestors.add(parent)
                        parent_graph = family.resolve(parent)
                        parent = parent_graph.parent_session_id if parent_graph else None
                    if graph.session_id in ancestors or _spawn_refs(graph) & ancestors:
                        raise GraphContextError("resolver_result_invalid", "resolved graph introduces a session cycle", session_id=graph.session_id)
                    batch[key] = graph

                with _REGISTRY_LOCK:
                    self._ensure_unowned(list(batch))
                    family.children.update(batch)
                    for session_id in batch:
                        _ACTIVE_CONTEXTS[session_id] = self
                if hasattr(family, "_causal_graph_query_context"):
                    delattr(family, "_causal_graph_query_context")
                loaded = {graph.session_id for graph in family.all_graphs()}
                requested = sorted({child for graph in batch.values() for child in _spawn_refs(graph)} - loaded)
            return self._snapshot()

    @_observed("unload")
    def unload(self, session_id: str) -> dict[str, Any]:
        """Unload one non-master session and every descendant beneath it."""
        with self._lock, _REGISTRY_LOCK:
            family = self.family
            if session_id == family.master.session_id:
                raise GraphContextError(
                    "master_unload_forbidden", "use unload_tree to unload a master",
                    master_session_id=family.master.session_id, session_id=session_id,
                )
            if session_id not in family.children:
                raise GraphContextError(
                    "session_not_loaded", "the requested session is not loaded",
                    master_session_id=family.master.session_id, session_id=session_id,
                )
            remove = {session_id}
            changed = True
            while changed:
                descendants = {sid for sid, graph in family.children.items() if graph.parent_session_id in remove}
                changed = not descendants.issubset(remove)
                remove.update(descendants)
            for sid in remove:
                family.children.pop(sid, None)
                _ACTIVE_CONTEXTS.pop(sid, None)
            if hasattr(family, "_causal_graph_query_context"):
                delattr(family, "_causal_graph_query_context")
            report = self._snapshot()
            report["unloaded_session_ids"] = sorted(remove)
            return report

    @_observed("unload_tree")
    def unload_tree(self) -> dict[str, Any]:
        """Unload the whole family and release every active registry entry."""
        with self._lock, _REGISTRY_LOCK:
            family = self.family
            master = family.master.session_id
            unloaded = [graph.session_id for graph in family.all_graphs()]
            nodes = sum(len(graph.nodes) for graph in family.all_graphs())
            for sid in unloaded:
                if _ACTIVE_CONTEXTS.get(sid) is self:
                    _ACTIVE_CONTEXTS.pop(sid, None)
            self._family = None
            return {
                "version": "1", "master_session_id": master, "generated_at": _utc_now(),
                "state": "unloaded", "outcome": "success", "loaded_session_ids": [],
                "loaded_session_count": 0, "loaded_node_count": 0,
                "unloaded_session_ids": unloaded, "unloaded_node_count": nodes,
                "referenced_session_ids": [], "unresolved_session_ids": [],
            }
