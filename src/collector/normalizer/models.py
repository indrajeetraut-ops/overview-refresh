"""In-process carrier types for the normalizer for one session.

SessionSource/SubagentStream/NormalizedSession never cross a process boundary, so they're
plain dataclasses; only EventIn is pydantic. EventIn is the normalizer's *own* event type,
defined here rather than borrowed from the transport: the normalizer produces structured
events, and how they are packaged onto the wire (one msg per session, opaque payload) is the
adapter's concern, not the normalizer's. SessionSource.records
is a materialized list, not an Iterator: the batch path hands the normalizer a whole file at once
(no chunking, doc/), so a list already satisfies that contract without buying anything
from a real generator.

SubagentStream.records is different: it's a zero-arg callable, not a materialized list. A
session can have many sibling subagents (an orchestrator loop pattern, not exotic), and
building the whole subagent_streams tree eagerly with every child's full content already
loaded would load every sibling's transcript into memory before the normalizer has processed
any of them -- bounded by fan-out width, not depth, violating the "no
unbounded lookahead" rule. Discovering *which* subagents exist and their `meta` stays eager
(cheap: filenames + small meta.json reads); only the potentially-large JSONL content is
deferred until the normalizer actually recurses into that specific child.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from pydantic import BaseModel

__all__ = ["EventIn", "SubagentStream", "SessionSource", "NormalizedSession"]


class EventIn(BaseModel):
    """One normalized event. Fields mirror the former msging wire type verbatim so the
    normalizer's output shape is unchanged by the transport rewrite; the adapter maps a
    NormalizedSession's events into an Envelope payload."""

    event_type: str
    payload: dict
    record_id: str | None = None
    parent_id: str | None = None
    event_ts: str | None = None


@dataclass
class SubagentStream:
    """One subagent's own record stream, keyed into its parent by tool_use id."""

    agent_id: str  # meta.json agentId == this child's session_id
    tool_use_id: str  # the parent Agent/Task tool_use.id that spawned this child
    meta: dict  # parsed meta.json (agentType, spawnDepth, resolvedModel, ...)
    records: Callable[[], list[dict]]  # lazy: read only when the normalizer recurses here
    subagent_streams: dict[str, "SubagentStream"] = field(default_factory=dict)  # nesting


@dataclass
class SessionSource:
    """What the source adapter hands the normalizer for one session."""

    vendor: str
    session_id: str
    source: str  # "batch" | "live_tail"
    agent_version: str | None
    records: list[dict]  # parsed JSONL records, in file order
    subagent_streams: dict[str, SubagentStream] = field(default_factory=dict)  # by tool_use_id


@dataclass
class NormalizedSession:
    """The normalizer's output for one session."""

    vendor: str
    session_id: str
    source: str
    agent_version: str | None
    parent_session_id: str | None
    events: list[EventIn]
