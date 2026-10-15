"""Runnable public consumer examples must not depend on CLI implementation internals."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).parents[3]
EXAMPLES = ROOT / "examples/causal_graph"
CORPUS_MASTER = EXAMPLES / "graphs/-Users-sumandra-projects-talium-code-econ/0f5e45b9-ecc1-4773-913c-86cd341369d1.json"


def _run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(EXAMPLES / script), *args], cwd=ROOT,
        env={**os.environ, "PYTHONPATH": "src"}, capture_output=True, text=True,
    )


def test_in_memory_and_file_consumers_run_the_same_public_flow() -> None:
    memory = _run("in_memory_consumer.py")
    file = _run("file_consumer.py", str(CORPUS_MASTER))
    assert memory.returncode == 0, memory.stderr
    assert file.returncode == 0, file.stderr
    for completed in (memory, file):
        result = json.loads(completed.stdout)
        assert result["budget"]["kind"] == "budget"
        assert result["tools"]["kind"] == "tool_list"
        assert result["memory"]["kind"] == "memory_usage"
        assert result["failed_tools"]["kind"] == "nodes"
        assert result["tool_count"]["kind"] == "aggregate"
        assert result["discovered_subagents"]
        assert result["final_load_report"]["state"] == "unloaded"


def test_focused_named_and_queryspec_examples_run() -> None:
    named = _run("run_named_api_examples.py")
    specs = _run("run_queryspec_examples.py")
    assert named.returncode == 0, named.stderr
    assert specs.returncode == 0, specs.stderr
    assert json.loads(named.stdout)["budget"]["kind"] == "budget"
    assert json.loads(named.stdout)["memory_usage"]["kind"] == "memory_usage"
    assert all(json.loads(line)["result"]["version"] == "1" for line in specs.stdout.splitlines())


def test_examples_do_not_import_cli_loader() -> None:
    for path in EXAMPLES.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "causal_graph.cli_graph" not in source
        assert "causal_graph._cli_store" not in source
