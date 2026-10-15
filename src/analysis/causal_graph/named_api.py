"""Stable named causal-graph APIs built on the versioned query engine."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from functools import wraps
import json
from time import perf_counter
from typing import Any

import structlog
import re2

from .field_registry import MISSING, FieldRegistry, default_summary_fields
from .models import Family, Graph, Node, NodeType
from .observability import metric_add
from .query import query
from .query_config import load_query_config

__all__ = [
    "NamedApiError", "budget", "goods", "memory_usage", "list_tools", "tool_usage",
    "turns", "subagents", "gitops",
]

_log = structlog.get_logger(module="analysis.causal_graph.named_api")
_USAGE_FIELDS = (
    ("input_tokens", "input_tokens"),
    ("output_tokens", "output_tokens"),
    ("cache_read_tokens", "cache_read_tokens"),
    ("cache_write_tokens", "cache_write_tokens"),
    ("estimated_reasoning_tokens", "reasoning_tokens"),
)


class NamedApiError(ValueError):
    """A stable named-API error with a machine-readable envelope."""

    def __init__(self, api: str, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.api = api
        self.code = code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "1",
            "api": self.api,
            "code": self.code,
            "message": str(self),
            "details": self.details,
        }


@dataclass
class _BudgetTurnRollup:
    total: dict[str, int]
    by_model: dict[str | None, dict[str, int]]


@dataclass
class _FamilyQueryContext:
    budget_reports: dict[tuple, dict[str, Any]] = field(default_factory=dict)
    budget_turn_rollups: dict[tuple[str, int, str], _BudgetTurnRollup] = field(default_factory=dict)


def _query_context(family: Family) -> _FamilyQueryContext:
    context = getattr(family, "_causal_graph_query_context", None)
    if context is None:
        context = _FamilyQueryContext()
        setattr(family, "_causal_graph_query_context", context)
    return context


def _scope_value(api: str, scope: str | dict[str, Any]) -> tuple[dict[str, str], tuple]:
    if isinstance(scope, str):
        if scope not in {"self", "tree", "subagents"}:
            raise NamedApiError(
                api, "invalid_scope", "scope must be self, tree, subagents, or a session scope object",
            )
        normalized = {"kind": scope}
    elif isinstance(scope, dict):
        kind = scope.get("kind")
        session_id = scope.get("session_id")
        if kind == "session" and isinstance(session_id, str) and session_id:
            normalized = {"kind": "session", "session_id": session_id}
        elif kind in {"self", "tree", "subagents"} and "session_id" not in scope:
            normalized = {"kind": kind}
        else:
            raise NamedApiError(api, "invalid_scope", "scope is malformed")
    else:
        raise NamedApiError(api, "invalid_scope", "scope is malformed")
    return normalized, tuple(sorted(normalized.items()))


def _turn_value(turn: dict[str, Any] | None) -> tuple[tuple[str, int] | None, tuple | None]:
    if turn is None:
        return None, None
    if not isinstance(turn, dict) or set(turn) != {"session_id", "turn_num"}:
        raise NamedApiError("budget", "invalid_filter", "turn must contain session_id and turn_num")
    session_id, turn_num = turn["session_id"], turn["turn_num"]
    if not isinstance(session_id, str) or not session_id or not isinstance(turn_num, int) or turn_num < 1:
        raise NamedApiError("budget", "invalid_filter", "turn must contain a non-empty session_id and turn_num >= 1")
    return (session_id, turn_num), (session_id, turn_num)


def _zero_usage() -> dict[str, int]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "reasoning_included_in_output_tokens": True,
    }


def _add_usage(target: dict[str, int], source: dict[str, int]) -> None:
    for name in _zero_usage():
        if name != "reasoning_included_in_output_tokens":
            target[name] += source[name]


def _assistant_usage(node: Node) -> dict[str, int]:
    usage = _zero_usage()
    for stored, public in _USAGE_FIELDS:
        value = node.attrs.get(stored)
        usage[public] = value if isinstance(value, int) else 0
    return usage


def _descendants(graph: Graph, root: Node) -> list[Node]:
    found: list[Node] = []
    pending = list(reversed(root.children))
    seen: set[str] = set()
    while pending:
        node_id = pending.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        node = graph.nodes.get(node_id)
        if node is None:
            continue
        found.append(node)
        pending.extend(reversed(node.children))
    return found


def _turn_rollup(context: _FamilyQueryContext, graph: Graph, turn: Node, revision: str) -> _BudgetTurnRollup:
    turn_num = turn.attrs.get("turn_num")
    if not isinstance(turn_num, int):
        raise NamedApiError("budget", "query_validation_error", "turn node has no valid turn_num")
    cache_key = (graph.session_id, turn_num, revision)
    cached = context.budget_turn_rollups.get(cache_key)
    if cached is not None:
        return cached

    total = _zero_usage()
    by_model: dict[str | None, dict[str, int]] = {}
    for node in _descendants(graph, turn):
        if node.type is not NodeType.ASSISTANT:
            continue
        usage = _assistant_usage(node)
        _add_usage(total, usage)
        model = node.attrs.get("model")
        model = model if isinstance(model, str) else None
        model_usage = by_model.setdefault(model, _zero_usage())
        _add_usage(model_usage, usage)
    rollup = _BudgetTurnRollup(total=total, by_model=by_model)
    context.budget_turn_rollups[cache_key] = rollup
    return rollup


def _turn_for_assistant(graph: Graph, assistant: Node) -> Node | None:
    node = assistant
    seen: set[str] = set()
    while node.parent_id and node.parent_id not in seen:
        seen.add(node.parent_id)
        node = graph.nodes.get(node.parent_id)
        if node is None:
            return None
        if node.type is NodeType.TURN:
            return node
    return None


def _containing_turn(graph: Graph, node: Node) -> Node | None:
    """Return a node's containing turn without exposing local turn storage keys."""
    current = node
    seen: set[str] = set()
    while current.parent_id and current.parent_id not in seen:
        seen.add(current.parent_id)
        current = graph.nodes.get(current.parent_id)
        if current is None:
            return None
        if current.type is NodeType.TURN:
            return current
    return None


def _selected_turns(family: Family, scope: dict[str, str]) -> tuple[list[tuple[Graph, Node]], str, list[dict]]:
    try:
        result = query(
            family,
            {
                "version": "1",
                "scope": scope,
                "select": {"type": "Assistant"},
                "return": {"kind": "nodes", "verbosity": "full"},
            },
        )
    except Exception as exc:  # QuerySpec errors are intentionally not public named-API details.
        raise NamedApiError("budget", "query_validation_error", str(exc)) from exc
    turns: dict[tuple[str, int], tuple[Graph, Node]] = {}
    for item in result.items or []:
        session_id = item.identity.session_id
        graph = family.resolve(session_id)
        if graph is None or not item.identity.node_id:
            continue
        assistant = graph.nodes.get(item.identity.node_id)
        if assistant is None:
            continue
        turn = _turn_for_assistant(graph, assistant)
        turn_num = turn.attrs.get("turn_num") if turn else None
        if isinstance(turn_num, int):
            turns[(session_id, turn_num)] = (graph, turn)
    warnings = [warning.model_dump() for warning in result.warnings]
    return list(turns.values()), result.status, warnings


def budget(
    family: Family,
    *,
    scope: str | dict[str, Any] = "tree",
    by: str = "model",
    model: str | None = None,
    permission_mode: str | None = None,
    turn: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return token usage grouped by the approved named-API axis."""
    if by not in {"model", "permission_mode", "turn", "subagent"}:
        raise NamedApiError("budget", "invalid_budget_axis", "by must be model, permission_mode, turn, or subagent")
    if model is not None and (not isinstance(model, str) or not model):
        raise NamedApiError("budget", "invalid_filter", "model must be a non-empty string or null")
    if permission_mode is not None and not isinstance(permission_mode, str):
        raise NamedApiError("budget", "invalid_filter", "permission_mode must be a string or null")
    normalized_scope, scope_key = _scope_value("budget", scope)
    turn_filter, turn_key = _turn_value(turn)
    config = load_query_config()
    context = _query_context(family)
    cache_key = (scope_key, by, model, permission_mode, turn_key, config.revision)
    cached = context.budget_reports.get(cache_key)
    if cached is not None:
        metric_add(family.master.session_id, "causal_graph.budget.cache", labels={"outcome": "hit"})
        _log.debug("named_api_budget_cache_hit", scope=normalized_scope, by=by)
        return deepcopy(cached)

    selected, query_status, warnings = _selected_turns(family, normalized_scope)
    rows: dict[tuple, dict[str, Any]] = {}
    for graph, turn_node in selected:
        turn_num = turn_node.attrs["turn_num"]
        if turn_filter and turn_filter != (graph.session_id, turn_num):
            continue
        mode = turn_node.attrs.get("permission_mode")
        if permission_mode is not None and mode != permission_mode:
            continue
        rollup = _turn_rollup(context, graph, turn_node, config.revision)
        usage_by_model = rollup.by_model
        if model is not None:
            model_usage = usage_by_model.get(model)
            if model_usage is None:
                continue
            model_entries = [(model, model_usage)]
        else:
            model_entries = list(usage_by_model.items()) if by == "model" else [(None, rollup.total)]
        for row_model, usage in model_entries:
            if by == "model":
                key = {"model": row_model}
            elif by == "permission_mode":
                key = {"permission_mode": mode}
            elif by == "turn":
                key = {"session_id": graph.session_id, "turn_num": turn_num}
            else:
                key = {"session_id": graph.session_id}
            encoded = tuple(sorted(key.items()))
            row = rows.setdefault(encoded, {"key": key, "usage": _zero_usage()})
            _add_usage(row["usage"], usage)
    ordered_rows = sorted(rows.values(), key=lambda row: json.dumps(row["key"], sort_keys=True, default=str))
    report = {
        "version": "1",
        "kind": "budget",
        "status": query_status,
        "scope": normalized_scope,
        "by": by,
        "rows": ordered_rows,
        "warnings": warnings,
    }
    context.budget_reports[cache_key] = deepcopy(report)
    metric_add(family.master.session_id, "causal_graph.budget.cache", labels={"outcome": "miss"})
    _log.debug("named_api_budget_cache_miss", scope=normalized_scope, by=by, rows=len(ordered_rows))
    return report


_ARTIFACT_CLASSES = frozenset({
    "code", "documentation", "config", "markup", "data", "notebook", "template",
    "document", "spreadsheet", "presentation", "pdf", "unknown",
})
_GOODS_OPERATIONS = frozenset({"create", "edit", "delete", "unknown"})


def _tool_nodes(
    api: str,
    family: Family,
    scope: dict[str, str],
) -> tuple[list[tuple[Graph, Node]], str, list[dict[str, Any]]]:
    try:
        result = query(
            family,
            {
                "version": "1",
                "scope": scope,
                "select": {"type": "Tool"},
                "return": {"kind": "nodes", "verbosity": "full"},
            },
        )
    except Exception as exc:
        raise NamedApiError(api, "query_validation_error", str(exc)) from exc
    nodes: list[tuple[Graph, Node]] = []
    for item in result.items or []:
        if not item.identity.node_id:
            continue
        graph = family.resolve(item.identity.session_id)
        node = graph.nodes.get(item.identity.node_id) if graph else None
        if node is not None:
            nodes.append((graph, node))
    return nodes, result.status, [warning.model_dump() for warning in result.warnings]


def _line_totals(hunks: Any) -> tuple[int, dict[str, Any]]:
    if not isinstance(hunks, list) or not hunks:
        return 0, {"status": "not_reported", "net_delta": None, "reason": "no_hunks"}
    ranges: list[tuple[int, int]] = []
    delta = 0
    for hunk in hunks:
        if not isinstance(hunk, dict):
            return len(hunks), {"status": "not_reported", "net_delta": None, "reason": "no_hunks"}
        start, new_len, old_len = hunk.get("start"), hunk.get("len"), hunk.get("old_len")
        if not all(isinstance(value, int) and value >= 0 for value in (start, new_len, old_len)):
            return len(hunks), {"status": "not_reported", "net_delta": None, "reason": "no_hunks"}
        width = max(new_len, old_len, 1)
        ranges.append((start, start + width))
        delta += new_len - old_len
    ranges.sort()
    if any(current[0] < previous[1] for previous, current in zip(ranges, ranges[1:])):
        return len(hunks), {"status": "unavailable", "net_delta": None, "reason": "overlapping_hunks"}
    return len(hunks), {"status": "available", "net_delta": delta}


def goods(
    family: Family,
    *,
    scope: str | dict[str, Any] = "tree",
    artifact_class: str | None = None,
    extension: str | None = None,
) -> dict[str, Any]:
    """Return local file-producing tool records, deduplicated by reported path."""
    if artifact_class is not None and artifact_class not in _ARTIFACT_CLASSES:
        raise NamedApiError("goods", "invalid_goods_filter", "artifact_class is not an accepted artifact class")
    if extension is not None and (not isinstance(extension, str) or not extension.startswith(".")):
        raise NamedApiError("goods", "invalid_goods_filter", "extension must include a leading dot")
    normalized_scope, _ = _scope_value("goods", scope)
    tools, status, warnings = _tool_nodes("goods", family, normalized_scope)
    artifacts: dict[str, dict[str, Any]] = {}
    excluded_remote = 0
    for graph, tool in tools:
        files = tool.attrs.get("files")
        if not isinstance(files, list):
            continue
        for file in files:
            if not isinstance(file, dict):
                continue
            if file.get("is_memory") is True:
                continue
            file_class = file.get("artifact_class")
            file_extension = file.get("extension")
            if artifact_class is not None and file_class != artifact_class:
                continue
            if extension is not None and file_extension != extension:
                continue
            path = file.get("path")
            if not isinstance(path, str) or not path:
                if file.get("remote_id") is not None:
                    excluded_remote += 1
                continue
            operation = file.get("operation")
            if operation == "read":
                continue
            operation = operation if operation in _GOODS_OPERATIONS else "unknown"
            artifact = artifacts.setdefault(
                path,
                {
                    "path": path,
                    "extension": file_extension if isinstance(file_extension, str) else None,
                    "artifact_class": file_class if isinstance(file_class, str) else None,
                    "changes": [],
                },
            )
            hunk_count, line_totals = _line_totals(file.get("hunks"))
            artifact["changes"].append(
                {
                    "operation": operation,
                    "source": {"session_id": graph.session_id, "node_id": tool.id},
                    "hunk_count": hunk_count,
                    "line_totals": line_totals,
                }
            )
    if excluded_remote:
        status = "partial"
        warnings.append(
            {
                "code": "remote_artifact_excluded",
                "message": f"excluded {excluded_remote} remote-only artifact record(s)",
            }
        )
    return {
        "version": "1",
        "kind": "goods",
        "status": status,
        "scope": normalized_scope,
        "artifacts": list(artifacts.values()),
        "warnings": warnings,
    }


_MEMORY_OPERATIONS = ("read", "create", "edit", "delete", "unknown")


def _memory_line_totals(hunks: Any) -> tuple[int, dict[str, Any]]:
    """Calculate one Edit's net line delta with explicit invalid/overlap states."""
    if not isinstance(hunks, list) or not hunks:
        return 0, {"status": "not_reported", "net_delta": None, "reason": "no_edit_hunks"}
    ranges: list[tuple[int, int]] = []
    delta = 0
    for hunk in hunks:
        if not isinstance(hunk, dict):
            return len(hunks), {"status": "unavailable", "net_delta": None, "reason": "invalid_hunks"}
        start, new_len, old_len = hunk.get("start"), hunk.get("len"), hunk.get("old_len")
        if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0
                   for value in (start, new_len, old_len)):
            return len(hunks), {"status": "unavailable", "net_delta": None, "reason": "invalid_hunks"}
        width = max(new_len, old_len, 1)
        ranges.append((start, start + width))
        delta += new_len - old_len
    ranges.sort()
    if any(current[0] < previous[1] for previous, current in zip(ranges, ranges[1:])):
        return len(hunks), {"status": "unavailable", "net_delta": None, "reason": "overlapping_hunks"}
    return len(hunks), {"status": "available", "net_delta": delta}


def _memory_activity(operations: set[str]) -> str:
    has_read = "read" in operations
    has_write = bool(operations - {"read"})
    if has_read and has_write:
        return "read_write"
    return "read" if has_read else "write"


def _memory_tool_nodes(
    family: Family, scope: dict[str, str],
) -> tuple[list[tuple[Graph, Node]], str, list[dict[str, Any]]]:
    try:
        result = query(
            family,
            {
                "version": "1",
                "scope": scope,
                "select": {"type": "Tool", "where": "Tool.memory_access == true"},
                "return": {"kind": "nodes", "verbosity": "ref"},
            },
        )
    except Exception as exc:
        raise NamedApiError("memory_usage", "query_validation_error", str(exc)) from exc
    nodes: list[tuple[Graph, Node]] = []
    for item in result.items or []:
        node_id = item.identity.node_id
        graph = family.resolve(item.identity.session_id)
        node = graph.nodes.get(node_id) if graph is not None and node_id else None
        if node is not None:
            nodes.append((graph, node))
    return nodes, result.status, [warning.model_dump() for warning in result.warnings]


def _breakdown_rows(
    responses: list[dict[str, Any]], field: str,
) -> list[dict[str, Any]]:
    rows: dict[Any, dict[str, Any]] = {}
    for response in responses:
        value = response[field]
        row = rows.setdefault(value, {
            "key": {field: value}, "response_count": 0, "usage": _zero_usage(),
        })
        row["response_count"] += 1
        _add_usage(row["usage"], response["usage"])
    return list(rows.values())


def memory_usage(
    family: Family,
    *,
    scope: str | dict[str, Any] = "tree",
) -> dict[str, Any]:
    """Report classified memory activity and associated response usage."""
    normalized_scope, _ = _scope_value("memory_usage", scope)
    tools, status, warnings = _memory_tool_nodes(family, normalized_scope)
    tool_activity = {"read": 0, "write": 0, "read_write": 0}
    operation_counts = {operation: 0 for operation in _MEMORY_OPERATIONS}
    files: dict[str, dict[str, Any]] = {}
    responses_by_identity: dict[tuple[str, str], dict[str, Any]] = {}

    for graph, tool in tools:
        operations: set[str] = set()
        raw_files = tool.attrs.get("files")
        for file in raw_files if isinstance(raw_files, list) else []:
            if not isinstance(file, dict) or file.get("is_memory") is not True:
                continue
            path = file.get("path")
            if not isinstance(path, str) or not path:
                continue
            operation = file.get("operation")
            operation = operation if operation in _MEMORY_OPERATIONS else "unknown"
            operations.add(operation)
            operation_counts[operation] += 1
            artifact = files.setdefault(path, {
                "path": path,
                "memory_kind": file.get("memory_kind")
                if file.get("memory_kind") in {"index", "entry", "unknown"} else "unknown",
                "operations": {name: 0 for name in _MEMORY_OPERATIONS},
                "edit_line_totals": None,
                "changes": [],
                "_edit_deltas": [],
                "_edit_failures": [],
            })
            artifact["operations"][operation] += 1
            if operation == "edit":
                hunk_count, line_totals = _memory_line_totals(file.get("hunks"))
                if line_totals["status"] == "available":
                    artifact["_edit_deltas"].append(line_totals["net_delta"])
                else:
                    artifact["_edit_failures"].append(line_totals["reason"])
            else:
                hunk_count = len(file.get("hunks")) if isinstance(file.get("hunks"), list) else 0
                line_totals = {"status": "not_reported", "net_delta": None, "reason": "absent"}
            artifact["changes"].append({
                "operation": operation,
                "source": {"session_id": graph.session_id, "node_id": tool.id},
                "hunk_count": hunk_count,
                "line_totals": line_totals,
            })

        if operations:
            tool_activity[_memory_activity(operations)] += 1

        parent = graph.nodes.get(tool.parent_id) if tool.parent_id else None
        message_id = parent.attrs.get("message_id") if parent is not None else None
        if parent is None or parent.type is not NodeType.ASSISTANT or not isinstance(message_id, str):
            status = "partial"
            warnings.append({
                "code": "memory_usage_parent_missing",
                "message": f"memory Tool {graph.session_id}:{tool.id} has no resolvable parent Assistant",
            })
            continue
        identity = (graph.session_id, message_id)
        response = responses_by_identity.get(identity)
        if response is None:
            all_actions = [graph.nodes.get(node_id) for node_id in parent.children]
            mixed_work = any(
                action is not None
                and action.type in {NodeType.TOOL, NodeType.SPAWN}
                and not (action.type is NodeType.TOOL and action.attrs.get("memory_access") is True)
                for action in all_actions
            )
            response = {
                "model": parent.attrs.get("model") if isinstance(parent.attrs.get("model"), str) else None,
                "effort": parent.attrs.get("effort") if isinstance(parent.attrs.get("effort"), str) else None,
                "work_class": "mixed" if mixed_work else "memory_only",
                "operations": set(),
                "usage": _assistant_usage(parent),
            }
            responses_by_identity[identity] = response
        response["operations"].update(operations)

    for artifact in files.values():
        failures = artifact.pop("_edit_failures")
        deltas = artifact.pop("_edit_deltas")
        unavailable = [reason for reason in failures if reason != "no_edit_hunks"]
        if unavailable:
            reason = "invalid_hunks" if "invalid_hunks" in unavailable else unavailable[0]
            artifact["edit_line_totals"] = {
                "status": "unavailable", "net_delta": None, "reason": reason,
            }
        elif deltas:
            artifact["edit_line_totals"] = {
                "status": "available", "net_delta": sum(deltas),
            }
        else:
            artifact["edit_line_totals"] = {
                "status": "not_reported", "net_delta": None, "reason": "no_edit_hunks",
            }

    responses = list(responses_by_identity.values())
    for response in responses:
        response["activity"] = _memory_activity(response.pop("operations"))
    associated = _zero_usage()
    memory_only = _zero_usage()
    mixed = _zero_usage()
    for response in responses:
        _add_usage(associated, response["usage"])
        _add_usage(memory_only if response["work_class"] == "memory_only" else mixed, response["usage"])

    return {
        "version": "1",
        "kind": "memory_usage",
        "status": status,
        "scope": normalized_scope,
        "counts": {
            "tool_calls": len(tools),
            "distinct_files": len(files),
            "responses": len(responses),
            "tool_calls_by_activity": tool_activity,
            "file_operations": operation_counts,
        },
        "usage": {"associated": associated, "memory_only": memory_only, "mixed": mixed},
        "breakdowns": {
            "by_model": _breakdown_rows(responses, "model"),
            "by_effort": _breakdown_rows(responses, "effort"),
            "by_activity": _breakdown_rows(responses, "activity"),
            "by_work_class": _breakdown_rows(responses, "work_class"),
        },
        "files": list(files.values()),
        "warnings": warnings,
    }


def list_tools(
    family: Family,
    *,
    scope: str | dict[str, Any] = "tree",
) -> dict[str, Any]:
    """List distinct harness tools and extracted commands with invocation counts."""
    normalized_scope, _ = _scope_value("list_tools", scope)
    tool_nodes, status, warnings = _tool_nodes("list_tools", family, normalized_scope)
    counts: dict[tuple[str, str], int] = {}
    for _, tool in tool_nodes:
        harness_name = tool.attrs.get("tool_name")
        if isinstance(harness_name, str) and harness_name:
            key = ("harness", harness_name)
            counts[key] = counts.get(key, 0) + 1
        commands = tool.attrs.get("cmd")
        if isinstance(commands, list):
            for command in dict.fromkeys(command for command in commands if isinstance(command, str) and command):
                key = ("command", command)
                counts[key] = counts.get(key, 0) + 1
    class_order = {"harness": 0, "command": 1}
    tools = [
        {"tool_class": tool_class, "name": name, "invocation_count": count}
        for (tool_class, name), count in counts.items()
    ]
    tools.sort(key=lambda item: (class_order[item["tool_class"]], -item["invocation_count"], item["name"]))
    return {
        "version": "1",
        "kind": "tool_list",
        "status": status,
        "scope": normalized_scope,
        "count": len(tools),
        "tools": tools,
        "warnings": warnings,
    }


def tool_usage(
    family: Family,
    *,
    scope: str | dict[str, Any] = "tree",
    tool_class: str | None = None,
    name: str | None = None,
    outcome: str = "all",
    invocation_matches: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Explore individual tool invocations through the stable named-API envelope."""
    if tool_class not in {None, "harness", "command"}:
        raise NamedApiError("tool_usage", "invalid_tool_selector", "tool_class must be harness, command, or null")
    if name is not None and (tool_class is None or not isinstance(name, str) or not name):
        raise NamedApiError("tool_usage", "invalid_tool_selector", "name requires tool_class and must be non-empty")
    if outcome not in {"success", "failure", "all"}:
        raise NamedApiError("tool_usage", "invalid_tool_outcome", "outcome must be success, failure, or all")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10_000):
        raise NamedApiError("tool_usage", "invalid_tool_limit", "limit must be an integer from 1 through 10000")
    config = load_query_config()
    pattern = None
    if invocation_matches is not None:
        if not isinstance(invocation_matches, str) or len(invocation_matches) > config.filter.max_regex_chars:
            raise NamedApiError("tool_usage", "invalid_invocation_regex", "invocation_matches exceeds the configured regex limit")
        try:
            pattern = re2.compile(invocation_matches)
        except re2.error as exc:
            raise NamedApiError("tool_usage", "invalid_invocation_regex", str(exc)) from exc
    normalized_scope, _ = _scope_value("tool_usage", scope)
    tool_nodes, status, warnings = _tool_nodes("tool_usage", family, normalized_scope)
    registry = FieldRegistry()
    records: list[dict[str, Any]] = []
    for graph, tool in tool_nodes:
        harness_name = tool.attrs.get("tool_name")
        commands = tool.attrs.get("cmd")
        commands = [command for command in commands if isinstance(command, str)] if isinstance(commands, list) else []
        if tool_class == "harness" and name is not None and harness_name != name:
            continue
        if tool_class == "command":
            if not commands or (name is not None and name not in commands):
                continue
        derived_outcome = registry.value(tool, "Tool.outcome")
        if outcome != "all" and derived_outcome != outcome:
            continue
        input_ = tool.attrs.get("input")
        invocation = input_.get("command") if isinstance(input_, dict) and isinstance(input_.get("command"), str) else None
        if pattern is not None and (invocation is None or pattern.search(invocation) is None):
            continue
        result = tool.attrs.get("result")
        failure_message = result.get("error_text") if isinstance(result, dict) and isinstance(result.get("error_text"), str) else None
        records.append(
            {
                "identity": {"session_id": graph.session_id, "node_id": tool.id},
                "harness_name": harness_name if isinstance(harness_name, str) else "",
                "commands": commands,
                "invocation": invocation,
                "outcome": derived_outcome,
                "duration_ms": tool.attrs.get("duration_ms") if isinstance(tool.attrs.get("duration_ms"), int) else None,
                "exit_status": tool.attrs.get("exit_status") if isinstance(tool.attrs.get("exit_status"), int) else None,
                "denial_kind": tool.attrs.get("tool_denial_kind") if isinstance(tool.attrs.get("tool_denial_kind"), str) else None,
                "failure_message": failure_message,
            }
        )
    count = len(records)
    effective_limit = limit or 10_000
    returned = records[:effective_limit]
    return {
        "version": "1",
        "kind": "tool_usage",
        "status": status,
        "scope": normalized_scope,
        "count": count,
        "returned_count": len(returned),
        "truncated": count > effective_limit,
        "tools": returned,
        "warnings": warnings,
    }


_TURN_NODE_TYPES = frozenset({"node", "user_prompt", "assistant", "tool", "spawn"})


def _turn_id_value(turn_id: Any) -> tuple[str, int]:
    if not isinstance(turn_id, dict) or set(turn_id) != {"session_id", "turn_num"}:
        raise NamedApiError("turns", "invalid_turn_id", "turn_id must contain session_id and turn_num")
    session_id, turn_num = turn_id["session_id"], turn_id["turn_num"]
    if not isinstance(session_id, str) or not session_id or not isinstance(turn_num, int) or isinstance(turn_num, bool) or turn_num < 1:
        raise NamedApiError("turns", "invalid_turn_id", "turn_id must contain a non-empty session_id and turn_num >= 1")
    return session_id, turn_num


def _turn_node(graph: Graph, turn_num: int) -> Node | None:
    for node in graph.nodes.values():
        if node.type is NodeType.TURN and node.attrs.get("turn_num") == turn_num:
            return node
    return None


def _tool_inventory(tool_nodes: list[Node]) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], int] = {}
    for tool in tool_nodes:
        harness_name = tool.attrs.get("tool_name")
        if isinstance(harness_name, str) and harness_name:
            key = ("harness", harness_name)
            counts[key] = counts.get(key, 0) + 1
        commands = tool.attrs.get("cmd")
        if isinstance(commands, list):
            for command in dict.fromkeys(command for command in commands if isinstance(command, str) and command):
                key = ("command", command)
                counts[key] = counts.get(key, 0) + 1
    class_order = {"harness": 0, "command": 1}
    return sorted(
        (
            {"tool_class": tool_class, "name": name, "invocation_count": count}
            for (tool_class, name), count in counts.items()
        ),
        key=lambda item: (class_order[item["tool_class"]], -item["invocation_count"], item["name"]),
    )


def _turn_node_summary(registry: FieldRegistry, graph: Graph, node: Node) -> dict[str, Any]:
    fields = {
        name: (None if (value := registry.value(node, name)) is MISSING else value)
        for name in default_summary_fields(node.type)
    }
    return {
        "identity": {"session_id": graph.session_id, "node_id": node.id},
        "type": node.type.value,
        "ts": node.ts,
        "fields": fields,
    }


def turns(
    family: Family,
    *,
    turn_id: dict[str, Any],
    node_type: str | None = None,
) -> dict[str, Any]:
    """Return one turn's stable summary and, optionally, its contained nodes."""
    session_id, turn_num = _turn_id_value(turn_id)
    if node_type is not None:
        if not isinstance(node_type, str) or node_type.casefold() not in _TURN_NODE_TYPES:
            raise NamedApiError(
                "turns", "invalid_turn_node_type",
                "node_type must be node, user_prompt, assistant, tool, spawn, or null",
            )
        node_type = node_type.casefold()

    graph = family.resolve(session_id)
    if graph is None:
        raise NamedApiError("turns", "turn_not_loaded", "the requested turn's session is not loaded")
    turn = _turn_node(graph, turn_num)
    if turn is None:
        raise NamedApiError("turns", "turn_not_loaded", "the requested turn is not loaded")

    try:
        selected = query(
            family,
            {
                "version": "1",
                "scope": {"kind": "session", "session_id": session_id},
                "select": {"type": "Turn", "where": f"Turn.turn_num == {turn_num}"},
                "return": {"kind": "nodes", "verbosity": "summary"},
            },
        )
    except Exception as exc:
        raise NamedApiError("turns", "query_validation_error", str(exc)) from exc
    if not selected.items:
        raise NamedApiError("turns", "turn_not_loaded", "the requested turn is not loaded")

    config = load_query_config()
    context = _query_context(family)
    rollup = _turn_rollup(context, graph, turn, config.revision)
    descendants = _descendants(graph, turn)
    tool_nodes = [node for node in descendants if node.type is NodeType.TOOL]
    models = sorted({
        model for node in descendants if node.type is NodeType.ASSISTANT
        if isinstance(model := node.attrs.get("model"), str) and model
    })
    attribution = turn.attrs.get("attribution")
    attribution = attribution if isinstance(attribution, dict) else {}
    spawns = [
        {
            "session_id": node.refs.get("child_session_id") if isinstance(node.refs.get("child_session_id"), str) else None,
            "spawn_id": node.id,
            "spawned_at": node.ts,
            "agent_type": node.attrs.get("agent_type") if isinstance(node.attrs.get("agent_type"), str) else None,
            "description": node.attrs.get("description") if isinstance(node.attrs.get("description"), str) else None,
            "status": node.attrs.get("status") if isinstance(node.attrs.get("status"), str) else None,
        }
        for node in descendants if node.type is NodeType.SPAWN
    ]
    mcp_counts: dict[str, int] = {}
    for tool in tool_nodes:
        name = tool.attrs.get("tool_name")
        if isinstance(name, str) and name.startswith("mcp__"):
            mcp_counts[name] = mcp_counts.get(name, 0) + 1
    report: dict[str, Any] = {
        "version": "1",
        "kind": "turns",
        "status": "complete",
        "turn": {
            "identity": {"session_id": session_id, "turn_num": turn_num},
            "ts": turn.ts,
            "status": turn.attrs.get("status") if isinstance(turn.attrs.get("status"), str) else None,
            "duration_ms": turn.attrs.get("duration_ms") if isinstance(turn.attrs.get("duration_ms"), int) else None,
            "models": models,
            "usage": deepcopy(rollup.total),
            "mode": {
                "permission_mode": turn.attrs.get("permission_mode") if isinstance(turn.attrs.get("permission_mode"), str) else None,
                "plan_mode": turn.attrs.get("plan_mode") if isinstance(turn.attrs.get("plan_mode"), bool) else None,
            },
            "attribution": {
                key: value if isinstance(value := attribution.get(key), list) else []
                for key in ("skill", "plugin", "agent", "effort")
            },
            "tool_count": len(tool_nodes),
            "tools_used": _tool_inventory(tool_nodes),
            "subagent_spawns": {"count": len(spawns), "spawns": spawns},
            "mcp_invocations": {
                "count": sum(mcp_counts.values()),
                "tools": [
                    {"name": name, "invocation_count": count}
                    for name, count in sorted(mcp_counts.items())
                ],
            },
        },
        "warnings": [],
    }
    if node_type is not None:
        registry = FieldRegistry()
        report["nodes"] = [
            _turn_node_summary(registry, graph, node)
            for node in descendants
            if node_type == "node" or node.type.value == node_type
        ]
    return report


def subagents(family: Family, *, scope: str = "self") -> dict[str, Any]:
    """Inventory master-session Spawn nodes without resolving their child sessions."""
    if scope != "self":
        raise NamedApiError("subagents", "invalid_subagent_scope", "subagents only accepts scope='self'")
    try:
        selected = query(
            family,
            {
                "version": "1",
                "scope": {"kind": "self"},
                "select": {"type": "Spawn"},
                "return": {"kind": "nodes", "verbosity": "full"},
            },
        )
    except Exception as exc:
        raise NamedApiError("subagents", "query_validation_error", str(exc)) from exc

    records: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    for item in selected.items or []:
        node_id = item.identity.node_id
        spawn = family.master.nodes.get(node_id) if node_id else None
        if spawn is None:
            continue
        turn = _containing_turn(family.master, spawn)
        turn_num = turn.attrs.get("turn_num") if turn else None
        if not isinstance(turn_num, int):
            warnings.append({
                "code": "spawn_without_turn",
                "message": f"excluded Spawn node {spawn.id!r} because it has no containing turn",
            })
            continue
        attrs = spawn.attrs
        records.append(
            {
                "session_id": spawn.refs.get("child_session_id") if isinstance(spawn.refs.get("child_session_id"), str) else None,
                "turn_id": {"session_id": family.master.session_id, "turn_num": turn_num},
                "spawn_id": spawn.id,
                "spawned_at": spawn.ts,
                "agent_type": attrs.get("agent_type") if isinstance(attrs.get("agent_type"), str) else None,
                "description": attrs.get("description") if isinstance(attrs.get("description"), str) else None,
                "prompt_char_count": attrs.get("prompt_char_count") if isinstance(attrs.get("prompt_char_count"), int) else None,
                "requested_model": attrs.get("requested_model") if isinstance(attrs.get("requested_model"), str) else None,
                "resolved_model": attrs.get("resolved_model") if isinstance(attrs.get("resolved_model"), str) else None,
                "run_in_background": attrs.get("run_in_background") if isinstance(attrs.get("run_in_background"), bool) else None,
                "status": attrs.get("status") if isinstance(attrs.get("status"), str) else None,
                "duration_ms": attrs.get("duration_ms") if isinstance(attrs.get("duration_ms"), int) else None,
            }
        )
    return {
        "version": "1",
        "kind": "subagents",
        "status": "complete",
        "scope": {"kind": "self"},
        "count": len(records),
        "subagents": records,
        "warnings": warnings,
    }


_GIT_OPERATIONS = frozenset({"commit", "push", "pr", "branch"})
_GIT_DETAIL_FIELDS = {
    "commit": ("sha", "kind"),
    "push": ("branch",),
    "pr": ("number", "url", "action"),
    "branch": ("ref", "action"),
}


def _git_details(operation: str, git: dict[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    for key in _GIT_DETAIL_FIELDS[operation]:
        value = git.get(key)
        if key == "number":
            details[key] = value if isinstance(value, int) and not isinstance(value, bool) else None
        else:
            details[key] = value if isinstance(value, str) else None
    return details


def gitops(
    family: Family,
    *,
    scope: str | dict[str, Any] = "tree",
    operation: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List only structured Git outcomes recognized by the result extractor."""
    if operation is not None and operation not in _GIT_OPERATIONS:
        raise NamedApiError("gitops", "invalid_git_operation", "operation must be commit, push, pr, branch, or null")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10_000):
        raise NamedApiError("gitops", "invalid_git_limit", "limit must be an integer from 1 through 10000")
    normalized_scope, _ = _scope_value("gitops", scope)
    tools, status, warnings = _tool_nodes("gitops", family, normalized_scope)
    records: list[dict[str, Any]] = []
    for graph, tool in tools:
        result = tool.attrs.get("result")
        git = result.get("git") if isinstance(result, dict) else None
        kind = git.get("op") if isinstance(git, dict) else None
        if kind not in _GIT_OPERATIONS or (operation is not None and kind != operation):
            continue
        input_ = tool.attrs.get("input")
        invocation = input_.get("command") if isinstance(input_, dict) and isinstance(input_.get("command"), str) else None
        commands = tool.attrs.get("cmd")
        commands = [command for command in commands if isinstance(command, str)] if isinstance(commands, list) else []
        harness_name = tool.attrs.get("tool_name")
        records.append(
            {
                "identity": {"session_id": graph.session_id, "node_id": tool.id},
                "operation": kind,
                "harness_name": harness_name if isinstance(harness_name, str) else "",
                "commands": commands,
                "invocation": invocation,
                "details": _git_details(kind, git),
            }
        )
    count = len(records)
    effective_limit = limit or 10_000
    returned = records[:effective_limit]
    return {
        "version": "1",
        "kind": "gitops",
        "status": status,
        "scope": normalized_scope,
        "count": count,
        "returned_count": len(returned),
        "truncated": count > effective_limit,
        "operations": returned,
        "warnings": warnings,
    }


def _observe_named_api(name: str, function):
    @wraps(function)
    def observed(family: Family, *args, **kwargs):
        started = perf_counter()
        try:
            result = function(family, *args, **kwargs)
        except Exception:
            metric_add(family.master.session_id, "causal_graph.named_api.calls", labels={"api": name, "outcome": "failure"})
            metric_add(family.master.session_id, "causal_graph.named_api.duration_ms", (perf_counter() - started) * 1000, metric_type="observation", labels={"api": name})
            raise
        metric_add(family.master.session_id, "causal_graph.named_api.calls", labels={"api": name, "outcome": "success"})
        metric_add(family.master.session_id, "causal_graph.named_api.duration_ms", (perf_counter() - started) * 1000, metric_type="observation", labels={"api": name})
        return result
    return observed


budget = _observe_named_api("budget", budget)
goods = _observe_named_api("goods", goods)
memory_usage = _observe_named_api("memory_usage", memory_usage)
list_tools = _observe_named_api("list_tools", list_tools)
tool_usage = _observe_named_api("tool_usage", tool_usage)
turns = _observe_named_api("turns", turns)
subagents = _observe_named_api("subagents", subagents)
gitops = _observe_named_api("gitops", gitops)
