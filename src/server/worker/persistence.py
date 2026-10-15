"""SessionState/SessionInfo <-> DB row mapping (storage.md §3.3-3.4). Shared by the ingest
fold handler (server/ingest/fold_handler.py, the arrival fold) and the rollup worker
(server/worker) -- the fold's *meaning* lives in fold/, its *persistence* here.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session as DbSession

from server.storage import session_scalars
from server.storage.queries import QueryCatalog
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo as SessionInfoRow

from .fold_api import SessionInfo, SessionState


def row_to_state(row: SessionRow) -> SessionState:
    return SessionState(
        session_id=row.session_id,
        producer_id=row.producer_id,
        org_id=row.org_id,
        user_id=row.user_id,
        agent=row.agent,
        agent_version=row.agent_version,
        status=row.status,
        started_at=row.started_at,
        last_event_at=row.last_event_at,
        ended_at=row.ended_at,
        signal_blob=row.signal_blob,
        info_blob=row.info_blob,
        parent_session_id=row.parent_session_id,
        subagents=row.subagents,
    )


def persist_session(s: DbSession, session: SessionState) -> None:
    row = s.get(SessionRow, session.session_id)
    row.status = session.status
    row.started_at = session.started_at
    row.last_event_at = session.last_event_at
    row.ended_at = session.ended_at
    row.signal_blob = session.signal_blob
    row.info_blob = session.info_blob
    row.parent_session_id = session.parent_session_id
    row.subagents = session.subagents


def persist_session_info(s: DbSession, session_id: str, info: SessionInfo) -> None:
    # A full replace is safe because both fold() and rollup_from_family() are contractually
    # required to return a COMPLETE SessionInfo, not a delta (fold_api.py) -- rollup recomputes
    # the owning session's own fields from its graph and adds subagents/inclusive/the practices
    # delegation ratio on top, so a rollup-only pass never clobbers fold()'s fields with
    # defaults (see GraphFold.rollup_from_family, dashboard-fold-implementation plan F0.2).
    #
    # cost_effective_usd_micros/datasource/jsonl_lag_s are the true-up worker's columns
    # (dashboard-fold-implementation plan F1) -- deliberately absent here.
    now = datetime.now(timezone.utc)
    row = s.get(SessionInfoRow, session_id)
    if row is None:
        row = SessionInfoRow(session_id=session_id, created_at=now, updated_at=now)
        s.add(row)
    row.cost_usd = info.cost_usd
    row.models = info.models
    row.tools = info.tools
    row.files = info.files
    row.skills = info.skills
    row.subagents = info.subagents
    row.pr_list = info.pr_list
    row.turns = info.turns
    row.turn_count = info.turn_count
    row.long_turn_count = info.long_turn_count
    row.tokens_total = info.tokens_total
    row.compaction_count = info.compaction_count
    row.subagent_count = info.subagent_count
    row.error_count = info.error_count
    row.info = info.info
    row.repo_id = info.repo_id
    row.repo_id_provenance = info.repo_id_provenance
    # api-latency plan Phase E: the fold-owned scalar columns, derived from `info`/`files`
    # here -- the single chokepoint for every fold path, rollup_from_family included --
    # rather than stored on `fold_api.SessionInfo` itself (D5/D6). Never written by the
    # true-up (`otel_vendor_cost_usd_micros` is its own column, set in `_write_cost_block`).
    for col, value in session_scalars.fold_scalars(info.info, info.files).items():
        setattr(row, col, value)
    row.updated_at = now


def load_children_info(s: DbSession, catalog: QueryCatalog, child_ids: list[str]) -> list[SessionInfo]:
    rows = s.execute(catalog.get("fold", "session_info_of"), {"session_ids": child_ids}).mappings().all()
    return [
        SessionInfo(
            session_id=r["session_id"],
            cost_usd=r["cost_usd"],
            models=r["models"],
            tools=r["tools"],
            files=r["files"],
            skills=r["skills"],
            subagents=r["subagents"],
            pr_list=r["pr_list"],
            turns=r["turns"],
            turn_count=r["turn_count"],
            long_turn_count=r["long_turn_count"],
            tokens_total=r["tokens_total"],
            compaction_count=r["compaction_count"],
            subagent_count=r["subagent_count"],
            error_count=r["error_count"],
            info=r["info"],
        )
        for r in rows
    ]
