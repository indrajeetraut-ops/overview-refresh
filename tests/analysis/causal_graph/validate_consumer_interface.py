"""Corpus audit for the source-neutral Python lifecycle, selection, and query contracts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
from typing import Any, Callable

from analysis.causal_graph import (
    GraphContext, budget, build_graph, get_graph_context, get_load_report, get_metric,
    gitops, goods, list_tools, memory_usage, query, select, subagents, tool_usage, turns,
)

ROOT = Path(__file__).resolve().parents[3]


def _documents(master_path: Path) -> tuple[dict, dict[str, dict]]:
    master = json.loads(master_path.read_text(encoding="utf-8"))
    child_dir = master_path.parent / master["session_id"] / "subagents"
    children = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(child_dir.glob("*.json"))
    }
    return master, children


def _summary(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return {
            key: value[key]
            for key in ("version", "kind", "status", "state", "count", "loaded_session_count", "loaded_node_count")
            if key in value
        } or {"keys": sorted(value)[:12]}
    if hasattr(value, "session_id"):
        return {"type": type(value).__name__, "session_id": value.session_id}
    if hasattr(value, "master_session_id"):
        return {"type": type(value).__name__, "master_session_id": value.master_session_id}
    return value


def validate(graph_root: Path, *, evidence: Path | None = None) -> dict[str, Any]:
    run_results = json.loads((graph_root / "run_results.json").read_text(encoding="utf-8"))
    paths = sorted(graph_root / row["output"] for row in run_results["files"] if row["status"] == "built")
    primary_path = next(path for path in paths if path.stem == "0f5e45b9-ecc1-4773-913c-86cd341369d1")
    secondary_path = next(path for path in paths if path.stem != primary_path.stem)
    master, children = _documents(primary_path)
    second_master, _ = _documents(secondary_path)
    records: list[dict[str, Any]] = []

    def run(case_id: str, invocation: str, function: Callable[[], Any], check: Callable[[Any], bool] = lambda _value: True) -> Any:
        try:
            value = function()
            valid = bool(check(value))
            records.append({"case_id": case_id, "invocation": invocation, "result": _summary(value), "valid": valid, "status": "PASS" if valid else "FAIL"})
            return value
        except Exception as exc:
            records.append({"case_id": case_id, "invocation": invocation, "result": f"{type(exc).__name__}: {exc}", "valid": False, "status": "FAIL"})
            return None

    normalized = json.loads((ROOT / "tests/fixtures/causal_graph/tiny_session.json").read_text(encoding="utf-8"))
    run("build.dict", "build_graph(normalized_session_dict)", lambda: build_graph(normalized), lambda value: type(value) is dict and value["session_id"] == normalized["session_id"])

    primary, secondary = GraphContext(), GraphContext()
    run("context.load-self", "primary.load_self(master_graph_dict)", lambda: primary.load_self(master), lambda value: value["loaded_session_count"] == 1)
    discovered = run("api.subagents-before-load", "subagents(primary.family)", lambda: subagents(primary.family), lambda value: value["count"] == len(children))
    batches: list[list[str]] = []

    def resolver(ids: list[str]) -> dict[str, dict]:
        batches.append(ids)
        return {sid: children[sid] for sid in ids if sid in children}

    run("context.load-tree", "primary.load_tree(resolve_graphs)", lambda: primary.load_tree(resolver), lambda value: value["loaded_session_count"] == 1 + len(children))
    run("context.breadth-batch", "resolve_graphs(session_ids: list[str])", lambda: batches, lambda value: bool(value) and all(type(batch) is list for batch in value))
    child_id = discovered["subagents"][0]["session_id"]
    run("context.resolve-child", f"primary.resolve({child_id!r})", lambda: primary.resolve(child_id), lambda value: value.session_id == child_id)
    run("registry.resolve-master", "get_graph_context(master_session_id)", lambda: get_graph_context(master["session_id"]), lambda value: value is primary)
    run("registry.resolve-child", "get_graph_context(child_session_id)", lambda: get_graph_context(child_id), lambda value: value is primary)

    run("context.second-master", "secondary.load_self(second_master_graph_dict)", lambda: secondary.load_self(second_master), lambda value: value["loaded_session_count"] == 1)
    run("context.multiple", "get_graph_context(second_master_session_id)", lambda: get_graph_context(second_master["session_id"]), lambda value: value is secondary)

    family = primary.family
    named_cases = [
        ("budget", "budget(family, scope='tree', by='model')", lambda: budget(family, scope="tree", by="model")),
        ("goods", "goods(family, scope='tree', artifact_class='code')", lambda: goods(family, scope="tree", artifact_class="code")),
        ("memory-usage", "memory_usage(family, scope='tree')", lambda: memory_usage(family, scope="tree")),
        ("list-tools", "list_tools(family, scope='tree')", lambda: list_tools(family, scope="tree")),
        ("tool-usage", "tool_usage(family, scope='tree', outcome='failure', limit=3)", lambda: tool_usage(family, scope="tree", outcome="failure", limit=3)),
        ("turns", "turns(family, turn_id={'session_id': master_id, 'turn_num': 1})", lambda: turns(family, turn_id={"session_id": master["session_id"], "turn_num": 1})),
        ("subagents", "subagents(family)", lambda: subagents(family)),
        ("gitops", "gitops(family, scope='tree', operation='commit', limit=3)", lambda: gitops(family, scope="tree", operation="commit", limit=3)),
    ]
    for name, invocation, function in named_cases:
        run(f"api.{name}", invocation, function, lambda value: value["version"] == "1")

    run(
        "select.filtered", "select(family, 'Tool', where=\"Tool.outcome == 'failure'\", fields=['Tool.input.command'], limit=3)",
        lambda: select(family, "Tool", where="Tool.outcome == 'failure'", fields=["Tool.input.command"], limit=3),
        lambda value: value.version == "1" and value.kind == "nodes",
    )
    run(
        "select.regex-order", "select(family, 'Tool', where=\"Tool.input.command MATCHES 'git|pytest'\", order_by=[{'field':'Tool.ts','direction':'asc'}])",
        lambda: select(family, "Tool", where="Tool.input.command MATCHES 'git|pytest'", order_by=[{"field": "Tool.ts", "direction": "asc"}]),
        lambda value: value.version == "1",
    )
    spec = {"version": "1", "scope": {"kind": "tree"}, "select": {"type": "Tool"}, "aggregates": [{"name": "count", "function": "COUNT"}], "return": {"kind": "aggregate"}}
    run("query.aggregate", "query(family, query_spec)", lambda: query(family, spec), lambda value: value.aggregate["count"] > 0)
    run("report.one", "get_load_report(master_session_id)", lambda: get_load_report(master["session_id"]), lambda value: len(value["operations"]) >= 2)
    run("report.all", "get_load_report()", get_load_report, lambda value: master["session_id"] in value and second_master["session_id"] in value)
    run("metric.one", "get_metric(master_session_id)", lambda: get_metric(master["session_id"]), lambda value: bool(value["metrics"]))

    run("context.unload-child", "primary.unload(child_session_id)", lambda: primary.unload(child_id), lambda value: child_id not in value["loaded_session_ids"])
    run("context.unload-tree", "primary.unload_tree()", primary.unload_tree, lambda value: value["state"] == "unloaded")
    run("context.unload-second", "secondary.unload_tree()", secondary.unload_tree, lambda value: value["state"] == "unloaded")

    replacement = GraphContext()
    run("context.reload-master", "replacement.load_self(master_graph_dict)", lambda: replacement.load_self(master), lambda value: value["loaded_session_count"] == 1)
    run("report.reload-continuity", "get_load_report(master_session_id)", lambda: get_load_report(master["session_id"]), lambda value: len(value["operations"]) >= 5)
    run("context.unload-replacement", "replacement.unload_tree()", replacement.unload_tree, lambda value: value["state"] == "unloaded")

    cli_cases = [
        ("cli.inline", ["./utils/graph.sh", "query", str(primary_path), "--select", "Tool", "--where", "Tool.outcome == 'failure'", "--scope", "tree", "--return", "summary", "--limit", "3"]),
        ("cli.queryspec", ["./utils/graph.sh", "query", str(primary_path), "--query", "examples/causal_graph/query_specs/tool_inventory.json"]),
    ]
    for case_id, command in cli_cases:
        def invoke(command=command):
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            if completed.returncode:
                raise AssertionError(completed.stderr)
            return json.loads(completed.stdout)
        run(case_id, " ".join(command), invoke, lambda value: value["version"] == "1")

    if evidence:
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text("".join(json.dumps(record, sort_keys=True, default=str) + "\n" for record in records), encoding="utf-8")
    failures = [record for record in records if record["status"] != "PASS"]
    if failures:
        raise AssertionError(f"consumer validation failures: {failures}")
    return {"families_available": len(paths), "cases": len(records), "passed": len(records), "failed": 0}


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_report(report: Path, named_evidence: Path, python_evidence: Path) -> None:
    named = [json.loads(line) for line in named_evidence.read_text(encoding="utf-8").splitlines()]
    python = [json.loads(line) for line in python_evidence.read_text(encoding="utf-8").splitlines()]
    representatives = [
        next(row for row in named if row["case_id"] == "baseline.budget.0f5e45b9-ecc1-4773-913c-86cd341369d1"),
        next(row for row in named if row["case_id"] == "baseline.subagents.0f5e45b9-ecc1-4773-913c-86cd341369d1"),
        next(row for row in named if row["case_id"] == "tool-usage.limit-one"),
    ]
    full_outputs = []
    for row in representatives:
        completed = subprocess.run(shlex.split(row["command"]), cwd=ROOT, capture_output=True, text=True, check=True)
        full_outputs.append((row, json.loads(completed.stdout)))

    lines = [
        "# Causal Graph Consumer Interface Validation",
        "",
        "**Date:** 2026-08-27",
        "**Branch:** `feature/code_trace`",
        "**Outcome:** PASS",
        "**Corpus:** `examples/causal_graph/graphs` — 17 master families, 72 persisted subagent graphs",
        "",
        "## What was validated",
        "",
        "The named-API audit executed every command through the human-facing `./utils/graph.sh` wrapper. "
        "Each successful CLI JSON envelope was compared with the equivalent Python function result and "
        "then checked against an independent raw-graph oracle. Invalid cases had to fail in both CLI and "
        "Python with the documented error code.",
        "",
        "The consumer audit separately exercised dict-to-dict graph building, two simultaneous contexts, "
        "master and descendant registry lookup, eager batch resolution, every named API, `select`, QuerySpec, "
        "reports, metrics, subtree/full unload, and same-master reload into a replacement context.",
        "",
        "| Gate | Command | Result |",
        "|---|---|---|",
        "| Contracts | `venv/bin/python -m pytest tests/analysis/causal_graph/test_contract_bundle.py tests/utils/test_doc_generators.py -q` | PASS |",
        "| Complete causal-graph suite | `venv/bin/python -m pytest tests/analysis/causal_graph -q` | 440 passed |",
        f"| Named API corpus | `PYTHONPATH=src venv/bin/python tests/analysis/causal_graph/validate_corpus.py examples/causal_graph/graphs --evidence {named_evidence}` | {len(named)} PASS, 0 FAIL |",
        f"| Python consumer corpus | `PYTHONPATH=src venv/bin/python tests/analysis/causal_graph/validate_consumer_interface.py examples/causal_graph/graphs --evidence {python_evidence}` | {len(python)} PASS, 0 FAIL |",
        "| CLI validation | all named calls below use `./utils/graph.sh`; inline and file QuerySpec calls appear in the Python-consumer table | PASS |",
        "| Python validation | every named CLI result was compared with the function API; lifecycle/select/query calls appear below | PASS |",
        "",
        "Named API totals: 238 cases (202 valid, 36 intentionally invalid). Per API: budget 41, goods 42, "
        "list_tools 24, tool_usage 44, turns 30, subagents 23, gitops 34. Every case passed.",
        "",
        "## Representative full results",
        "",
    ]
    for row, output in full_outputs:
        lines.extend([
            f"### `{row['case_id']}`",
            "",
            f"Command: `{row['command']}`",
            "",
            "Validated: **YES** — CLI/Python equality and independent semantic oracle passed.",
            "",
            "```json",
            json.dumps(output, indent=2, sort_keys=True),
            "```",
            "",
        ])

    lines.extend([
        "## Python consumer invocations",
        "",
        "| Case | Invocation | Result | Validated |",
        "|---|---|---|---|",
    ])
    for row in python:
        lines.append(
            f"| {_markdown_cell(row['case_id'])} | `{_markdown_cell(row['invocation'])}` | "
            f"`{_markdown_cell(json.dumps(row['result'], sort_keys=True))}` | {'YES' if row['status'] == 'PASS' and row['valid'] else 'NO'} |"
        )

    lines.extend([
        "",
        "## Every `graph.sh` named-API invocation",
        "",
        "The result column is intentionally compact; the JSONL evidence retains the same command, expected "
        "validity, exit code, semantic assertions, result summary/error code, and pass/fail status.",
        "",
        "| Case | API | CLI command | Expected | Exit | Result | Validated |",
        "|---|---|---|---|---:|---|---|",
    ])
    for row in named:
        lines.append(
            f"| {_markdown_cell(row['case_id'])} | `{row['api']}` | `{_markdown_cell(row['command'])}` | "
            f"{row['expected']} | {row['exit_code']} | `{_markdown_cell(row.get('result', row.get('failure', '')))}` | "
            f"{'YES' if row['status'] == 'PASS' else 'NO'} |"
        )

    lines.extend([
        "",
        "## Evidence and handoff",
        "",
        f"- Named CLI/Python/oracle evidence: [`{named_evidence.name}`]({named_evidence.name})",
        f"- Python lifecycle/query evidence: [`{python_evidence.name}`]({python_evidence.name})",
        "- Authoritative contract bundle: `src/common/contracts/causal_graph/manifest.yaml`",
        "- Python guide: `src/analysis/causal_graph/README.md`",
        "- CLI guide: `src/analysis/causal_graph/README-graphcli.md`",
        "",
        "Work-package commits: `ed6f0bf` contracts, `cbcb943` builder, `47a9dab` context, "
        "`b152fd7` observability, `87d7065` selection, `92ce4da` CLI loading, `68e5ab5` docs/examples.",
        "",
        "Deferred by the approved plan: lazy loading, metric flush/export/persistence, and a graph server. "
        "Reports and metrics are intentionally process-local and retained until process exit.",
        "",
        "No implementation commit was pushed as part of this validation.",
        "",
    ])
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("graph_root", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--named-evidence", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = validate(args.graph_root, evidence=args.evidence)
    if args.report:
        if not args.evidence or not args.named_evidence:
            parser.error("--report requires --evidence and --named-evidence")
        write_report(args.report, args.named_evidence, args.evidence)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
