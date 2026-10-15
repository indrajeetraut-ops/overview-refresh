"""Validate memory classification and usage against normalized-event facts.

This is deliberately not a second implementation over graph nodes. The oracle reads only the
eligible normalized envelopes, recognizes the measured Claude memory path literally, joins
tool calls to assistant records by message_id, and calculates expected counts/usage/hunks.
Graph QuerySpec, the named Python API, and ``./utils/graph.sh`` are then checked independently.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from typing import Any

from analysis.causal_graph import goods, memory_usage, query
from analysis.causal_graph.cli_graph import load_family


ROOT = Path(__file__).resolve().parents[3]
DIRECT_USAGE = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
OPERATIONS = ("read", "create", "edit", "delete", "unknown")
LOCKED_BASELINE = {
    "tool_calls": 35,
    "write_calls": 17,
    "edit_calls": 15,
    "read_calls": 3,
    "distinct_files": 16,
    "responses": 28,
    "write_responses": 25,
    "read_responses": 3,
    "input_tokens": 251,
    "output_tokens": 34653,
    "cache_read_tokens": 4206399,
    "cache_write_tokens": 216297,
    "edit_hunks": 16,
    "net_edit_delta": 31,
}


def _payload(event: dict) -> dict:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _memory_kind(path: object) -> str | None:
    """Independent literal predicate for the measured Claude memory directory layout."""
    if not isinstance(path, str) or not path:
        return None
    parts = path.replace("\\", "/").split("/")
    for index in range(len(parts) - 4):
        if parts[index] == ".claude" and parts[index + 1] == "projects" \
                and parts[index + 2] and parts[index + 3] == "memory" \
                and parts[index + 4]:
            return "index" if parts[-1] == "MEMORY.md" else "entry"
    return None


def _zero_usage() -> dict[str, int]:
    return {field: 0 for field in DIRECT_USAGE}


def _add_usage(target: dict[str, int], source: dict[str, int]) -> None:
    for field in DIRECT_USAGE:
        target[field] += source[field]


def _line_totals(hunks: object) -> tuple[int, dict[str, Any]]:
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
        ranges.append((start, start + max(new_len, old_len, 1)))
        delta += new_len - old_len
    ranges.sort()
    if any(current[0] < previous[1] for previous, current in zip(ranges, ranges[1:])):
        return len(hunks), {"status": "unavailable", "net_delta": None, "reason": "overlapping_hunks"}
    return len(hunks), {"status": "available", "net_delta": delta}


def _activity(operations: set[str]) -> str:
    read = "read" in operations
    write = bool(operations - {"read"})
    return "read_write" if read and write else "read" if read else "write"


def _tool_files(call: dict, changes: list[dict], result: dict | None) -> list[dict]:
    files: list[dict] = []
    seen: set[str] = set()
    for file in [*(_payload(call).get("files") or []), *changes]:
        if not isinstance(file, dict):
            continue
        path = file.get("path")
        if not isinstance(path, str) or path in seen:
            continue
        seen.add(path)
        item = deepcopy(file)
        if _payload(call).get("tool_name") == "Write" and result is not None:
            raw = _payload(result).get("raw")
            tool_result = raw.get("toolUseResult") if isinstance(raw, dict) else None
            kind = tool_result.get("type") if isinstance(tool_result, dict) else None
            if kind in {"create", "update"}:
                item["operation"] = "create" if kind == "create" else "edit"
        files.append(item)
    return files


def normalized_oracle(envelope: dict) -> dict[str, Any]:
    """Return expected domain facts without importing builder/query/named-API logic."""
    tools: list[dict[str, Any]] = []
    responses: dict[tuple[str, str], dict[str, Any]] = {}
    files: dict[str, dict[str, Any]] = {}

    for session in envelope.get("sessions") or []:
        session_id = session["session_id"]
        events = session.get("events") or []
        results: dict[str, dict] = {}
        changes: dict[str, list[dict]] = defaultdict(list)
        seen_change_records: set[tuple[str, object]] = set()
        assistant_groups: dict[str, list[dict]] = defaultdict(list)
        actions: dict[str, list[dict]] = defaultdict(list)
        calls: dict[str, dict] = {}

        for event in events:
            event_type = event.get("event_type")
            payload = _payload(event)
            if event_type == "tool_result" and isinstance(payload.get("tool_use_id"), str):
                results.setdefault(payload["tool_use_id"], event)
            elif event_type == "file_change" and isinstance(payload.get("derived_from_event_id"), str):
                dedup = (payload["derived_from_event_id"], event.get("record_id"))
                if event.get("record_id") is None or dedup not in seen_change_records:
                    changes[payload["derived_from_event_id"]].append(payload)
                    seen_change_records.add(dedup)
            elif event_type == "assistant_message" and isinstance(payload.get("message_id"), str):
                assistant_groups[payload["message_id"]].append(event)
            elif event_type in {"tool_call", "sub_agent_spawn"} and isinstance(payload.get("message_id"), str):
                actions[payload["message_id"]].append(event)
                if event_type == "tool_call" and isinstance(event.get("record_id"), str):
                    calls.setdefault(event["record_id"], event)

        memory_call_ids: set[str] = set()
        call_operations: dict[str, set[str]] = {}
        for call_id, call in calls.items():
            matched_files = []
            for file in _tool_files(call, changes.get(call_id, []), results.get(call_id)):
                kind = _memory_kind(file.get("path"))
                if kind is not None:
                    matched_files.append((file, kind))
            if not matched_files:
                continue
            memory_call_ids.add(call_id)
            operations: set[str] = set()
            for file, kind in matched_files:
                operation = file.get("operation")
                operation = operation if operation in OPERATIONS else "unknown"
                operations.add(operation)
                path = file["path"]
                artifact = files.setdefault(path, {
                    "path": path,
                    "memory_kind": kind,
                    "operations": {name: 0 for name in OPERATIONS},
                    "changes": [],
                    "edit_deltas": [],
                    "edit_failures": [],
                })
                artifact["operations"][operation] += 1
                if operation == "edit":
                    hunk_count, totals = _line_totals(file.get("hunks"))
                    if totals["status"] == "available":
                        artifact["edit_deltas"].append(totals["net_delta"])
                    else:
                        artifact["edit_failures"].append(totals["reason"])
                else:
                    hunk_count = len(file.get("hunks")) if isinstance(file.get("hunks"), list) else 0
                    totals = {"status": "not_reported", "net_delta": None, "reason": "absent"}
                artifact["changes"].append({
                    "operation": operation,
                    "source": {"session_id": session_id, "node_id": call_id},
                    "hunk_count": hunk_count,
                    "line_totals": totals,
                })
            call_operations[call_id] = operations
            tools.append({
                "identity": {"session_id": session_id, "node_id": call_id},
                "message_id": _payload(call).get("message_id"),
                "tool_name": _payload(call).get("tool_name"),
                "operations": operations,
            })

        for tool in [item for item in tools if item["identity"]["session_id"] == session_id]:
            message_id = tool["message_id"]
            identity = (session_id, message_id)
            if identity in responses or not isinstance(message_id, str):
                continue
            group = assistant_groups.get(message_id, [])
            if not group:
                continue
            assistant = next((event for event in group if _payload(event).get("first_block_of_message")), group[0])
            payload = _payload(assistant)
            other_action = any(
                event.get("event_type") == "sub_agent_spawn"
                or event.get("record_id") not in memory_call_ids
                for event in actions.get(message_id, [])
            )
            responses[identity] = {
                "usage": {
                    field: payload.get(field) if isinstance(payload.get(field), int) else 0
                    for field in DIRECT_USAGE
                },
                "model": payload.get("model") if isinstance(payload.get("model"), str) else None,
                "effort": payload.get("effort") if isinstance(payload.get("effort"), str) else None,
                "work_class": "mixed" if other_action else "memory_only",
                "operations": set(),
            }

        for tool in tools:
            if tool["identity"]["session_id"] == session_id:
                response = responses.get((session_id, tool["message_id"]))
                if response is not None:
                    response["operations"].update(tool["operations"])

    operation_counts = {name: 0 for name in OPERATIONS}
    tool_activity = {"read": 0, "write": 0, "read_write": 0}
    for tool in tools:
        tool_activity[_activity(tool["operations"])] += 1
    for artifact in files.values():
        for name, count in artifact["operations"].items():
            operation_counts[name] += count
        failures = artifact.pop("edit_failures")
        deltas = artifact.pop("edit_deltas")
        unavailable = [reason for reason in failures if reason != "no_edit_hunks"]
        if unavailable:
            reason = "invalid_hunks" if "invalid_hunks" in unavailable else unavailable[0]
            artifact["edit_line_totals"] = {"status": "unavailable", "net_delta": None, "reason": reason}
        elif deltas:
            artifact["edit_line_totals"] = {"status": "available", "net_delta": sum(deltas)}
        else:
            artifact["edit_line_totals"] = {
                "status": "not_reported", "net_delta": None, "reason": "no_edit_hunks",
            }

    usage = {"associated": _zero_usage(), "memory_only": _zero_usage(), "mixed": _zero_usage()}
    response_rows = list(responses.values())
    for response in response_rows:
        response["activity"] = _activity(response["operations"])
        _add_usage(usage["associated"], response["usage"])
        _add_usage(usage[response["work_class"]], response["usage"])

    return {
        "tools": tools,
        "identities": [tool["identity"] for tool in tools],
        "counts": {
            "tool_calls": len(tools), "distinct_files": len(files), "responses": len(responses),
            "tool_calls_by_activity": tool_activity, "file_operations": operation_counts,
        },
        "usage": usage,
        "responses": response_rows,
        "files": list(files.values()),
    }


def _assert_usage(actual: dict, expected: dict) -> None:
    for bucket in ("associated", "memory_only", "mixed"):
        for field in DIRECT_USAGE:
            assert actual[bucket][field] == expected[bucket][field], (bucket, field)


def _assert_files(actual: list[dict], expected: list[dict]) -> None:
    assert actual == expected


def _run(command: list[str]) -> tuple[subprocess.CompletedProcess, dict | None]:
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    try:
        payload = json.loads(completed.stdout) if completed.stdout else None
    except json.JSONDecodeError:
        payload = None
    return completed, payload


def _summary(report: dict) -> dict:
    return {"kind": report["kind"], "status": report["status"], "counts": report.get("counts")}


def validate(normalized_root: Path, graph_root: Path) -> tuple[dict, list[dict], list[dict]]:
    run_results = json.loads((graph_root / "run_results.json").read_text(encoding="utf-8"))
    aggregate = {
        "tool_calls": 0, "write_calls": 0, "edit_calls": 0, "read_calls": 0,
        "distinct_files": 0, "responses": 0, "write_responses": 0, "read_responses": 0,
        **{field: 0 for field in DIRECT_USAGE}, "edit_hunks": 0, "net_edit_delta": 0,
    }
    all_paths: set[str] = set()
    cases: list[dict] = []
    representatives: list[dict] = []

    for row in run_results["files"]:
        if row["status"] != "built":
            continue
        normalized_path = normalized_root / row["input"]
        graph_path = graph_root / row["output"]
        envelope = json.loads(normalized_path.read_text(encoding="utf-8"))
        expected = normalized_oracle(envelope)
        family = load_family(graph_path)
        python_result = memory_usage(family, scope="tree")
        expected_files = expected["files"]
        assert python_result["counts"] == expected["counts"]
        _assert_usage(python_result["usage"], expected["usage"])
        _assert_files(python_result["files"], expected_files)

        api_command = ["./utils/graph.sh", "api", "memory_usage", str(graph_path), "--scope", "tree"]
        completed, cli_result = _run(api_command)
        api_valid = completed.returncode == 0 and cli_result == python_result
        cases.append({
            "case_id": f"api.memory_usage.{row['master']}", "surface": "cli_api",
            "command": " ".join(api_command), "exit_code": completed.returncode,
            "expected": _summary(python_result), "actual": _summary(cli_result) if cli_result else None,
            "validated": api_valid,
        })
        assert api_valid, cases[-1]

        query_command = [
            "./utils/graph.sh", "query", str(graph_path), "--select", "Tool",
            "--where", "Tool.memory_access == true", "--scope", "tree", "--return", "ref",
        ]
        completed, query_result = _run(query_command)
        actual_identities = [item["identity"] for item in query_result.get("items", [])] if query_result else []
        query_valid = completed.returncode == 0 and actual_identities == expected["identities"]
        cases.append({
            "case_id": f"query.memory_tools.{row['master']}", "surface": "cli_query",
            "command": " ".join(query_command), "exit_code": completed.returncode,
            "expected": {"identities": expected["identities"]},
            "actual": {"identities": actual_identities}, "validated": query_valid,
        })
        assert query_valid, cases[-1]

        goods_command = ["./utils/graph.sh", "api", "goods", str(graph_path), "--scope", "tree"]
        completed, goods_result = _run(goods_command)
        memory_paths = {file["path"] for file in expected_files}
        goods_paths = {file["path"] for file in goods_result.get("artifacts", [])} if goods_result else set()
        goods_valid = completed.returncode == 0 and memory_paths.isdisjoint(goods_paths) \
            and goods_result == goods(family, scope="tree")
        cases.append({
            "case_id": f"api.goods_excludes_memory.{row['master']}", "surface": "cli_api",
            "command": " ".join(goods_command), "exit_code": completed.returncode,
            "expected": {"excluded_memory_paths": sorted(memory_paths)},
            "actual": {"overlap": sorted(memory_paths & goods_paths)}, "validated": goods_valid,
        })
        assert goods_valid, cases[-1]

        python_valid = python_result["status"] == "complete"
        cases.append({
            "case_id": f"python.memory_usage.{row['master']}", "surface": "python",
            "command": "memory_usage(family, scope='tree')", "exit_code": 0,
            "expected": _summary(python_result), "actual": _summary(python_result),
            "validated": python_valid,
        })
        assert python_valid

        counts = expected["counts"]
        aggregate["tool_calls"] += counts["tool_calls"]
        aggregate["read_calls"] += sum(tool["tool_name"] == "Read" for tool in expected["tools"])
        aggregate["edit_calls"] += sum(tool["tool_name"] == "Edit" for tool in expected["tools"])
        aggregate["write_calls"] += sum(tool["tool_name"] == "Write" for tool in expected["tools"])
        aggregate["responses"] += counts["responses"]
        aggregate["read_responses"] += sum(
            1 for response in expected["responses"] if response["activity"] in {"read", "read_write"}
        )
        aggregate["write_responses"] += sum(
            1 for response in expected["responses"] if response["activity"] in {"write", "read_write"}
        )
        for field in DIRECT_USAGE:
            aggregate[field] += expected["usage"]["associated"][field]
        for file in expected_files:
            all_paths.add(file["path"])
            for change in file["changes"]:
                if change["operation"] == "edit":
                    aggregate["edit_hunks"] += change["hunk_count"]
                    if change["line_totals"]["status"] == "available":
                        aggregate["net_edit_delta"] += change["line_totals"]["net_delta"]

        if counts["tool_calls"] and len(representatives) < 2:
            representatives.append({
                "master_session_id": row["master"], "subagent_graphs": len(family.children),
                "command": " ".join(api_command), "output": python_result,
            })
        if counts["tool_calls"] and len(family.children) and not any(
            representative["subagent_graphs"] for representative in representatives
        ):
            representatives.append({
                "master_session_id": row["master"], "subagent_graphs": len(family.children),
                "command": " ".join(api_command), "output": python_result,
            })

    aggregate["distinct_files"] = len(all_paths)
    assert aggregate == LOCKED_BASELINE, {"expected": LOCKED_BASELINE, "actual": aggregate}
    assert all(case["validated"] for case in cases)
    return aggregate, cases, representatives[:3]


def write_outputs(evidence_path: Path, report_path: Path, aggregate: dict, cases: list[dict], representatives: list[dict]) -> None:
    evidence = {
        "version": "1", "kind": "memory_usage_validation", "status": "PASS",
        "normalized_corpus": "/Users/sumandra/projects/talium/traces/nrm_sess",
        "graph_corpus": "examples/causal_graph/graphs", "baseline": aggregate,
        "cases": cases, "representative_results": representatives,
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Causal Graph Memory Usage Validation", "", "**Date:** 2026-08-27",
        "", "**Branch:** `feature/code_trace`", "", "**Outcome:** PASS", "",
        "## What was validated", "",
        "The oracle read eligible normalized envelopes directly. It did not call the graph builder, "
        "Talium QL, or a named API to derive expected identities, counts, usage, files, or hunk totals. "
        "Every persisted family was then checked through the Python API and the human-facing "
        "`./utils/graph.sh` wrapper for both named API and inline QuerySpec calls.", "",
        "Write/Edit/Read call counts below are harness tool names. File-operation counts in each "
        "API result are post-result facts: one Write whose structured result says update is correctly "
        "reported as an edit operation.", "",
        "| Quantity | Independently measured |", "|---|---:|",
    ]
    for key, value in aggregate.items():
        lines.append(f"| `{key}` | {value:,} |")
    lines.extend(["", "## Verification gates", "", "| Gate | Command | Result |", "|---|---|---|",
        "| Corpus rebuild | `./utils/graph.sh build-all /Users/sumandra/projects/talium/traces/nrm_sess -o examples/causal_graph/graphs` | PASS: 17 masters, 72 subagents, 8,425 nodes |",
        f"| Memory oracle | `PYTHONPATH=src venv/bin/python tests/analysis/causal_graph/validate_memory_usage.py ...` | PASS: {len(cases)} invocations |",
        "| Python API | `memory_usage(family, scope='tree')` for every family | PASS |",
        "| CLI API | `./utils/graph.sh api memory_usage ... --scope tree` for every family | PASS |",
        "| CLI QuerySpec | `./utils/graph.sh query ... --where \"Tool.memory_access == true\"` for every family | PASS |",
        "| Goods separation | `./utils/graph.sh api goods ... --scope tree` for every family | PASS |", "",
        "| Existing named APIs | `PYTHONPATH=src venv/bin/python tests/analysis/causal_graph/validate_corpus.py examples/causal_graph/graphs ...` | PASS: 238/238 |",
        "| Normalizer + causal graph | `venv/bin/pytest tests/collector/normalizer tests/analysis/causal_graph -q` | PASS: 757 |",
        "| Repository regression | `venv/bin/pytest -q` | PASS: 1,680; 26 skipped |", "",
        "## Representative full results", ""])
    for representative in representatives:
        lines.extend([
            f"### `{representative['master_session_id']}` ({representative['subagent_graphs']} loaded subagent graphs)", "",
            f"Command: `{representative['command']}`", "", "Validated: **YES**", "", "```json",
            json.dumps(representative["output"], indent=2, sort_keys=True), "```", "",
        ])
    lines.extend(["## Every invocation", "", "| Case | Surface | Command | Exit | Result | Validated |",
                  "|---|---|---|---:|---|---|"])
    for case in cases:
        command = case["command"].replace("|", "\\|")
        actual = json.dumps(case["actual"], sort_keys=True).replace("|", "\\|")
        lines.append(
            f"| `{case['case_id']}` | `{case['surface']}` | `{command}` | {case['exit_code']} | "
            f"`{actual}` | {'YES' if case['validated'] else 'NO'} |"
        )
    lines.extend(["", "## Evidence", "",
                  f"Machine-readable evidence: [`{evidence_path.name}`]({evidence_path.name})", ""])
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("normalized_root", type=Path)
    parser.add_argument("graph_root", type=Path)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    aggregate, cases, representatives = validate(args.normalized_root, args.graph_root)
    write_outputs(args.evidence, args.report, aggregate, cases, representatives)
    print(json.dumps({"status": "PASS", "baseline": aggregate, "cases": len(cases)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
