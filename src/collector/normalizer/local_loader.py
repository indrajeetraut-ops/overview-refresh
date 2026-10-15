"""Loads a real Claude Code session from local disk into a SessionSource This is a dev/manual-testing convenience -- it reads from
a local filesystem path, not S3, so the normalizer can be exercised directly against a real
`~/.claude/projects/...` session. It is **not** the production source-reader (that lives
inside the future engine and reads from S3), though its parsing logic
is a natural starting point for it.

Auto-discovers a sibling subagents/ directory at `<session_dir>/<session_stem>/subagents/`,
matching Claude Code's real on-disk layout -- the same layout the batch-uploader's tar
preserves verbatim -- recursing the same way for nested subagent
trees (spawnDepth > 1).
"""
from __future__ import annotations

import functools
import json
from pathlib import Path

import structlog

from .models import SessionSource, SubagentStream

log = structlog.get_logger(module="collector.normalizer.local_loader")


def _read_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _load_subagent_streams(subagents_dir: Path) -> dict[str, SubagentStream]:
    streams: dict[str, SubagentStream] = {}
    if not subagents_dir.is_dir():
        return streams
    for jsonl_path in sorted(subagents_dir.glob("agent-*.jsonl")):
        meta_path = jsonl_path.with_name(f"{jsonl_path.stem}.meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        tool_use_id = meta.get("toolUseId")
        if not tool_use_id:
            log.warning("subagent_missing_tool_use_id", path=str(jsonl_path))
            continue
        streams[tool_use_id] = SubagentStream(
            agent_id=meta.get("agentId", jsonl_path.stem),
            tool_use_id=tool_use_id,
            meta=meta,
            # Lazy: only this child's filenames/meta are read now; its content is read only
            # if the normalizer actually recurses into it (models.py's bounded-memory note).
            records=functools.partial(_read_jsonl, jsonl_path),
            subagent_streams=_load_subagent_streams(subagents_dir / jsonl_path.stem / "subagents"),
        )
    return streams


def load_session_source(
    session_path: str | Path,
    *,
    vendor: str = "claude_code",
    source: str = "batch",
) -> SessionSource:
    session_path = Path(session_path)
    records = _read_jsonl(session_path)
    agent_version = next((r["version"] for r in records if r.get("version")), None)
    subagents_dir = session_path.with_suffix("") / "subagents"
    return SessionSource(
        vendor=vendor,
        session_id=session_path.stem,
        source=source,
        agent_version=agent_version,
        records=records,
        subagent_streams=_load_subagent_streams(subagents_dir),
    )


def load_codex_session_source(session_path: str | Path, *, vendor: str = "codex", source: str = "batch"):
    """Not implemented -- the seam, not the reader.

    Codex writes `~/.codex/sessions/**/rollout-*.jsonl` in a different shape: every record is a
    `{timestamp, type, payload}` envelope, where Claude Code writes flat records.
    `session_meta` opens the session, `response_item/{message,function_call,
    function_call_output,reasoning}` carry the turn, `event_msg/token_count` the usage, and
    `turn_context` / `event_msg/turn_aborted` mark turn boundaries explicitly.

    Three divergences make the reader a design decision rather than a mapping exercise, and
    they belong to the causal-graph model, not here:

      * No `uuid`/`parentUuid`. Codex is a flat ordered stream linked by `call_id`, so
        `record_id`/`parent_id` -- which the entire Claude spine rests on -- have no direct
        equivalent.
      * Reasoning text is present (`response_item/reasoning.content`,
        `event_msg/agent_reasoning.text`), the opposite of Claude, so the text-cap policy has
        to cover a field Claude never populates.
      * Turns are explicit, so none of the promptId/turn_duration reconstruction applies.

    Raising here rather than returning an empty SessionSource is deliberate: a silent empty
    result would look like a session that simply had no records.
    """
    raise NotImplementedError(
        "codex source reader not implemented: the record shape is understood "
        "({timestamp,type,payload} envelope, call_id linkage, explicit turn_context) but the "
        "record_id/parent_id model for a vendor with no parent chain is a causal-graph "
        "decision. See load_codex_session_source.__doc__."
    )


# Vendor -> loader. Parallel to normalizer.registry, which maps vendor -> VendorNormalizer:
# this side owns *file layout*, that side owns *record mapping*. Both are keyed on the same
# vendor string so a new vendor is one entry in each, and no change to the callers.
SOURCE_READERS = {
    "claude_code": load_session_source,
    "codex": load_codex_session_source,
}

# CLI-facing spellings. `-t claude` is what a person types; `claude_code` is the vendor
# identity that rides on every event.
VENDOR_ALIASES = {"claude": "claude_code", "claude_code": "claude_code", "codex": "codex"}


def resolve_vendor(name: str) -> str:
    try:
        return VENDOR_ALIASES[name]
    except KeyError:
        raise ValueError(
            f"unknown vendor {name!r}; known: {', '.join(sorted(set(VENDOR_ALIASES)))}"
        ) from None
