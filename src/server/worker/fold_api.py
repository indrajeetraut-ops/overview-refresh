"""The abstracted fold seam (ingest.md §8). No metric logic here — the ingest handler owns the
mechanism (per-session lock, transaction boundary, ACK-after-commit, rollup trigger); the fold
owns the meaning (what a token/tool/file/cost is).

One msg carries one whole session (event-messaging-design.md §1), so `fold()` receives that
session's events and dedups **positionally** against the blob (append-only prefix-extension,
see GraphFold.fold) — there is no per-event id ledger.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass
class SessionState:
    """Mirrors a `sessions` row (storage.md §3.3) — the fold's unit of work.

    `signal_blob["events"]` is the session's normalized-event dicts, in order
    (`{event_type, record_id, parent_id, event_ts, payload}`). Dedup is positional against
    that list's length (append-only prefix-extension, see GraphFold.fold) — there is no
    per-event id ledger, since a normalized event's `record_id` is `None` for derived events
    (session_start, file_change) and cannot serve as a dedup key (ingest.md §14)."""

    session_id: str
    producer_id: str
    org_id: str | None
    user_id: str | None
    agent: str
    agent_version: str | None
    status: str
    started_at: datetime
    last_event_at: datetime
    ended_at: datetime | None
    signal_blob: dict
    info_blob: dict
    parent_session_id: str | None
    subagents: list


@dataclass
class SessionInfo:
    """Mirrors a `session_info` row (storage.md §3.4) — the fold's analytics output."""

    session_id: str
    cost_usd: Decimal | None = None
    models: dict = field(default_factory=dict)
    tools: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)
    skills: list = field(default_factory=list)
    subagents: dict = field(default_factory=dict)  # keyed by child_session_id (ingest.md §7)
    pr_list: list = field(default_factory=list)
    turns: list = field(default_factory=list)  # root-session only (worker plan §2, §3)
    turn_count: int = 0
    long_turn_count: int = 0
    # Sort/filter scalars (dashboard-fold-implementation plan F1). `tokens_total` promotes
    # `info["tokens_total"]` -- metrics.tokens_total(models) is the one source of truth,
    # both are set from it (never compute it twice). `subagent_count` is the spawn count
    # from this session's own events on a plain fold; rollup_from_family overrides it to
    # len(subagents) once children have actually arrived -- the two agree once every spawn's
    # child has folded.
    tokens_total: int = 0
    compaction_count: int = 0
    subagent_count: int = 0
    error_count: int = 0
    info: dict = field(default_factory=dict)
    # Promoted out of info["repo"] (dashboard plan §3) -- the session's primary repo (highest-
    # provenance evidence; metrics.py's repo_info), for a real GROUP BY instead of a JSON dig.
    repo_id: str | None = None
    repo_id_provenance: str | None = None


@dataclass
class SessionFoldResult:
    """What `fold()` wants persisted for the owning session (ingest.md §8)."""

    session: SessionState
    session_info: SessionInfo
    # The causal graph built for this session (GraphFold), for the handler to upsert into
    # session_graph. None for a fold that produces no graph.
    graph: dict | None = None


@dataclass
class SessionInfoDelta:
    """What `rollup_from_family()` wants persisted onto the parent's `session_info`."""

    session_info: SessionInfo


class SessionFold(Protocol):
    def fold(self, session: SessionState, session_events: list[dict]) -> SessionFoldResult:
        """Fold one arrived session's events (its `Envelope.payload["events"]`, a list of
        normalized-event dicts) into the session. One msg = one session (event-messaging-
        design.md §1), so `session_events` is the WHOLE session; the fold dedups
        **positionally** (append-only prefix-extension) and recomputes analytics from the full
        blob. MUST be idempotent — a resent session appends nothing and recomputes identically.
        Returns the mutations to persist (blob delta, session_info, lifecycle fields)."""
        ...

    def rollup_from_family(self, parent: SessionState, family: object) -> SessionInfoDelta:
        """Recompute `parent`'s COMPLETE `session_info` (own fields + the subagents map + inclusive
        totals) from an already-loaded causal-graph `Family` (its descendants resolved -- the rollup
        handler loads it via GraphService). MUST be idempotent (keyed replacement, not addition) and
        return a COMPLETE SessionInfo -- the worker persists it as a full replace, so a partial here
        would clobber fields with defaults on a child-doorbell pass.

        `family` is typed `object` to keep this seam free of an analysis.causal_graph import; the one
        implementation (GraphFold) takes the real `Family`."""
        ...
