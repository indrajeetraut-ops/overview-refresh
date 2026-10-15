"""The only public-field mapping used by causal-graph query filters and results."""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any

from .models import Node, NodeType

__all__ = ["FieldRegistry", "MISSING", "default_summary_fields"]

MISSING = object()

_TYPE_NAMES = {
    "turn": NodeType.TURN, "userprompt": NodeType.USER_PROMPT, "assistant": NodeType.ASSISTANT,
    "tool": NodeType.TOOL, "spawn": NodeType.SPAWN, "compaction": NodeType.COMPACTION,
}
_DEFAULTS = {
    NodeType.TURN: ("Turn.status", "Turn.duration_ms", "Turn.permission_mode", "model"),
    NodeType.USER_PROMPT: ("UserPrompt.preview", "UserPrompt.char_count"),
    NodeType.ASSISTANT: ("model_id", "Assistant.input_tokens", "Assistant.output_tokens"),
    NodeType.TOOL: ("Tool.name", "Tool.cmd", "Tool.outcome", "Tool.duration_ms"),
    NodeType.SPAWN: ("Spawn.agent_type", "Spawn.status", "Spawn.child_session_id"),
    NodeType.COMPACTION: ("Compaction.trigger", "Compaction.tokens_reclaimed", "Compaction.duration_ms"),
}


def default_summary_fields(node_type: NodeType) -> tuple[str, ...]:
    return _DEFAULTS[node_type]


class FieldRegistry:
    """Resolve logical dotted names without exposing Python object internals."""

    def validate(self, path: str, node_type: NodeType | None = None) -> None:
        """Reject a field absent from the authoritative public-field contracts."""
        if not isinstance(path, str) or not path:
            raise ValueError("field path must be a non-empty string")
        if path in {"harness", "model", "model_id"}:
            return
        parts = path.split(".")
        selected_type = node_type
        prefixed = _TYPE_NAMES.get(parts[0].casefold())
        if prefixed is not None:
            if selected_type is not None and selected_type is not prefixed:
                raise ValueError(f"field {path!r} does not apply to {selected_type.value}")
            selected_type = prefixed
            parts = parts[1:]
        if not parts or selected_type is None:
            raise ValueError(f"unknown public field {path!r}")
        if parts[0] == "ts":
            return
        if parts[0] in _contract_attr_roots().get(selected_type.value, set()):
            return
        derived = {
            NodeType.TOOL: {"name", "cmd", "outcome"},
            NodeType.SPAWN: {"child_session_id"},
            NodeType.USER_PROMPT: {"preview", "char_count"},
        }
        if parts[0] in derived.get(selected_type, set()):
            return
        raise ValueError(f"unknown public field {path!r}")

    def value(self, node: Node, path: str) -> Any:
        parts = path.split(".")
        if path == "harness":
            return node.meta.get("harness", MISSING)
        if path in {"model", "model_id"}:
            return node.attrs.get("model", MISSING)
        if parts[0].casefold() in _TYPE_NAMES:
            expected = _TYPE_NAMES[parts[0].casefold()]
            if node.type is not expected:
                return MISSING
            parts = parts[1:]
        if not parts:
            return MISSING
        if node.type is NodeType.TOOL and parts[0] == "name":
            return node.attrs.get("tool_name", MISSING)
        if node.type is NodeType.TOOL and parts[0] == "cmd":
            return node.attrs.get("cmd", MISSING)
        if node.type is NodeType.TOOL and parts[0] == "outcome":
            result = node.attrs.get("result")
            if isinstance(result, dict):
                outcome = result.get("outcome")
                if outcome in {"error", "denied"}:
                    return "failure"
                if outcome == "ok":
                    process = result.get("process")
                    return "failure" if isinstance(process, dict) and process.get("interrupted") else "success"
                return "unknown"
            return "failure" if node.attrs.get("is_error") else "unknown"
        if node.type is NodeType.SPAWN and parts[0] == "child_session_id":
            return node.refs.get("child_session_id", MISSING)
        if node.type is NodeType.USER_PROMPT and parts[0] in {"preview", "char_count"}:
            text = node.attrs.get("text", MISSING)
            if not isinstance(text, str):
                return MISSING
            return text[:200] if parts[0] == "preview" else len(text)
        if parts[0] == "ts":
            return node.ts
        value: Any = node.attrs
        for part in parts:
            if not isinstance(value, dict):
                return MISSING
            value = value.get(part, MISSING)
            if value is MISSING:
                return MISSING
        return value


@lru_cache(maxsize=1)
def _contract_attr_roots() -> dict[str, set[str]]:
    path = Path(__file__).resolve().parents[2] / "common/contracts/causal_graph/schemas/graph.schema.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    return {
        node_type: set(block.get("attrs", {}))
        for node_type, block in contract.get("attrs_by_node_type", {}).items()
    }
