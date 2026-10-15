"""The source-independent part shared by file and in-memory consumers."""
from __future__ import annotations

from collections.abc import Callable
import sys

import structlog

from analysis.causal_graph import (
    GraphContext,
    budget,
    get_load_report,
    get_metric,
    list_tools,
    memory_usage,
    query,
    select,
    subagents,
)


def run_consumer(master_graph: dict, resolve_graphs: Callable[[list[str]], dict[str, dict]]) -> dict:
    """Exercise the same public lifecycle regardless of where graph dictionaries came from."""
    context = GraphContext()
    context.load_self(master_graph)
    discovered = subagents(context.family)
    context.load_tree(resolve_graphs)

    family = context.family
    result = {
        "master_session_id": family.master.session_id,
        "discovered_subagents": discovered["subagents"],
        "budget": budget(family, scope="tree", by="model"),
        "tools": list_tools(family, scope="tree"),
        "memory": memory_usage(family, scope="tree"),
        "failed_tools": select(
            family, "Tool", where="Tool.outcome == 'failure'", scope="tree",
            fields=["Tool.input.command"], limit=3,
        ).model_dump(mode="json", exclude_none=True),
        "tool_count": query(
            family,
            {
                "version": "1", "scope": {"kind": "tree"},
                "select": {"type": "Tool"},
                "aggregates": [{"name": "count", "function": "COUNT"}],
                "return": {"kind": "aggregate"},
            },
        ).model_dump(mode="json", exclude_none=True),
    }
    result["load_report"] = get_load_report(family.master.session_id)
    result["metrics"] = get_metric(family.master.session_id)

    loaded_children = list(family.children)
    if loaded_children:
        context.unload(loaded_children[0])
    context.unload_tree()
    result["final_load_report"] = get_load_report(result["master_session_id"])
    return result


def configure_example_logging() -> None:
    """Keep structured library logs on stderr and example results on stdout."""
    structlog.configure(
        processors=[structlog.processors.add_log_level, structlog.processors.JSONRenderer()],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )
