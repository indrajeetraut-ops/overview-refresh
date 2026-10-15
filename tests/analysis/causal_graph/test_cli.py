"""The graph CLI round-trips build/load and audits named APIs through ``graph.sh``.

Build and parser-isolation tests call ``main([...])`` directly. Named-API acceptance tests launch
the human-facing shell wrapper as a subprocess, so wrapper, module dispatch, argument parsing,
error streams, and JSON serialization are all part of the verified interface.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from analysis.causal_graph import cli_graph
from analysis.causal_graph import get_graph_context


def _envelope() -> dict:
    """A tiny master + one subagent, with a failing tool in each — the shapes the builder and
    named APIs read (reuses tests/fixtures/causal_graph/tiny_family.json's structure)."""
    def _turn(prompt_id: str, mid: str, model: str, tool_rec: str, tool_name: str,
              is_error: bool, ts_base: str) -> list[dict]:
        return [
            {
                "event_type": "user_message",
                "record_id": f"{prompt_id}-u",
                "parent_id": None,
                "event_ts": f"{ts_base}1.000Z",
                "payload": {"text": "do work", "is_injected": False,
                            "raw": {"type": "user", "promptId": prompt_id}},
            },
            {
                "event_type": "assistant_message",
                "record_id": f"{mid}-a",
                "parent_id": f"{prompt_id}-u",
                "event_ts": f"{ts_base}2.000Z",
                "payload": {"message_id": mid, "first_block_of_message": True,
                            "has_thinking": False, "model": model,
                            "input_tokens": 100, "output_tokens": 50,
                            "cache_read_tokens": 0, "cache_write_tokens": 0,
                            "raw": {"type": "assistant", "message": {"id": mid}}},
            },
            {
                "event_type": "tool_call",
                "record_id": tool_rec,
                "parent_id": f"{mid}-a",
                "event_ts": f"{ts_base}3.000Z",
                "payload": {"tool_name": tool_name, "input": {"cmd": "ls"},
                            "message_id": mid,
                            "raw": {"type": "assistant", "message": {"id": mid}}},
            },
            {
                "event_type": "tool_result",
                "record_id": f"{tool_rec}-r",
                "parent_id": tool_rec,
                "event_ts": f"{ts_base}4.000Z",
                "payload": {"tool_use_id": tool_rec, "is_error": is_error,
                            "raw": {"type": "user", "promptId": prompt_id}},
            },
            {
                "event_type": "unknown",
                "record_id": f"{prompt_id}-td",
                "parent_id": f"{tool_rec}-r",
                "event_ts": f"{ts_base}5.000Z",
                "payload": {"raw": {"type": "system", "subtype": "turn_duration",
                                    "durationMs": 500}},
            },
        ]

    # The spawn must live inside the turn (before its turn_duration close marker), else the
    # builder drops it as a startup event.
    master_turn = _turn("MP1", "MM1", "claude-opus-4-8", "m-tool-1", "Bash", True,
                        "2026-07-11T00:00:0")
    master_turn.insert(-1, {
        "event_type": "sub_agent_spawn",
        "record_id": "m-spawn",
        "parent_id": "MM1-a",
        "event_ts": "2026-07-11T00:00:04.500Z",
        "payload": {"agent_type": "Explore", "child_session_id": "agent-child",
                    "status": "completed", "message_id": "MM1",
                    "raw": {"type": "assistant", "message": {"id": "MM1"}}},
    })
    master_events = [
        {
            "event_type": "session_start",
            "record_id": "m-ss",
            "parent_id": None,
            "event_ts": "2026-07-11T00:00:00.000Z",
            "payload": {"ai_title": "Master", "parent_session_id": None,
                        "raw": {"type": "summary", "sessionId": "sess-master"}},
        },
        *master_turn,
    ]
    child_events = [
        {
            "event_type": "session_start",
            "record_id": "c-ss",
            "parent_id": None,
            "event_ts": "2026-07-11T00:00:03.100Z",
            "payload": {"ai_title": "Child", "parent_session_id": "sess-master",
                        "raw": {"type": "summary", "sessionId": "agent-child"}},
        },
        *_turn("CP1", "CM1", "claude-sonnet-4", "c-tool-1", "Grep", True,
               "2026-07-11T00:00:1"),
    ]
    return {
        "meta_info": {"normalizer_version": "1.0.0", "master_session_id": "sess-master"},
        "sessions": [
            {"vendor": "claude_code", "session_id": "sess-master", "source": "jsonl",
             "agent_version": "1.0.0", "parent_session_id": None, "events": master_events},
            {"vendor": "claude_code", "session_id": "agent-child", "source": "jsonl",
             "agent_version": "1.0.0", "parent_session_id": "sess-master",
             "events": child_events},
        ],
    }


@pytest.fixture()
def envelope_file(tmp_path: Path) -> Path:
    path = tmp_path / "envelope.json"
    path.write_text(json.dumps(_envelope()), encoding="utf-8")
    return path


def _last_stdout_json(capsys) -> object:
    out = capsys.readouterr().out.strip().splitlines()[-1]
    return json.loads(out)  # asserts valid JSON


def test_build_writes_the_family_layout(envelope_file, tmp_path, capsys):
    """Master at the top, children under `<master-id>/subagents/`.

    The nesting is what lets several families share one output directory: flat, every sibling
    file looks like a child of whichever master is read first.
    """
    out_dir = tmp_path / "graphs"
    cli_graph.main(["build", str(envelope_file), "-o", str(out_dir)])

    assert sorted(p.name for p in out_dir.glob("*.json")) == ["sess-master.json"]
    subagents = out_dir / "sess-master" / "subagents"
    assert sorted(p.name for p in subagents.glob("*.json")) == ["agent-child.json"]

    summary = _last_stdout_json(capsys)
    assert summary["master"] == "sess-master"
    assert summary["children"] == 1
    assert summary["nodes"] > 0
    assert summary["out"].endswith("sess-master.json")


def test_load_by_master_file_finds_its_subagents(envelope_file, tmp_path):
    """The primary form: address a family by one path, children discovered beside it."""
    out_dir = tmp_path / "graphs"
    cli_graph.main(["build", str(envelope_file), "-o", str(out_dir)])

    family = cli_graph.load_family(out_dir / "sess-master.json")
    assert family.master.session_id == "sess-master"
    assert list(family.children) == ["agent-child"]


def test_cli_private_loader_uses_context_and_universal_session_registry(envelope_file, tmp_path):
    out_dir = tmp_path / "graphs"
    cli_graph.main(["build", str(envelope_file), "-o", str(out_dir)])
    context = cli_graph._load_graph_context(out_dir / "sess-master.json")
    try:
        assert get_graph_context("sess-master") is context
        assert get_graph_context("agent-child") is context
        assert context.resolve("agent-child") is not None
    finally:
        context.unload_tree()


def test_file_helpers_are_not_public_analysis_exports():
    import analysis.causal_graph as causal_graph

    assert not hasattr(causal_graph, "load_graph")
    assert not hasattr(causal_graph, "dump_graph")


def test_two_families_in_one_dir_stay_separate(envelope_file, tmp_path):
    """The bug this layout fixes.

    Flat, `load_family` took the first null-parent graph as master and adopted every other
    file as its child -- so a second session's subagents were silently attributed to the
    first, corrupting any rollup computed from it. Nested, each master sees only its own.
    """
    out_dir = tmp_path / "graphs"
    cli_graph.main(["build", str(envelope_file), "-o", str(out_dir)])

    # a second, unrelated family in the same directory
    second = json.loads(json.dumps(_envelope()))
    for session in second["sessions"]:
        session["session_id"] = session["session_id"].replace("sess-", "other-").replace(
            "agent-", "otheragent-")
        if session["parent_session_id"]:
            session["parent_session_id"] = "other-master"
        for event in session["events"]:
            if event["event_type"] == "sub_agent_spawn":
                event["payload"]["child_session_id"] = "otheragent-child"
    other_file = tmp_path / "other.json"
    other_file.write_text(json.dumps(second), encoding="utf-8")
    cli_graph.main(["build", str(other_file), "-o", str(out_dir)])

    first = cli_graph.load_family(out_dir / "sess-master.json")
    assert list(first.children) == ["agent-child"], "adopted another family's subagent"

    other = cli_graph.load_family(out_dir / "other-master.json")
    assert list(other.children) == ["otheragent-child"]
    assert "agent-child" not in other.children


def test_directory_load_refuses_multiple_masters(envelope_file, tmp_path):
    """A directory holding two families cannot be disentangled, so it must not guess."""
    out_dir = tmp_path / "graphs"
    cli_graph.main(["build", str(envelope_file), "-o", str(out_dir)])
    second = json.loads(json.dumps(_envelope()))
    for session in second["sessions"]:
        session["session_id"] = session["session_id"].replace("sess-", "other-")
        if session["parent_session_id"]:
            session["parent_session_id"] = "other-master"
    other_file = tmp_path / "other.json"
    other_file.write_text(json.dumps(second), encoding="utf-8")
    cli_graph.main(["build", str(other_file), "-o", str(out_dir)])

    with pytest.raises(SystemExit, match="master envelopes"):
        cli_graph.load_family(out_dir)


def test_load_rejects_a_subagent_envelope(envelope_file, tmp_path):
    """Loading a child directly would yield a family missing its own master."""
    out_dir = tmp_path / "graphs"
    cli_graph.main(["build", str(envelope_file), "-o", str(out_dir)])
    child = out_dir / "sess-master" / "subagents" / "agent-child.json"

    with pytest.raises(SystemExit, match="subagent envelope"):
        cli_graph.load_family(child)


def test_load_reassembles_master_and_children(envelope_file, tmp_path, capsys):
    out_dir = tmp_path / "graphs"
    cli_graph.main(["build", str(envelope_file), "-o", str(out_dir)])
    capsys.readouterr()  # drop build output

    family = cli_graph.load_family(out_dir)
    assert family.master.session_id == "sess-master"
    assert family.master.parent_session_id is None
    assert list(family.children) == ["agent-child"]
    assert family.children["agent-child"].parent_session_id == "sess-master"

    cli_graph.main(["load", str(out_dir)])
    summary = _last_stdout_json(capsys)
    assert summary == {"master": "sess-master", "children": 1, "nodes": len(
        family.master.nodes) + len(family.children["agent-child"].nodes)}


def test_query_inline_and_query_file_emit_identical_structured_json(envelope_file, tmp_path, capsys):
    inline = ["query", str(envelope_file), "--select", "Tool", "--where", "Tool.name == 'Bash'", "--scope", "tree", "--return", "summary"]
    cli_graph.main(inline)
    inline_result = _last_stdout_json(capsys)
    spec_file = tmp_path / "query.json"
    spec_file.write_text(json.dumps({
        "version": "1", "scope": {"kind": "tree"},
        "select": {"type": "Tool", "where": "Tool.name == 'Bash'"},
        "return": {"kind": "nodes", "verbosity": "summary"},
    }), encoding="utf-8")
    cli_graph.main(["query", str(envelope_file), "--query", str(spec_file)])
    assert _last_stdout_json(capsys) == inline_result


@pytest.mark.parametrize(
    ("api_name", "filters", "kind"),
    [
        ("budget", ["--by", "turn", "--model", "claude-opus-4-8"], "budget"),
        ("goods", ["--extension", ".py"], "goods"),
        ("memory_usage", ["--scope", "tree"], "memory_usage"),
        ("list_tools", ["--scope", "self"], "tool_list"),
        (
            "tool_usage",
            ["--tool-class", "harness", "--name", "Bash", "--outcome", "failure", "--limit", "1"],
            "tool_usage",
        ),
        (
            "turns",
            ["--session-id", "sess-master", "--turn-num", "1", "--node-type", "tool"],
            "turns",
        ),
        ("subagents", ["--scope", "self"], "subagents"),
        ("gitops", ["--operation", "commit", "--limit", "1"], "gitops"),
    ],
)
def test_named_api_cli_accepts_filtered_invocations(
    envelope_file, capsys, api_name, filters, kind
):
    cli_graph.main(["api", api_name, str(envelope_file), *filters])
    result = _last_stdout_json(capsys)

    assert result["kind"] == kind
    if api_name == "budget":
        assert result["by"] == "turn"
    elif api_name == "goods":
        assert all(row["extension"] == ".py" for row in result["artifacts"])
    elif api_name == "memory_usage":
        assert result["counts"]["tool_calls"] == 0
    elif api_name == "list_tools":
        assert result["scope"] == {"kind": "self"}
    elif api_name == "tool_usage":
        assert result["returned_count"] == 1
        assert result["tools"][0]["outcome"] == "failure"
    elif api_name == "turns":
        assert all(row["type"] == "tool" for row in result["nodes"])
    elif api_name == "subagents":
        assert result["count"] == 1
    elif api_name == "gitops":
        assert result["returned_count"] == 0


@pytest.mark.parametrize(
    ("api_name", "filters", "error_code"),
    [
        ("budget", ["--by", "assistant"], "invalid_budget_axis"),
        ("goods", ["--extension", "py"], "invalid_goods_filter"),
        ("list_tools", ["--scope", "invalid"], "invalid_scope"),
        ("tool_usage", ["--outcome", "sometimes"], "invalid_tool_outcome"),
        (
            "turns",
            ["--session-id", "sess-master", "--turn-num", "1", "--node-type", "result"],
            "invalid_turn_node_type",
        ),
        ("subagents", ["--scope", "tree"], "invalid_subagent_scope"),
        ("gitops", ["--operation", "merge"], "invalid_git_operation"),
    ],
)
def test_named_api_cli_rejects_invalid_filters_with_error_envelope(
    envelope_file, capsys, api_name, filters, error_code
):
    with pytest.raises(SystemExit) as exc:
        cli_graph.main(["api", api_name, str(envelope_file), *filters])

    assert exc.value.code == 2
    error = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert error["api"] == api_name
    assert error["code"] == error_code


def test_graph_shell_wrapper_invokes_named_api(envelope_file):
    root = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        [
            str(root / "utils" / "graph.sh"),
            "api",
            "subagents",
            str(envelope_file),
            "--scope",
            "self",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )

    result = json.loads(completed.stdout)
    assert result["kind"] == "subagents"
    assert result["count"] == 1


_ARTIFACT_CLASSES = (
    "code", "documentation", "config", "markup", "data", "notebook", "template",
    "document", "spreadsheet", "presentation", "pdf", "unknown",
)


_VALID_WRAPPER_CASES = [
    # budget: defaults, every axis, every scope, individual filters, and combinations
    ("budget", [], "budget"),
    *[("budget", ["--by", axis], "budget") for axis in ("model", "permission_mode", "turn", "subagent")],
    *[("budget", ["--scope", scope], "budget") for scope in ("self", "tree", "subagents", "session:agent-child")],
    ("budget", ["--model", "claude-opus-4-8"], "budget"),
    ("budget", ["--model", "__missing_model__"], "budget"),
    ("budget", ["--permission-mode", "__missing_mode__"], "budget"),
    ("budget", ["--turn-session-id", "sess-master", "--turn-num", "1"], "budget"),
    ("budget", ["--model", "claude-opus-4-8", "--turn-session-id", "sess-master", "--turn-num", "1"], "budget"),
    ("budget", ["--model", "__missing_model__", "--permission-mode", "__missing_mode__", "--turn-session-id", "sess-master", "--turn-num", "1"], "budget"),
    # goods: every class, extensions, combinations, empty matches, and all scopes
    ("goods", [], "goods"),
    *[("goods", ["--artifact-class", value], "goods") for value in _ARTIFACT_CLASSES],
    *[("goods", ["--scope", scope], "goods") for scope in ("self", "tree", "subagents", "session:agent-child")],
    ("goods", ["--extension", ".py"], "goods"),
    ("goods", ["--extension", ".not-present"], "goods"),
    ("goods", ["--artifact-class", "code", "--extension", ".py"], "goods"),
    ("goods", ["--artifact-class", "code", "--extension", ".md"], "goods"),
    # inventory and invocation exploration
    ("memory_usage", [], "memory_usage"),
    *[("memory_usage", ["--scope", scope], "memory_usage") for scope in ("self", "tree", "subagents", "session:agent-child")],
    ("list_tools", [], "tool_list"),
    *[("list_tools", ["--scope", scope], "tool_list") for scope in ("self", "tree", "subagents", "session:agent-child")],
    ("tool_usage", [], "tool_usage"),
    *[("tool_usage", ["--scope", scope], "tool_usage") for scope in ("self", "tree", "subagents", "session:agent-child")],
    ("tool_usage", ["--tool-class", "harness"], "tool_usage"),
    ("tool_usage", ["--tool-class", "harness", "--name", "Bash"], "tool_usage"),
    ("tool_usage", ["--tool-class", "command"], "tool_usage"),
    ("tool_usage", ["--tool-class", "command", "--name", "ls"], "tool_usage"),
    *[("tool_usage", ["--outcome", outcome], "tool_usage") for outcome in ("all", "success", "failure")],
    ("tool_usage", ["--invocation-matches", "ls"], "tool_usage"),
    ("tool_usage", ["--invocation-matches", "__not_present__"], "tool_usage"),
    ("tool_usage", ["--limit", "1"], "tool_usage"),
    ("tool_usage", ["--limit", "10000"], "tool_usage"),
    ("tool_usage", ["--tool-class", "harness", "--name", "Bash", "--outcome", "failure", "--limit", "1"], "tool_usage"),
    ("tool_usage", ["--tool-class", "command", "--name", "ls", "--invocation-matches", "ls", "--outcome", "failure", "--limit", "1"], "tool_usage"),
    # turns: master/child and every accepted contained-node view
    ("turns", ["--session-id", "sess-master", "--turn-num", "1"], "turns"),
    ("turns", ["--session-id", "agent-child", "--turn-num", "1"], "turns"),
    *[("turns", ["--session-id", "sess-master", "--turn-num", "1", "--node-type", node_type], "turns") for node_type in ("node", "user_prompt", "assistant", "tool", "spawn")],
    ("subagents", [], "subagents"),
    ("subagents", ["--scope", "self"], "subagents"),
    # structured Git filters are valid even when the tiny fixture has no recognized records
    ("gitops", [], "gitops"),
    *[("gitops", ["--scope", scope], "gitops") for scope in ("self", "tree", "subagents", "session:agent-child")],
    *[("gitops", ["--operation", operation], "gitops") for operation in ("commit", "push", "pr", "branch")],
    ("gitops", ["--limit", "1"], "gitops"),
    ("gitops", ["--limit", "10000"], "gitops"),
    ("gitops", ["--operation", "commit", "--limit", "1"], "gitops"),
]


@pytest.mark.parametrize(("api_name", "filters", "kind"), _VALID_WRAPPER_CASES)
def test_graph_shell_wrapper_accepts_named_api_matrix(envelope_file, api_name, filters, kind):
    root = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        ["./utils/graph.sh", "api", api_name, str(envelope_file), *filters],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["kind"] == kind


_INVALID_WRAPPER_CASES = [
    ("budget", ["--by", "assistant"], "invalid_budget_axis"),
    ("budget", ["--model", ""], "invalid_filter"),
    ("budget", ["--turn-session-id", "sess-master"], "invalid_filter"),
    ("budget", ["--turn-num", "1"], "invalid_filter"),
    ("budget", ["--turn-session-id", "sess-master", "--turn-num", "0"], "invalid_filter"),
    ("goods", ["--artifact-class", "binary"], "invalid_goods_filter"),
    ("goods", ["--extension", "py"], "invalid_goods_filter"),
    ("tool_usage", ["--tool-class", "process"], "invalid_tool_selector"),
    ("tool_usage", ["--name", "git"], "invalid_tool_selector"),
    ("tool_usage", ["--tool-class", "harness", "--name", ""], "invalid_tool_selector"),
    ("tool_usage", ["--outcome", "sometimes"], "invalid_tool_outcome"),
    ("tool_usage", ["--invocation-matches", "["], "invalid_invocation_regex"),
    ("tool_usage", ["--limit", "0"], "invalid_tool_limit"),
    ("tool_usage", ["--limit", "10001"], "invalid_tool_limit"),
    ("turns", ["--session-id", "", "--turn-num", "1"], "invalid_turn_id"),
    ("turns", ["--session-id", "sess-master", "--turn-num", "0"], "invalid_turn_id"),
    ("turns", ["--session-id", "missing", "--turn-num", "1"], "turn_not_loaded"),
    ("turns", ["--session-id", "sess-master", "--turn-num", "999"], "turn_not_loaded"),
    ("turns", ["--session-id", "sess-master", "--turn-num", "1", "--node-type", "result"], "invalid_turn_node_type"),
    ("subagents", ["--scope", "tree"], "invalid_subagent_scope"),
    ("subagents", ["--scope", "subagents"], "invalid_subagent_scope"),
    ("subagents", ["--scope", "session:sess-master"], "invalid_subagent_scope"),
    ("subagents", ["--scope", "invalid"], "invalid_subagent_scope"),
    ("gitops", ["--operation", "merge"], "invalid_git_operation"),
    ("gitops", ["--limit", "0"], "invalid_git_limit"),
    ("gitops", ["--limit", "10001"], "invalid_git_limit"),
    *[
        (api_name, ["--scope", scope], "invalid_scope")
        for api_name in ("budget", "goods", "memory_usage", "list_tools", "tool_usage", "gitops")
        for scope in ("invalid", "session:")
    ],
]


@pytest.mark.parametrize(("api_name", "filters", "error_code"), _INVALID_WRAPPER_CASES)
def test_graph_shell_wrapper_rejects_named_api_matrix(
    envelope_file, api_name, filters, error_code
):
    root = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        ["./utils/graph.sh", "api", api_name, str(envelope_file), *filters],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    error = json.loads(completed.stderr.strip().splitlines()[-1])
    assert error["api"] == api_name
    assert error["code"] == error_code


def test_graph_shell_memory_usage_matches_python_api(envelope_file):
    envelope = json.loads(envelope_file.read_text(encoding="utf-8"))
    master = envelope["sessions"][0]
    assistant = next(event for event in master["events"] if event["event_type"] == "assistant_message")
    assistant["payload"]["effort"] = "high"
    tool = next(event for event in master["events"] if event["event_type"] == "tool_call")
    tool["payload"]["tool_name"] = "Edit"
    tool["payload"]["files"] = [{
        "path": "/home/.claude/projects/-repo/memory/MEMORY.md",
        "extension": ".md", "artifact_class": "documentation", "operation": "edit",
        "hunks": [{"start": 1, "len": 3, "old_len": 1}],
    }]
    envelope_file.write_text(json.dumps(envelope), encoding="utf-8")

    root = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        ["./utils/graph.sh", "api", "memory_usage", str(envelope_file), "--scope", "tree"],
        cwd=root, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr

    from analysis.causal_graph import build_family, memory_usage

    cli_graph._configure_logging()
    expected = memory_usage(build_family(envelope), scope="tree")
    assert json.loads(completed.stdout) == expected
    assert expected["counts"]["tool_calls"] == 1


# --- build-all -------------------------------------------------------------------------


def _write_envelope(path: Path, master_id: str) -> None:
    """A normalizer envelope with `master_id` and one subagent, at `path`."""
    env = json.loads(json.dumps(_envelope()))
    for session in env["sessions"]:
        if session["parent_session_id"] is None:
            session["session_id"] = master_id
        else:
            session["session_id"] = f"{master_id}-child"
            session["parent_session_id"] = master_id
        for event in session["events"]:
            if event["event_type"] == "sub_agent_spawn":
                event["payload"]["child_session_id"] = f"{master_id}-child"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(env), encoding="utf-8")


def _report(out: Path) -> dict:
    return json.loads((out / "run_results.json").read_text(encoding="utf-8"))


def test_build_all_mirrors_the_source_tree(tmp_path):
    src = tmp_path / "nrm"
    _write_envelope(src / "projA" / "s1.json", "s1")
    _write_envelope(src / "projB" / "s2.json", "s2")
    out = tmp_path / "graphs"

    cli_graph.main(["build-all", str(src), "-o", str(out)])

    # projects stay apart, so session ids cannot collide across them
    assert (out / "projA" / "s1.json").is_file()
    assert (out / "projA" / "s1" / "subagents" / "s1-child.json").is_file()
    assert (out / "projB" / "s2.json").is_file()

    assert _report(out)["counts"] == {"built": 2, "skipped": 0, "error": 0}


def test_build_all_excludes_the_memory_directory(tmp_path):
    """A master envelope under `memory/` is never a buildable input.

    Measured on the real corpus: the memory-directory copy of one session carried 62 spawn
    nodes and not one resolvable `child_session_id`, because its `subagents/` tree sits beside
    the ordinary sessions rather than beside it. Building it emits a graph whose every spawn is
    a dead end.
    """
    src = tmp_path / "nrm"
    _write_envelope(src / "proj" / "s1.json", "s1")
    _write_envelope(src / "proj" / "memory" / "s2.json", "s2")
    out = tmp_path / "graphs"

    cli_graph.main(["build-all", str(src), "-o", str(out)])

    assert (out / "proj" / "s1.json").is_file()
    assert not (out / "proj" / "memory" / "s2.json").exists()
    # excluded BEFORE the count, like a subagent envelope -- `total` is inputs, not files
    assert _report(out)["counts"] == {"built": 1, "skipped": 0, "error": 0}


def test_build_all_ignores_envelopes_under_subagents(tmp_path):
    """The selector that makes the command correct.

    The normalizer writes each subagent BOTH inline in its master's envelope and as its own
    file under `<session>/subagents/`. Building those files again would emit orphan graphs
    whose spawns resolve to nothing and whose tokens double-count in any cross-session rollup.
    """
    src = tmp_path / "nrm"
    _write_envelope(src / "proj" / "s1.json", "s1")
    _write_envelope(src / "proj" / "s1" / "subagents" / "agent-x.json", "agent-x")
    out = tmp_path / "graphs"

    cli_graph.main(["build-all", str(src), "-o", str(out)])

    report = _report(out)
    assert report["counts"]["built"] == 1, "a subagent envelope was built as its own family"
    # excluded before counting, so `total` reports inputs rather than files on disk
    assert report["total"] == 1
    assert not (out / "proj" / "s1" / "subagents" / "agent-x").exists()


def test_build_all_skips_non_envelopes(tmp_path):
    """`run_results.json` sits in the corpus root and is not an envelope.

    Skipped by shape (no `sessions` key), so no filename convention has to be maintained.
    """
    src = tmp_path / "nrm"
    _write_envelope(src / "proj" / "s1.json", "s1")
    (src / "run_results.json").write_text(json.dumps({"counts": {}}), encoding="utf-8")
    out = tmp_path / "graphs"

    cli_graph.main(["build-all", str(src), "-o", str(out)])

    assert _report(out)["counts"] == {"built": 1, "skipped": 1, "error": 0}


def test_build_all_report_matches_the_normalizer_shape(tmp_path, capsys):
    """The report is consumed alongside the normalizer's, so the fields line up."""
    src = tmp_path / "nrm"
    _write_envelope(src / "proj" / "s1.json", "s1")
    out = tmp_path / "graphs"
    cli_graph.main(["build-all", str(src), "-o", str(out)])

    report = _report(out)
    assert set(report) == {"generated_at", "source", "target", "total", "counts",
                           "totals", "files"}
    assert report["source"] == str(src) and report["target"] == str(out)
    assert report["total"] == 1
    assert report["counts"]["built"] == 1
    assert report["totals"] == {"masters": 1, "subagents": 1, "nodes": report["files"][0]["nodes"]}

    row = report["files"][0]
    assert row["input"] == str(Path("proj") / "s1.json")
    assert row["output"] == str(Path("proj") / "s1.json")
    assert row["status"] == "built"
    assert row["master"] == "s1" and row["children"] == 1 and row["nodes"] > 0

    # the console line a human reads
    assert "1 built, 0 skipped, 0 error(s)" in capsys.readouterr().out


def test_build_all_error_row_carries_the_reason(tmp_path):
    """A count tells you something failed; only the message tells you what."""
    src = tmp_path / "nrm"
    _write_envelope(src / "proj" / "s1.json", "s1")
    (src / "proj" / "broken.json").write_text("{not json", encoding="utf-8")
    out = tmp_path / "graphs"

    with pytest.raises(SystemExit):
        cli_graph.main(["build-all", str(src), "-o", str(out)])

    row = next(r for r in _report(out)["files"] if r["status"] == "error")
    assert row["input"] == str(Path("proj") / "broken.json")
    assert row["output"] is None
    assert "unreadable" in row["message"]


def test_build_all_survives_one_bad_file(tmp_path, capsys):
    """One unreadable session must not abandon the rest of the batch."""
    src = tmp_path / "nrm"
    _write_envelope(src / "proj" / "s1.json", "s1")
    (src / "proj" / "broken.json").write_text("{not json", encoding="utf-8")
    out = tmp_path / "graphs"

    with pytest.raises(SystemExit):        # non-zero exit because something errored
        cli_graph.main(["build-all", str(src), "-o", str(out)])

    results = json.loads((out / "run_results.json").read_text(encoding="utf-8"))
    assert results["counts"] == {"built": 1, "skipped": 0, "error": 1}
    assert (out / "proj" / "s1.json").is_file(), "good session was not built"


def test_build_all_output_loads_back_as_a_family(tmp_path):
    """Round trip: what build-all writes is what load_family reads."""
    src = tmp_path / "nrm"
    _write_envelope(src / "proj" / "s1.json", "s1")
    out = tmp_path / "graphs"
    cli_graph.main(["build-all", str(src), "-o", str(out)])

    family = cli_graph.load_family(out / "proj" / "s1.json")
    assert family.master.session_id == "s1"
    assert list(family.children) == ["s1-child"]


def test_subagent_directory_points_at_its_master(envelope_file, tmp_path):
    """`<master-id>/` holds only `subagents/`, so a plain 'no envelopes found' reads as
    'nothing was built' when the fix is to add `.json`."""
    out_dir = tmp_path / "graphs"
    cli_graph.main(["build", str(envelope_file), "-o", str(out_dir)])

    with pytest.raises(SystemExit, match="Pass the master envelope instead"):
        cli_graph.load_family(out_dir / "sess-master")
