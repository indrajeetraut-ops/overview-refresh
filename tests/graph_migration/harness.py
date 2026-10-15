"""Harness for the graph-migration tests: normalize a local Claude Code session in memory (master +
subagents) with the real `ClaudeCodeNormalizer`, and assemble the causal-graph `Family` from it.

`build_graph_family` builds each session's graph and stitches the family (the in-memory equivalent of
GraphService loading from the DB); `graph_snapshot_family` runs GraphFold's `build_session_info` over
it to snapshot `session_info`. The old EconFold-based oracle lived here too until the fold was removed;
the frozen `baseline_fixtures.json` it produced remains the A/B reference.

Determinism: a **fixed** `sent_at` fallback (never wall-clock), so a snapshot depends only on the
session's own event content -- reproducible.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from collector.normalizer.claude_code import ClaudeCodeNormalizer
from collector.normalizer.local_loader import load_session_source
from collector.normalizer.models import NormalizedSession
from server.worker.fold_api import SessionInfo, SessionState

# A fixed epoch used only as the leading-untimed-record fallback and the placeholder row time.
# Any constant works; it must not be wall-clock, or a family with leading untimed records would
# produce a non-reproducible snapshot.
_FIXED_SENT_AT = "2026-01-01T00:00:00.000000+00:00"
_FIXED_ROW_TIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _to_events(session: NormalizedSession, sent_at: str = _FIXED_SENT_AT) -> list[dict]:
    """A NormalizedSession's events -> the normalized-event dicts fold consumes, mirroring
    collector/engine._serialize_event and tests/.../recorded_turns._to_events: carry the last real
    timestamp forward across untimed passthrough records so the turn duration math stays inside the
    session's own timeline."""
    out: list[dict] = []
    last_known_ts = sent_at
    for event in session.events:
        ts = event.event_ts or last_known_ts
        last_known_ts = ts
        out.append(
            {
                "event_type": event.event_type,
                "record_id": event.record_id,
                "parent_id": event.parent_id,
                "event_ts": ts,
                "payload": event.payload,
            }
        )
    return out


def _new_session(session_id: str, parent_session_id: str | None, agent: str = "claude_code") -> SessionState:
    return SessionState(
        session_id=session_id,
        producer_id="oracle",
        org_id=None,
        user_id=None,
        agent=agent,
        agent_version=None,
        status="live",
        started_at=_FIXED_ROW_TIME,
        last_event_at=_FIXED_ROW_TIME,
        ended_at=None,
        signal_blob={},
        info_blob={},
        parent_session_id=parent_session_id,
        subagents=[],
    )


def load_family(jsonl_path: str | Path) -> dict[str, NormalizedSession]:
    """{session_id: NormalizedSession} for the master at `jsonl_path` plus every discovered
    subagent, preserving parent_session_id linkage."""
    src = load_session_source(Path(jsonl_path))
    return {s.session_id: s for s in ClaudeCodeNormalizer().normalize(src)}


def master_session_id(jsonl_path: str | Path) -> str | None:
    sessions = load_family(jsonl_path)
    masters = [sid for sid, s in sessions.items() if s.parent_session_id is None]
    return masters[0] if masters else None


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"not JSON-serializable: {type(value).__name__}")


def serialize_session_info(info: SessionInfo) -> dict:
    """SessionInfo dataclass -> a plain JSON-safe dict (Decimal->str, datetime->iso). Round-trips
    through json so the differential runner compares the same shape a persisted row would carry."""
    import json

    raw = dataclasses.asdict(info)
    return json.loads(json.dumps(raw, default=_json_default, sort_keys=True))


def graph_snapshot_family(jsonl_path: str | Path) -> dict[str, dict]:
    """{session_id: serialized graph-based session_info} for the whole family -- the GraphFold
    extraction (build_session_info) over the in-memory family, the master carrying its tree-scoped
    fields. The replacement for the old EconFold snapshot now that the fold is gone."""
    from server.worker.graph_fold import build_session_info

    family, master, events_by_sid = build_graph_family(jsonl_path)
    return {
        sid: serialize_session_info(build_session_info(family, sid, events_by_sid.get(sid, [])))
        for sid in [master, *family.children]
    }


def build_graph_family(jsonl_path: str | Path):
    """Assemble the causal-graph `Family` for a session in memory (no DB), plus the per-session
    event dicts. Mirrors the CLI's build+load: build_graph per session, load_self + load_tree over
    an in-memory resolver, then unload_tree (the family object stays usable, the global registry is
    released so repeated test calls on the same session_id don't collide).

    Returns (family, master_session_id, events_by_session_id).
    """
    from analysis.causal_graph import GraphContext, build_graph

    sessions = load_family(jsonl_path)
    events_by_sid = {sid: _to_events(s) for sid, s in sessions.items()}
    docs = {
        sid: build_graph({
            "vendor": s.vendor, "session_id": sid, "source": s.source,
            "parent_session_id": s.parent_session_id, "events": events_by_sid[sid],
        })
        for sid, s in sessions.items()
    }
    master = next(sid for sid, s in sessions.items() if s.parent_session_id is None)
    ctx = GraphContext()
    ctx.load_self(docs[master])
    ctx.load_tree(lambda ids: {i: docs[i] for i in ids if i in docs})
    family = ctx.family
    ctx.unload_tree()
    return family, master, events_by_sid
