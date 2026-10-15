"""Audit every named API through ``./utils/graph.sh`` against independent graph facts."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any, Callable, Iterable

import structlog

from analysis.causal_graph import (
    NamedApiError, budget, gitops, goods, list_tools, subagents, tool_usage, turns,
)
from analysis.causal_graph.cli_graph import load_family
from analysis.causal_graph.field_registry import FieldRegistry
from analysis.causal_graph.models import Family, Graph, Node, NodeType


_ROOT = Path(__file__).resolve().parents[3]
_WRAPPER = "./utils/graph.sh"
_USAGE_KEYS = (
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "reasoning_tokens",
)
_ARTIFACT_CLASSES = (
    "code", "documentation", "config", "markup", "data", "notebook", "template",
    "document", "spreadsheet", "presentation", "pdf", "unknown",
)


@dataclass(frozen=True)
class Case:
    case_id: str
    api: str
    graph_path: Path
    cli_args: tuple[str, ...] = ()
    python_args: tuple[tuple[str, Any], ...] = ()
    assertions: tuple[str, ...] = ()
    expected_error: str | None = None

    @property
    def kwargs(self) -> dict[str, Any]:
        return dict(self.python_args)

    @property
    def command(self) -> list[str]:
        return [_WRAPPER, "api", self.api, str(self.graph_path), *self.cli_args]


def _nodes(graph: Graph, node_type: NodeType) -> list[Node]:
    return [node for node in graph.nodes.values() if node.type is node_type]


def _descendants(graph: Graph, root: Node) -> list[Node]:
    found: list[Node] = []
    pending, seen = list(reversed(root.children)), set()
    while pending:
        node_id = pending.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        node = graph.nodes.get(node_id)
        if node is not None:
            found.append(node)
            pending.extend(reversed(node.children))
    return found


def _graphs_for_scope(family: Family, scope: str | dict[str, str]) -> list[Graph]:
    if scope == "self":
        return [family.master]
    if scope == "subagents":
        return list(family.children.values())
    if scope == "tree":
        return list(family.all_graphs())
    if isinstance(scope, dict) and scope.get("kind") == "session":
        graph = family.resolve(scope["session_id"])
        assert graph is not None
        return [graph]
    raise AssertionError(f"unsupported oracle scope: {scope!r}")


def _scope(kwargs: dict[str, Any], *, default: str = "tree") -> str | dict[str, str]:
    return kwargs.get("scope", default)


def _tool_nodes(family: Family, kwargs: dict[str, Any]) -> list[tuple[Graph, Node]]:
    return [
        (graph, node)
        for graph in _graphs_for_scope(family, _scope(kwargs))
        for node in graph.nodes.values()
        if node.type is NodeType.TOOL
    ]


def _zero_usage() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "reasoning_included_in_output_tokens": True,
    }


def _add_usage(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in _USAGE_KEYS:
        target[key] += source[key]


def _assistant_usage(node: Node) -> dict[str, Any]:
    result = _zero_usage()
    stored = {
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "cache_read_tokens": "cache_read_tokens",
        "cache_write_tokens": "cache_write_tokens",
        "reasoning_tokens": "estimated_reasoning_tokens",
    }
    for public, attr in stored.items():
        value = node.attrs.get(attr)
        result[public] = value if isinstance(value, int) and not isinstance(value, bool) else 0
    return result


def _raw_budget_rows(family: Family, kwargs: dict[str, Any]) -> list[dict[str, Any]]:
    by = kwargs.get("by", "model")
    wanted_model = kwargs.get("model")
    wanted_mode = kwargs.get("permission_mode")
    wanted_turn = kwargs.get("turn")
    rows: dict[tuple[tuple[str, Any], ...], dict[str, Any]] = {}
    for graph in _graphs_for_scope(family, _scope(kwargs)):
        for turn in _nodes(graph, NodeType.TURN):
            turn_num = turn.attrs.get("turn_num")
            if not isinstance(turn_num, int):
                continue
            if wanted_turn and wanted_turn != {"session_id": graph.session_id, "turn_num": turn_num}:
                continue
            mode = turn.attrs.get("permission_mode")
            if wanted_mode is not None and mode != wanted_mode:
                continue
            assistants = [node for node in _descendants(graph, turn) if node.type is NodeType.ASSISTANT]
            if not assistants:
                continue
            model_totals: dict[str | None, dict[str, Any]] = {}
            turn_total = _zero_usage()
            for assistant in assistants:
                model = assistant.attrs.get("model")
                model = model if isinstance(model, str) else None
                usage = _assistant_usage(assistant)
                _add_usage(turn_total, usage)
                _add_usage(model_totals.setdefault(model, _zero_usage()), usage)
            if wanted_model is not None:
                if wanted_model not in model_totals:
                    continue
                contributions = [(wanted_model, model_totals[wanted_model])]
            elif by == "model":
                contributions = list(model_totals.items())
            else:
                contributions = [(None, turn_total)]
            for model, usage in contributions:
                if by == "model":
                    key = {"model": model}
                elif by == "permission_mode":
                    key = {"permission_mode": mode if isinstance(mode, str) else None}
                elif by == "turn":
                    key = {"session_id": graph.session_id, "turn_num": turn_num}
                else:
                    key = {"session_id": graph.session_id}
                encoded = tuple(sorted(key.items()))
                row = rows.setdefault(encoded, {"key": key, "usage": _zero_usage()})
                _add_usage(row["usage"], usage)
    return sorted(rows.values(), key=lambda row: json.dumps(row["key"], sort_keys=True))


def _assert_budget(family: Family, kwargs: dict[str, Any], result: dict[str, Any]) -> None:
    assert result["rows"] == _raw_budget_rows(family, kwargs)
    if _scope(kwargs) == "tree" and kwargs.get("by", "model") == "model" and not any(
        kwargs.get(key) is not None for key in ("model", "permission_mode", "turn")
    ):
        tree = {row["key"]["model"]: row["usage"] for row in result["rows"]}
        self_rows = _raw_budget_rows(family, {**kwargs, "scope": "self"})
        sub_rows = _raw_budget_rows(family, {**kwargs, "scope": "subagents"})
        self_by_model = {row["key"]["model"]: row["usage"] for row in self_rows}
        sub_by_model = {row["key"]["model"]: row["usage"] for row in sub_rows}
        assert set(tree) == set(self_by_model) | set(sub_by_model)
        for model, usage in tree.items():
            for key in _USAGE_KEYS:
                assert usage[key] == self_by_model.get(model, _zero_usage())[key] + sub_by_model.get(model, _zero_usage())[key]


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
        ranges.append((start, start + max(new_len, old_len, 1)))
        delta += new_len - old_len
    ranges.sort()
    if any(current[0] < previous[1] for previous, current in zip(ranges, ranges[1:])):
        return len(hunks), {"status": "unavailable", "net_delta": None, "reason": "overlapping_hunks"}
    return len(hunks), {"status": "available", "net_delta": delta}


def _raw_goods(family: Family, kwargs: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    for graph, tool in _tool_nodes(family, kwargs):
        files = tool.attrs.get("files")
        if not isinstance(files, list):
            continue
        for file in files:
            if not isinstance(file, dict):
                continue
            if file.get("is_memory") is True:
                continue
            if kwargs.get("artifact_class") is not None and file.get("artifact_class") != kwargs["artifact_class"]:
                continue
            if kwargs.get("extension") is not None and file.get("extension") != kwargs["extension"]:
                continue
            path = file.get("path")
            if not isinstance(path, str) or not path or file.get("operation") == "read":
                continue
            operation = file.get("operation")
            if operation not in {"create", "edit", "delete", "unknown"}:
                operation = "unknown"
            artifact = artifacts.setdefault(path, {
                "path": path,
                "extension": file.get("extension") if isinstance(file.get("extension"), str) else None,
                "artifact_class": file.get("artifact_class") if isinstance(file.get("artifact_class"), str) else None,
                "changes": [],
            })
            hunk_count, totals = _line_totals(file.get("hunks"))
            artifact["changes"].append({
                "operation": operation,
                "source": {"session_id": graph.session_id, "node_id": tool.id},
                "hunk_count": hunk_count,
                "line_totals": totals,
            })
    return list(artifacts.values())


def _assert_goods(family: Family, kwargs: dict[str, Any], result: dict[str, Any]) -> None:
    assert result["artifacts"] == _raw_goods(family, kwargs)
    assert len({row["path"] for row in result["artifacts"]}) == len(result["artifacts"])


def _raw_tool_inventory(tools: Iterable[tuple[Graph, Node]]) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], int] = {}
    for _, tool in tools:
        name = tool.attrs.get("tool_name")
        if isinstance(name, str) and name:
            counts[("harness", name)] = counts.get(("harness", name), 0) + 1
        commands = tool.attrs.get("cmd")
        if isinstance(commands, list):
            for command in dict.fromkeys(item for item in commands if isinstance(item, str) and item):
                counts[("command", command)] = counts.get(("command", command), 0) + 1
    order = {"harness": 0, "command": 1}
    return sorted((
        {"tool_class": kind, "name": name, "invocation_count": count}
        for (kind, name), count in counts.items()
    ), key=lambda row: (order[row["tool_class"]], -row["invocation_count"], row["name"]))


def _assert_list_tools(family: Family, kwargs: dict[str, Any], result: dict[str, Any]) -> None:
    expected = _raw_tool_inventory(_tool_nodes(family, kwargs))
    assert result["tools"] == expected
    assert result["count"] == len(expected)


def _raw_tool_usage(family: Family, kwargs: dict[str, Any]) -> list[dict[str, Any]]:
    registry = FieldRegistry()
    pattern = re.compile(kwargs["invocation_matches"]) if kwargs.get("invocation_matches") else None
    records: list[dict[str, Any]] = []
    for graph, tool in _tool_nodes(family, kwargs):
        harness = tool.attrs.get("tool_name")
        commands = tool.attrs.get("cmd")
        commands = [item for item in commands if isinstance(item, str)] if isinstance(commands, list) else []
        tool_class, name = kwargs.get("tool_class"), kwargs.get("name")
        if tool_class == "harness" and name is not None and harness != name:
            continue
        if tool_class == "command" and (not commands or (name is not None and name not in commands)):
            continue
        outcome = registry.value(tool, "Tool.outcome")
        if kwargs.get("outcome", "all") != "all" and outcome != kwargs["outcome"]:
            continue
        input_ = tool.attrs.get("input")
        invocation = input_.get("command") if isinstance(input_, dict) and isinstance(input_.get("command"), str) else None
        if pattern and (invocation is None or pattern.search(invocation) is None):
            continue
        result = tool.attrs.get("result")
        records.append({
            "identity": {"session_id": graph.session_id, "node_id": tool.id},
            "harness_name": harness if isinstance(harness, str) else "",
            "commands": commands,
            "invocation": invocation,
            "outcome": outcome,
            "duration_ms": tool.attrs.get("duration_ms") if isinstance(tool.attrs.get("duration_ms"), int) else None,
            "exit_status": tool.attrs.get("exit_status") if isinstance(tool.attrs.get("exit_status"), int) else None,
            "denial_kind": tool.attrs.get("tool_denial_kind") if isinstance(tool.attrs.get("tool_denial_kind"), str) else None,
            "failure_message": result.get("error_text") if isinstance(result, dict) and isinstance(result.get("error_text"), str) else None,
        })
    return records


def _assert_tool_usage(family: Family, kwargs: dict[str, Any], result: dict[str, Any]) -> None:
    expected = _raw_tool_usage(family, kwargs)
    limit = kwargs.get("limit") or 10_000
    assert result["count"] == len(expected)
    assert result["tools"] == expected[:limit]
    assert result["returned_count"] == min(len(expected), limit)
    assert result["truncated"] is (len(expected) > limit)


def _turn_node(family: Family, turn_id: dict[str, Any]) -> tuple[Graph, Node]:
    graph = family.resolve(turn_id["session_id"])
    assert graph is not None
    turn = next(node for node in graph.nodes.values() if node.type is NodeType.TURN and node.attrs.get("turn_num") == turn_id["turn_num"])
    return graph, turn


def _assert_turns(family: Family, kwargs: dict[str, Any], result: dict[str, Any]) -> None:
    graph, turn = _turn_node(family, kwargs["turn_id"])
    descendants = _descendants(graph, turn)
    tools = [node for node in descendants if node.type is NodeType.TOOL]
    assistants = [node for node in descendants if node.type is NodeType.ASSISTANT]
    usage = _zero_usage()
    for assistant in assistants:
        _add_usage(usage, _assistant_usage(assistant))
    assert result["turn"]["identity"] == kwargs["turn_id"]
    assert result["turn"]["usage"] == usage
    assert result["turn"]["tool_count"] == len(tools)
    assert result["turn"]["tools_used"] == _raw_tool_inventory((graph, tool) for tool in tools)
    assert result["turn"]["subagent_spawns"]["count"] == sum(node.type is NodeType.SPAWN for node in descendants)
    assert result["turn"]["mcp_invocations"]["count"] == sum(
        isinstance(node.attrs.get("tool_name"), str) and node.attrs["tool_name"].startswith("mcp__") for node in tools
    )
    node_type = kwargs.get("node_type")
    if node_type is None:
        assert "nodes" not in result
    else:
        expected = descendants if node_type == "node" else [node for node in descendants if node.type.value == node_type]
        assert [row["identity"]["node_id"] for row in result["nodes"]] == [node.id for node in expected]


def _assert_subagents(family: Family, kwargs: dict[str, Any], result: dict[str, Any]) -> None:
    spawns = _nodes(family.master, NodeType.SPAWN)
    assert result["count"] == len(spawns)
    assert [row["spawn_id"] for row in result["subagents"]] == [spawn.id for spawn in spawns]
    assert all(row["turn_id"]["session_id"] == family.master.session_id for row in result["subagents"])


def _raw_gitops(family: Family, kwargs: dict[str, Any]) -> list[dict[str, Any]]:
    detail_fields = {
        "commit": ("sha", "kind"), "push": ("branch",),
        "pr": ("number", "url", "action"), "branch": ("ref", "action"),
    }
    records: list[dict[str, Any]] = []
    for graph, tool in _tool_nodes(family, kwargs):
        result = tool.attrs.get("result")
        git = result.get("git") if isinstance(result, dict) else None
        operation = git.get("op") if isinstance(git, dict) else None
        if operation not in detail_fields or (kwargs.get("operation") and operation != kwargs["operation"]):
            continue
        details: dict[str, Any] = {}
        for field in detail_fields[operation]:
            value = git.get(field)
            details[field] = value if (isinstance(value, int) and field == "number") or isinstance(value, str) else None
        input_ = tool.attrs.get("input")
        commands = tool.attrs.get("cmd")
        records.append({
            "identity": {"session_id": graph.session_id, "node_id": tool.id},
            "operation": operation,
            "harness_name": tool.attrs.get("tool_name") if isinstance(tool.attrs.get("tool_name"), str) else "",
            "commands": [item for item in commands if isinstance(item, str)] if isinstance(commands, list) else [],
            "invocation": input_.get("command") if isinstance(input_, dict) and isinstance(input_.get("command"), str) else None,
            "details": details,
        })
    return records


def _assert_gitops(family: Family, kwargs: dict[str, Any], result: dict[str, Any]) -> None:
    expected = _raw_gitops(family, kwargs)
    limit = kwargs.get("limit") or 10_000
    assert result["count"] == len(expected)
    assert result["operations"] == expected[:limit]
    assert result["returned_count"] == min(len(expected), limit)
    assert result["truncated"] is (len(expected) > limit)


_PYTHON_APIS: dict[str, Callable[..., dict[str, Any]]] = {
    "budget": budget, "goods": goods, "list_tools": list_tools,
    "tool_usage": tool_usage, "turns": turns, "subagents": subagents, "gitops": gitops,
}
_SEMANTIC_ASSERTIONS = {
    "budget": _assert_budget, "goods": _assert_goods, "list_tools": _assert_list_tools,
    "tool_usage": _assert_tool_usage, "turns": _assert_turns,
    "subagents": _assert_subagents, "gitops": _assert_gitops,
}


def _summary(result: dict[str, Any]) -> str:
    kind = result["kind"]
    if kind == "budget":
        return f"kind=budget rows={len(result['rows'])} status={result['status']}"
    if kind == "goods":
        return f"kind=goods artifacts={len(result['artifacts'])} status={result['status']}"
    if kind == "tool_list":
        return f"kind=tool_list count={result['count']} status={result['status']}"
    if kind == "tool_usage":
        return f"kind=tool_usage count={result['count']} returned={result['returned_count']} truncated={str(result['truncated']).lower()}"
    if kind == "turns":
        return f"kind=turns tool_count={result['turn']['tool_count']} nodes={len(result.get('nodes', []))}"
    if kind == "subagents":
        return f"kind=subagents count={result['count']}"
    return f"kind=gitops count={result['count']} returned={result['returned_count']} truncated={str(result['truncated']).lower()}"


def _run_case(case: Case, family: Family, *, verbose: bool) -> dict[str, Any]:
    command_text = shlex.join(case.command)
    expected = "invalid" if case.expected_error else "valid"
    record: dict[str, Any] = {
        "case_id": case.case_id,
        "api": case.api,
        "command": command_text,
        "expected": expected,
        "semantic_assertions": list(case.assertions),
    }
    if verbose:
        print(f"CASE {case.case_id}")
        print(f"COMMAND {command_text}")
        print(f"EXPECTED {expected}")
    completed = subprocess.run(case.command, cwd=_ROOT, text=True, capture_output=True, check=False)
    record["exit_code"] = completed.returncode
    try:
        if case.expected_error:
            assert completed.returncode != 0
            assert not completed.stdout.strip(), "invalid command emitted a successful result envelope"
            error = json.loads(completed.stderr.strip().splitlines()[-1])
            assert error["api"] == case.api and error["code"] == case.expected_error
            try:
                _PYTHON_APIS[case.api](family, **case.kwargs)
            except NamedApiError as exc:
                assert exc.api == case.api and exc.code == case.expected_error
            else:
                raise AssertionError("Python API accepted the invalid case")
            record["result"] = f"api={error['api']} code={error['code']}"
        else:
            assert completed.returncode == 0, completed.stderr
            result = json.loads(completed.stdout)
            assert result == _PYTHON_APIS[case.api](family, **case.kwargs)
            _SEMANTIC_ASSERTIONS[case.api](family, case.kwargs, result)
            record["result"] = _summary(result)
        record["status"] = "PASS"
    except Exception as exc:  # keep running so the evidence exposes every failure
        record["status"] = "FAIL"
        record["failure"] = f"{type(exc).__name__}: {exc}"
    if verbose:
        print(f"OUTPUT {record.get('result', record.get('failure'))}")
        print(f"{expected.upper()} {str(record['status'] == 'PASS').lower()}")
    return record


def _first_turn(family: Family, *, child: bool = False) -> dict[str, Any]:
    graphs = list(family.children.values()) if child else [family.master]
    for graph in [*graphs, *family.all_graphs()]:
        turns_ = sorted(
            (node for node in graph.nodes.values() if node.type is NodeType.TURN and isinstance(node.attrs.get("turn_num"), int)),
            key=lambda node: node.attrs["turn_num"],
        )
        if turns_:
            return {"session_id": graph.session_id, "turn_num": turns_[0].attrs["turn_num"]}
    raise AssertionError("family has no public turn")


def _turn_with(family: Family, node_type: NodeType, *, mcp: bool = False) -> dict[str, Any]:
    for graph in family.all_graphs():
        for turn in _nodes(graph, NodeType.TURN):
            if any(
                node.type is node_type
                and (not mcp or (isinstance(node.attrs.get("tool_name"), str) and node.attrs["tool_name"].startswith("mcp__")))
                for node in _descendants(graph, turn)
            ):
                return {"session_id": graph.session_id, "turn_num": turn.attrs["turn_num"]}
    raise AssertionError(f"no turn contains {node_type.value}")


def _cli_scope(scope: str | dict[str, str]) -> str:
    return f"session:{scope['session_id']}" if isinstance(scope, dict) else scope


def _case(
    case_id: str,
    api: str,
    path: Path,
    *cli_args: str,
    expected_error: str | None = None,
    assertions: tuple[str, ...] | None = None,
    **python_args: Any,
) -> Case:
    if assertions is None:
        assertions = (
            ("nonzero_exit", "empty_stdout", "named_error_envelope", "python_error_code_equality")
            if expected_error
            else ("python_envelope_equality", f"raw_graph_{api}_oracle")
        )
    return Case(
        case_id=case_id,
        api=api,
        graph_path=path,
        cli_args=tuple(cli_args),
        python_args=tuple(python_args.items()),
        assertions=assertions,
        expected_error=expected_error,
    )


def _baseline_cases(paths: list[Path], families: dict[Path, Family]) -> list[Case]:
    cases: list[Case] = []
    for path in paths:
        family = families[path]
        slug = family.master.session_id
        turn_id = _first_turn(family)
        for api in ("budget", "goods", "list_tools", "tool_usage"):
            cases.append(_case(f"baseline.{api}.{slug}", api, path))
        cases.append(_case(
            f"baseline.turns.{slug}", "turns", path,
            "--session-id", turn_id["session_id"], "--turn-num", str(turn_id["turn_num"]),
            turn_id=turn_id,
        ))
        cases.append(_case(f"baseline.subagents.{slug}", "subagents", path))
        cases.append(_case(f"baseline.gitops.{slug}", "gitops", path))
    return cases


def _scope_cases(prefix: str, api: str, path: Path, family: Family) -> list[Case]:
    child_id = sorted(family.children)[0]
    scopes: list[str | dict[str, str]] = [
        "self", "subagents",
        {"kind": "session", "session_id": family.master.session_id},
        {"kind": "session", "session_id": child_id},
    ]
    return [
        _case(
            f"{prefix}.scope-{_cli_scope(scope).replace(':', '-')}", api, path,
            "--scope", _cli_scope(scope), scope=scope,
        )
        for scope in scopes
    ]


def _assistant_contexts(family: Family) -> list[tuple[Graph, Node, str, str | None]]:
    contexts: list[tuple[Graph, Node, str, str | None]] = []
    for graph in family.all_graphs():
        for turn in _nodes(graph, NodeType.TURN):
            models = sorted({
                node.attrs["model"] for node in _descendants(graph, turn)
                if node.type is NodeType.ASSISTANT and isinstance(node.attrs.get("model"), str)
            })
            mode = turn.attrs.get("permission_mode")
            for model in models:
                contexts.append((graph, turn, model, mode if isinstance(mode, str) else None))
    return contexts


def _focused_budget(path: Path, family: Family) -> list[Case]:
    cases = _scope_cases("budget", "budget", path, family)
    contexts = _assistant_contexts(family)
    graph, turn, model, mode = next(item for item in contexts if item[3] is not None)
    child_context = next(item for item in contexts if item[0].session_id in family.children)
    turn_id = {"session_id": graph.session_id, "turn_num": turn.attrs["turn_num"]}
    child_turn = {"session_id": child_context[0].session_id, "turn_num": child_context[1].attrs["turn_num"]}
    cases.extend([
        _case("budget.by-permission-mode", "budget", path, "--by", "permission_mode", by="permission_mode"),
        _case("budget.by-turn", "budget", path, "--by", "turn", by="turn"),
        _case("budget.by-subagent", "budget", path, "--by", "subagent", by="subagent"),
        _case("budget.filter-model", "budget", path, "--model", model, model=model),
        _case("budget.filter-model-empty", "budget", path, "--model", "__missing_model__", model="__missing_model__"),
        _case("budget.filter-mode", "budget", path, "--permission-mode", mode, permission_mode=mode),
        _case("budget.filter-mode-empty", "budget", path, "--permission-mode", "__missing_mode__", permission_mode="__missing_mode__"),
        _case("budget.filter-master-turn", "budget", path, "--turn-session-id", turn_id["session_id"], "--turn-num", str(turn_id["turn_num"]), turn=turn_id),
        _case("budget.filter-child-turn", "budget", path, "--turn-session-id", child_turn["session_id"], "--turn-num", str(child_turn["turn_num"]), turn=child_turn),
        _case("budget.filter-model-mode", "budget", path, "--model", model, "--permission-mode", mode, model=model, permission_mode=mode),
        _case("budget.filter-model-turn", "budget", path, "--model", model, "--turn-session-id", turn_id["session_id"], "--turn-num", str(turn_id["turn_num"]), model=model, turn=turn_id),
        _case("budget.filter-model-mode-turn", "budget", path, "--model", model, "--permission-mode", mode, "--turn-session-id", turn_id["session_id"], "--turn-num", str(turn_id["turn_num"]), model=model, permission_mode=mode, turn=turn_id),
        _case("budget.filter-incompatible-empty", "budget", path, "--model", "__missing_model__", "--permission-mode", mode, "--turn-session-id", turn_id["session_id"], "--turn-num", str(turn_id["turn_num"]), model="__missing_model__", permission_mode=mode, turn=turn_id),
    ])
    return cases


def _focused_goods(path: Path, family: Family) -> list[Case]:
    cases = _scope_cases("goods", "goods", path, family)
    cases.extend(
        _case(f"goods.class-{artifact_class}", "goods", path, "--artifact-class", artifact_class, artifact_class=artifact_class)
        for artifact_class in _ARTIFACT_CLASSES
    )
    cases.extend([
        _case("goods.extension-py", "goods", path, "--extension", ".py", extension=".py"),
        _case("goods.extension-md", "goods", path, "--extension", ".md", extension=".md"),
        _case("goods.extension-empty", "goods", path, "--extension", ".not-present", extension=".not-present"),
        _case("goods.code-py", "goods", path, "--artifact-class", "code", "--extension", ".py", artifact_class="code", extension=".py"),
        _case("goods.code-md-empty", "goods", path, "--artifact-class", "code", "--extension", ".md", artifact_class="code", extension=".md"),
    ])
    return cases


def _focused_list_tools(path: Path, family: Family, zero_path: Path) -> list[Case]:
    return [*_scope_cases("list-tools", "list_tools", path, family), _case("list-tools.empty", "list_tools", zero_path)]


def _focused_tool_usage(
    path: Path,
    family: Family,
    mcp_path: Path,
    mcp_family: Family,
) -> list[Case]:
    tools = _tool_nodes(family, {})
    harnesses = sorted({node.attrs["tool_name"] for _, node in tools if isinstance(node.attrs.get("tool_name"), str)})
    native = "Read" if "Read" in harnesses else next(name for name in harnesses if not name.startswith("mcp__"))
    mcp = next(
        node.attrs["tool_name"]
        for _, node in _tool_nodes(mcp_family, {})
        if isinstance(node.attrs.get("tool_name"), str) and node.attrs["tool_name"].startswith("mcp__")
    )
    commands = sorted({command for _, node in tools for command in node.attrs.get("cmd", []) if isinstance(command, str)})
    command = "git" if "git" in commands else commands[0]
    invocation = next(
        node.attrs["input"]["command"] for _, node in tools
        if command in node.attrs.get("cmd", []) and isinstance(node.attrs.get("input"), dict)
        and isinstance(node.attrs["input"].get("command"), str)
    )
    token = re.escape(invocation.split()[0])
    cases = _scope_cases("tool-usage", "tool_usage", path, family)
    cases.extend([
        _case("tool-usage.harness-any", "tool_usage", path, "--tool-class", "harness", tool_class="harness"),
        _case("tool-usage.harness-native", "tool_usage", path, "--tool-class", "harness", "--name", native, tool_class="harness", name=native),
        _case("tool-usage.harness-mcp", "tool_usage", mcp_path, "--tool-class", "harness", "--name", mcp, tool_class="harness", name=mcp),
        _case("tool-usage.command-any", "tool_usage", path, "--tool-class", "command", tool_class="command"),
        _case("tool-usage.command-name", "tool_usage", path, "--tool-class", "command", "--name", command, tool_class="command", name=command),
        _case("tool-usage.outcome-success", "tool_usage", path, "--outcome", "success", outcome="success"),
        _case("tool-usage.outcome-failure", "tool_usage", path, "--outcome", "failure", outcome="failure"),
        _case("tool-usage.regex-match", "tool_usage", path, "--invocation-matches", token, invocation_matches=token),
        _case("tool-usage.regex-empty", "tool_usage", path, "--invocation-matches", "__not_a_real_invocation__", invocation_matches="__not_a_real_invocation__"),
        _case("tool-usage.limit-one", "tool_usage", path, "--limit", "1", limit=1),
        _case("tool-usage.limit-max", "tool_usage", path, "--limit", "10000", limit=10_000),
        _case("tool-usage.combined-harness", "tool_usage", path, "--tool-class", "harness", "--name", native, "--outcome", "success", "--limit", "2", tool_class="harness", name=native, outcome="success", limit=2),
        _case("tool-usage.combined-command", "tool_usage", path, "--tool-class", "command", "--name", command, "--invocation-matches", token, "--outcome", "success", "--limit", "2", tool_class="command", name=command, invocation_matches=token, outcome="success", limit=2),
        _case("tool-usage.selector-empty", "tool_usage", path, "--tool-class", "harness", "--name", "__missing_tool__", tool_class="harness", name="__missing_tool__"),
    ])
    return cases


def _focused_turns(path: Path, family: Family) -> list[Case]:
    master_turn = _first_turn(family)
    child_turn = _first_turn(family, child=True)
    spawn_turn = _turn_with(family, NodeType.SPAWN)
    mcp_turn = _turn_with(family, NodeType.TOOL, mcp=True)
    cases = [
        _case("turns.master-summary", "turns", path, "--session-id", master_turn["session_id"], "--turn-num", str(master_turn["turn_num"]), turn_id=master_turn),
        _case("turns.child-summary", "turns", path, "--session-id", child_turn["session_id"], "--turn-num", str(child_turn["turn_num"]), turn_id=child_turn),
        _case("turns.spawn-view", "turns", path, "--session-id", spawn_turn["session_id"], "--turn-num", str(spawn_turn["turn_num"]), "--node-type", "spawn", turn_id=spawn_turn, node_type="spawn"),
        _case("turns.mcp-summary", "turns", path, "--session-id", mcp_turn["session_id"], "--turn-num", str(mcp_turn["turn_num"]), turn_id=mcp_turn),
    ]
    for node_type in ("node", "user_prompt", "assistant", "tool"):
        cases.append(_case(
            f"turns.node-type-{node_type}", "turns", path,
            "--session-id", master_turn["session_id"], "--turn-num", str(master_turn["turn_num"]),
            "--node-type", node_type, turn_id=master_turn, node_type=node_type,
        ))
    return cases


def _focused_subagents(path: Path, zero_path: Path) -> list[Case]:
    return [
        _case("subagents.explicit-self", "subagents", path, "--scope", "self", scope="self"),
        _case("subagents.empty", "subagents", zero_path),
    ]


def _focused_gitops(path: Path, family: Family, branch_path: Path) -> list[Case]:
    cases = _scope_cases("gitops", "gitops", path, family)
    for operation in ("commit", "push", "pr"):
        cases.append(_case(f"gitops.operation-{operation}", "gitops", path, "--operation", operation, operation=operation))
    cases.extend([
        _case("gitops.operation-branch", "gitops", branch_path, "--operation", "branch", operation="branch"),
        _case("gitops.operation-empty", "gitops", branch_path, "--operation", "pr", operation="pr"),
        _case("gitops.limit-one", "gitops", path, "--limit", "1", limit=1),
        _case("gitops.limit-max", "gitops", path, "--limit", "10000", limit=10_000),
        _case("gitops.operation-limit", "gitops", path, "--operation", "commit", "--limit", "1", operation="commit", limit=1),
    ])
    return cases


def _invalid_cases(path: Path, family: Family) -> list[Case]:
    turn_id = _first_turn(family)
    cases: list[Case] = [
        _case("budget.invalid-axis", "budget", path, "--by", "assistant", expected_error="invalid_budget_axis", by="assistant"),
        _case("budget.empty-model", "budget", path, "--model", "", expected_error="invalid_filter", model=""),
        _case("budget.partial-turn-session", "budget", path, "--turn-session-id", turn_id["session_id"], expected_error="invalid_filter", turn={"session_id": turn_id["session_id"], "turn_num": None}),
        _case("budget.partial-turn-number", "budget", path, "--turn-num", "1", expected_error="invalid_filter", turn={"session_id": None, "turn_num": 1}),
        _case("budget.turn-zero", "budget", path, "--turn-session-id", turn_id["session_id"], "--turn-num", "0", expected_error="invalid_filter", turn={"session_id": turn_id["session_id"], "turn_num": 0}),
        _case("goods.invalid-class", "goods", path, "--artifact-class", "binary", expected_error="invalid_goods_filter", artifact_class="binary"),
        _case("goods.invalid-extension", "goods", path, "--extension", "py", expected_error="invalid_goods_filter", extension="py"),
        _case("tool-usage.invalid-class", "tool_usage", path, "--tool-class", "process", expected_error="invalid_tool_selector", tool_class="process"),
        _case("tool-usage.name-without-class", "tool_usage", path, "--name", "git", expected_error="invalid_tool_selector", name="git"),
        _case("tool-usage.empty-name", "tool_usage", path, "--tool-class", "harness", "--name", "", expected_error="invalid_tool_selector", tool_class="harness", name=""),
        _case("tool-usage.invalid-outcome", "tool_usage", path, "--outcome", "sometimes", expected_error="invalid_tool_outcome", outcome="sometimes"),
        _case("tool-usage.invalid-regex", "tool_usage", path, "--invocation-matches", "[", expected_error="invalid_invocation_regex", invocation_matches="["),
        _case("tool-usage.limit-zero", "tool_usage", path, "--limit", "0", expected_error="invalid_tool_limit", limit=0),
        _case("tool-usage.limit-over-max", "tool_usage", path, "--limit", "10001", expected_error="invalid_tool_limit", limit=10_001),
        _case("turns.empty-session", "turns", path, "--session-id", "", "--turn-num", "1", expected_error="invalid_turn_id", turn_id={"session_id": "", "turn_num": 1}),
        _case("turns.turn-zero", "turns", path, "--session-id", turn_id["session_id"], "--turn-num", "0", expected_error="invalid_turn_id", turn_id={"session_id": turn_id["session_id"], "turn_num": 0}),
        _case("turns.unloaded-session", "turns", path, "--session-id", "__missing_session__", "--turn-num", "1", expected_error="turn_not_loaded", turn_id={"session_id": "__missing_session__", "turn_num": 1}),
        _case("turns.unloaded-turn", "turns", path, "--session-id", turn_id["session_id"], "--turn-num", "999999", expected_error="turn_not_loaded", turn_id={"session_id": turn_id["session_id"], "turn_num": 999_999}),
        _case("turns.invalid-node-type", "turns", path, "--session-id", turn_id["session_id"], "--turn-num", str(turn_id["turn_num"]), "--node-type", "result", expected_error="invalid_turn_node_type", turn_id=turn_id, node_type="result"),
        _case("subagents.scope-tree", "subagents", path, "--scope", "tree", expected_error="invalid_subagent_scope", scope="tree"),
        _case("subagents.scope-subagents", "subagents", path, "--scope", "subagents", expected_error="invalid_subagent_scope", scope="subagents"),
        _case("subagents.scope-session", "subagents", path, "--scope", f"session:{family.master.session_id}", expected_error="invalid_subagent_scope", scope={"kind": "session", "session_id": family.master.session_id}),
        _case("subagents.scope-malformed", "subagents", path, "--scope", "invalid", expected_error="invalid_subagent_scope", scope="invalid"),
        _case("gitops.invalid-operation", "gitops", path, "--operation", "merge", expected_error="invalid_git_operation", operation="merge"),
        _case("gitops.limit-zero", "gitops", path, "--limit", "0", expected_error="invalid_git_limit", limit=0),
        _case("gitops.limit-over-max", "gitops", path, "--limit", "10001", expected_error="invalid_git_limit", limit=10_001),
    ]
    for api in ("budget", "goods", "list_tools", "tool_usage", "gitops"):
        cases.extend([
            _case(f"{api}.malformed-scope", api, path, "--scope", "invalid", expected_error="invalid_scope", scope="invalid"),
            _case(f"{api}.empty-session-scope", api, path, "--scope", "session:", expected_error="invalid_scope", scope={"kind": "session", "session_id": ""}),
        ])
    return cases


def _find_path(paths: list[Path], session_id: str) -> Path:
    return next(path for path in paths if path.stem == session_id)


def _first_zero_tool_family(paths: list[Path], families: dict[Path, Family]) -> Path:
    return next(path for path in paths if not any(
        node.type is NodeType.TOOL for graph in families[path].all_graphs() for node in graph.nodes.values()
    ))


def _first_zero_spawn_family(paths: list[Path], families: dict[Path, Family]) -> Path:
    return next(path for path in paths if not _nodes(families[path].master, NodeType.SPAWN))


def _first_mcp_family(paths: list[Path], families: dict[Path, Family]) -> Path:
    return next(path for path in paths if any(
        node.type is NodeType.TOOL
        and isinstance(node.attrs.get("tool_name"), str)
        and node.attrs["tool_name"].startswith("mcp__")
        for graph in families[path].all_graphs()
        for node in graph.nodes.values()
    ))


def _write_evidence(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8")


def validate(graph_root: Path, *, verbose: bool = False, evidence: Path | None = None) -> dict[str, Any]:
    run_results = json.loads((graph_root / "run_results.json").read_text(encoding="utf-8"))
    paths = sorted(graph_root / row["output"] for row in run_results["files"] if row["status"] == "built")
    families = {path: load_family(path) for path in paths}
    budget_path = _find_path(paths, "e97d97f5-19a0-4ccb-921c-117cffd9ab82")
    usage_path = _find_path(paths, "1cc0fed2-e532-4611-89cc-aa96d3af0e72")
    subagent_path = _find_path(paths, "0f5e45b9-ecc1-4773-913c-86cd341369d1")
    branch_path = _find_path(paths, "be15495e-c0b2-4849-abac-1dd69d7c1201")
    zero_tool_path = _first_zero_tool_family(paths, families)
    zero_spawn_path = _first_zero_spawn_family(paths, families)
    mcp_path = _first_mcp_family(paths, families)

    cases = _baseline_cases(paths, families)
    cases.extend(_focused_budget(budget_path, families[budget_path]))
    cases.extend(_focused_goods(usage_path, families[usage_path]))
    cases.extend(_focused_list_tools(usage_path, families[usage_path], zero_tool_path))
    cases.extend(_focused_tool_usage(usage_path, families[usage_path], mcp_path, families[mcp_path]))
    cases.extend(_focused_turns(budget_path, families[budget_path]))
    cases.extend(_focused_subagents(subagent_path, zero_spawn_path))
    cases.extend(_focused_gitops(usage_path, families[usage_path], branch_path))
    cases.extend(_invalid_cases(usage_path, families[usage_path]))

    records = [_run_case(case, families[case.graph_path], verbose=verbose) for case in cases]
    if evidence is not None:
        _write_evidence(evidence, records)
    assert all(record["command"].startswith("./utils/graph.sh ") for record in records)
    failures = [record for record in records if record["status"] != "PASS"]
    totals = {
        "families": len(paths),
        "cases": len(records),
        "valid": sum(record["expected"] == "valid" for record in records),
        "invalid": sum(record["expected"] == "invalid" for record in records),
        "passed": sum(record["status"] == "PASS" for record in records),
        "failed": len(failures),
        "by_api": {
            api: {
                "valid": sum(record["api"] == api and record["expected"] == "valid" for record in records),
                "invalid": sum(record["api"] == api and record["expected"] == "invalid" for record in records),
                "passed": sum(record["api"] == api and record["status"] == "PASS" for record in records),
            }
            for api in _PYTHON_APIS
        },
    }
    if failures:
        sample = "; ".join(f"{row['case_id']}: {row.get('failure')}" for row in failures[:5])
        raise AssertionError(f"{len(failures)} validation case(s) failed: {sample}")
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph_root", type=Path)
    parser.add_argument("--verbose", action="store_true", help="print every shell command and result")
    parser.add_argument("--evidence", type=Path, help="write one JSON evidence record per shell invocation")
    args = parser.parse_args()
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
    print(json.dumps(validate(args.graph_root, verbose=args.verbose, evidence=args.evidence), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
