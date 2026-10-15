"""Re-fold already-ingested sessions from their stored events. Run:

    python -m server.worker.cli_refold [--config PATH] [--session-id ID ...] [--dry-run]

The ingest fold only runs when new events arrive (positional dedup in `GraphFold.fold`), so a
change to what the fold computes never reaches sessions ingested before it. Every session's full
normalized event stream is already persisted in `sessions.signal_blob`, so re-folding needs no
re-ingest: this rebuilds each session's graph and `session_info` exactly the way the arrival fold
would, then re-runs the rollup for every session with children so tree-scoped fields (subagents
map, inclusive tokens) are restored on top.

Only fold-owned columns are rewritten (`persist_session_info`); the true-up worker's cost columns
(`cost_effective_usd_micros`, `datasource`, `jsonl_lag_s`) and the `sessions` row are untouched.
Children fold before parents, one transaction per session, so a failure leaves every other session
consistent and the run can simply be repeated.
"""
from __future__ import annotations

import argparse

import structlog
from sqlalchemy import select

from server.storage.config import load_config as load_storage_config
from server.storage.engine import get_engine, session_scope
from server.storage.queries import QueryCatalog
from server.storage.schema import Session as SessionRow

from .config import load_config
from .graph_build import persist_graph
from .graph_fold import GraphFold, load_pricing_survivable
from .persistence import persist_session_info, row_to_state
from .rollup import make_rollup_handler

log = structlog.get_logger(module="worker.cli_refold")


def _session_ids(requested: list[str] | None) -> list[str]:
    """Children (parent set) first, then roots, so a parent's rollup reads freshly built child graphs."""
    with session_scope() as s:
        stmt = select(SessionRow.session_id, SessionRow.parent_session_id)
        if requested:
            stmt = stmt.where(SessionRow.session_id.in_(requested))
        rows = s.execute(stmt).all()
    missing = sorted(set(requested or []) - {r.session_id for r in rows})
    if missing:
        log.warning("refold_sessions_not_found", session_ids=missing)
    return [r.session_id for r in sorted(rows, key=lambda r: (r.parent_session_id is None, r.session_id))]


def refold_session(catalog: QueryCatalog, fold: GraphFold, session_id: str) -> bool:
    """Re-fold one session in its own transaction. Returns False when it has no stored events."""
    rollup = make_rollup_handler(fold)
    with session_scope() as s:
        s.execute(catalog.get("ingest", "lock_session"), {"session_id": session_id})
        row = s.get(SessionRow, session_id)
        state = row_to_state(row)
        events = list(state.signal_blob.get("events", []))
        if not events:
            return False
        result = fold.fold(state, events)
        persist_session_info(s, session_id, result.session_info)
        if result.graph is not None:
            persist_graph(s, session_id, result.session.parent_session_id, result.graph)
        s.flush()
        if s.execute(catalog.get("fold", "children_of"), {"session_id": session_id}).first() is not None:
            rollup(s, catalog, {"session_id": session_id})
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-fold stored sessions with the current fold.")
    parser.add_argument("--config", default="config/worker.yaml")
    parser.add_argument("--session-id", action="append", dest="session_ids",
                        help="re-fold only this session (repeatable); default: every session")
    parser.add_argument("--dry-run", action="store_true", help="list the sessions that would be re-folded")
    args = parser.parse_args()

    config = load_config(args.config)
    storage_cfg = load_storage_config(config.storage_config)
    get_engine(storage_cfg)
    catalog = QueryCatalog.load(storage_cfg.queries_dir)

    session_ids = _session_ids(args.session_ids)
    log.info("refold_started", sessions=len(session_ids), dry_run=args.dry_run)
    if args.dry_run:
        for session_id in session_ids:
            log.info("refold_would_fold", session_id=session_id)
        return

    fold = GraphFold(pricing=load_pricing_survivable(config.pricing_org, config.pricing_version))
    counts = {"refolded": 0, "no_events": 0, "failed": 0}
    for session_id in session_ids:
        try:
            if refold_session(catalog, fold, session_id):
                counts["refolded"] += 1
                log.info("session_refolded", session_id=session_id)
            else:
                counts["no_events"] += 1
                log.info("session_refold_skipped_no_events", session_id=session_id)
        except Exception as exc:  # noqa: BLE001 -- one bad session must not stop the rest
            counts["failed"] += 1
            log.warning("session_refold_failed", session_id=session_id, error=type(exc).__name__,
                        detail=str(exc))
    log.info("refold_finished", **counts)
    if counts["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
