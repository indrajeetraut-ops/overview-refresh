"""The ingest fold handler -- the msging `on_event` callback that folds one arrived session
inline (ingest.md §3.3). One msg = one session (event-messaging-design.md §1): its events ride
in `envelope.payload["events"]`, its identity in `envelope.info`.

Lifecycle per msg (ingest.md §3.3, one session_scope transaction):
  ensure the sessions row exists (race-safe) -> lock it (per-session serialization) ->
  fold (positional dedup + own session_info) -> ring the parent's rollup if a subagent ->
  commit -> send_notice(ack). Any failure before the ACK -> rollback + send_notice(nack); the
  collector resolves it (resend/purge), so at-least-once holds with no server-side staging.

The handler is async so the sync DB fold is offloaded to a thread (`asyncio.to_thread`) and
never blocks the msging event loop (srv/queue.py §6.4). `send_notice` is safe from that thread.
"""
from __future__ import annotations

import asyncio

import structlog

from common import git_ident
from common.msging import srv
from server.storage.engine import session_scope
from server.storage.queries import QueryCatalog
from server.storage.schema import Session as SessionRow
from server.worker.fold_api import SessionFold
from server.worker.graph_build import persist_graph
from server.worker.persistence import persist_session, persist_session_info, row_to_state

log = structlog.get_logger(module="ingest.fold_handler")


def make_handler(catalog: QueryCatalog, fold: SessionFold):
    """Builds the `on_event` callback bound to a query catalog + a SessionFold."""

    async def on_event(envelope) -> None:
        await asyncio.to_thread(_fold_one, catalog, fold, envelope)

    return on_event


def _fold_one(catalog: QueryCatalog, fold: SessionFold, envelope) -> None:
    producer_id, msg_id = envelope.producer_id, envelope.msg_id
    info = envelope.info or {}
    session_id = info.get("session_id")
    try:
        if not session_id:
            raise ValueError("envelope.info.session_id is required")
        events = (envelope.payload or {}).get("events", [])
        with session_scope() as s:
            user_id = info.get("user_id")
            github_user = info.get("github_user")
            if github_user:
                github_user = github_user.strip().lower()
                canonical = s.execute(
                    catalog.get("ingest", "resolve_canonical_user_id_by_github_login"),
                    {"github_login": github_user},
                ).scalar()
                if canonical and canonical != user_id:
                    # A `users` row already claims this GitHub login under a DIFFERENT
                    # user_id -- most likely git-source's own auto-created "github:<login>"
                    # stub (apply.py `resolve_user_id`), created because a PR from this person
                    # landed before they ever declared their handle here. Adopt it as canonical
                    # rather than forking a second identity: this session (and this upload's
                    # account_uuid/github_user declarations below) fold under THAT user_id, so
                    # `rollup_by_user` and future PR resolution both land on the same row.
                    log.info("user_id_resolved_to_canonical", declared_user_id=user_id,
                            canonical_user_id=canonical, github_user=github_user)
                    user_id = canonical
            s.execute(
                catalog.get("ingest", "ensure_session"),
                {
                    "session_id": session_id,
                    "producer_id": producer_id,
                    "org_id": info.get("org_id"),
                    "user_id": user_id,
                    "agent": info.get("vendor") or "unknown",
                    "agent_version": info.get("agent_version"),
                    "parent_session_id": info.get("parent_session_id"),
                },
            )
            account_uuid = info.get("account_uuid")
            if account_uuid and user_id:
                # Links this session's user_id to the vendor account identity OTel carries
                # (otel_trueup.py's `_ensure_session_row` resolves through it) -- declared once
                # at upload time, never inferred. Cheap and idempotent; the WHERE in the query
                # itself skips the write once the link already matches.
                s.execute(
                    catalog.get("ingest", "upsert_user_account_uuid"),
                    {"user_id": user_id, "account_uuid": account_uuid},
                )
            if github_user and user_id:
                s.execute(
                    catalog.get("ingest", "upsert_user_github_login"),
                    {"user_id": user_id, "github_user": github_user},
                )
            s.execute(catalog.get("ingest", "lock_session"), {"session_id": session_id})
            state = row_to_state(s.get(SessionRow, session_id))

            result = fold.fold(state, events)
            persist_session(s, result.session)
            persist_session_info(s, session_id, result.session_info)
            # Persist this session's causal graph (source of truth for the tree). GraphFold returns
            # it built; the rollup materializes the family tree from these rows.
            if result.graph is not None:
                persist_graph(s, session_id, result.session.parent_session_id, result.graph)

            repo_id = result.session_info.repo_id
            if repo_id:
                kind, label = git_ident.describe(repo_id)
                s.execute(catalog.get("ingest", "upsert_repository"), {
                    "repo_id": repo_id, "kind": kind, "label": label,
                    "provenance": result.session_info.repo_id_provenance,
                })

            # Rollup triggers (ingest.md §7). Ring the parent when a subagent commits, AND ring
            # this session when it has children -- the latter covers children-arrive-first: their
            # rollup jobs no-op while this parent row is absent, so the parent must re-ring itself
            # once it folds. A childless root rings nothing (the common, no-overhead case).
            parent_session_id = result.session.parent_session_id
            if parent_session_id:
                s.execute(catalog.get("ingest", "ring_rollup"), {"session_id": parent_session_id})
            has_children = s.execute(catalog.get("fold", "children_of"), {"session_id": session_id}).first() is not None
            if has_children:
                s.execute(catalog.get("ingest", "ring_rollup"), {"session_id": session_id})
            folded = len(result.session.signal_blob.get("events", []))
    except Exception as exc:  # noqa: BLE001 -- any failure NACKs; the collector resolves it
        srv.send_notice(producer_id, msg_id, "nack", reason=type(exc).__name__)
        log.warning("session_nacked", session_id=session_id, msg_id=msg_id, error=type(exc).__name__)
        return

    srv.send_notice(producer_id, msg_id, "ack")
    log.info("session_folded", session_id=session_id, msg_id=msg_id, delivered=len(events), folded=folded)
