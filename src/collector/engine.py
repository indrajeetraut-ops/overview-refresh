"""The tail-with-checkpoint loop, batch-scoped (spec: docs/modules/source-adapter.md §4).

run_once(config, scope) only -- a batch pass: sweep scope for manifests, process every
not-yet-done source found, exit. run_forever (standing poll/doorbell) is a documented seam,
not built here (doc §11/§A2).

Rewritten for the msging fire-and-forget + async-ACK model (event-messaging-design.md §2.2):
`send()` makes one POST and returns immediately with a `msg_id`; the ACK/NACK for that msg
arrives *later* on the SSE notify callback (`notify_on_ack=True` -- the design's "mark a source
file done" case, §6.1). So a source's checkpoint commits from the callback, only once *every*
msg that source produced has been ACKed -- never at send time.

Granularity: **one NormalizedSession = one msg** (design §1 "one session file per msg"). Its
events ride in the Envelope payload; its identity in info. A session's subagents are their own
NormalizedSessions, hence their own msgs, all attributed to the same source's checkpoint.

Checkpoint attribution & the two races it survives (all shared state under `_lock`):
  * an ACK can land on the SSE thread *before* the send loop finishes enqueuing a source --
    so msg_ids join `pending[key]` at send time, not after the loop; and
  * an early ACK can empty `pending[key]` while more msgs are still coming -- so a key only
    commits once it is both drained *and* `sealed` (fully enqueued).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

import httpx
import structlog

import common.msging as msg
from common.manifest import Manifest

from .checkpoint import Checkpoint, make_checkpoint_store
from .claude_code.source_reader import ClaudeCodeSourceReader
from .config import CollectorConfig
from .normalizer.claude_code import ClaudeCodeNormalizer
from .normalizer.config import NormalizerConfig
from .normalizer.config import load_config as load_normalizer_config
from .normalizer.models import EventIn, NormalizedSession
from .store import make_source_store

log = structlog.get_logger(module="collector.engine")


@dataclass
class RunSummary:
    manifests_found: int = 0
    sources_processed: int = 0
    sources_skipped_unchanged: int = 0
    sources_incomplete: int = 0
    errors: list[str] = field(default_factory=list)


def _serialize_event(event: EventIn) -> dict:
    return {
        "event_type": event.event_type,
        "record_id": event.record_id,
        "parent_id": event.parent_id,
        "event_ts": event.event_ts,
        "payload": event.payload,
    }


def _envelope_payload(nsession: NormalizedSession) -> dict:
    """One session's events -> the opaque Envelope payload (decision #2)."""
    return {"events": [_serialize_event(e) for e in nsession.events]}


def _envelope_info(nsession: NormalizedSession, org_id: str | None, user_id: str | None,
                   account_uuid: str | None = None, github_user: str | None = None) -> dict:
    """Session + routing identity -> the Envelope info bag (decision #2). The wire header
    is gone (event-messaging-design.md §7); identity the old EnvelopeHeader carried now
    travels here for the consumer (ingest/fold) to key on.

    `account_uuid` -- the vendor account identity OTel carries in `user.account_uuid`, declared
    at upload time (uploader/models.py `DeclaredFields`) -- rides along so the ingest handler
    can link it to `user_id` in the `users` table, letting an otel_only session resolve to the
    same `user_id` instead of surfacing the raw account UUID (otel_trueup.py).

    `github_user` -- self-declared GitHub login (also `DeclaredFields`), rides along so the
    ingest handler can pre-populate `users.github_user`, letting git-source's own
    `resolve_user_id` find this `user_id` instead of auto-creating a separate `github:<login>`
    identity for this person's PRs."""
    return {
        "session_id": nsession.session_id,
        "vendor": nsession.vendor,
        "source": nsession.source,
        "agent_version": nsession.agent_version,
        "parent_session_id": nsession.parent_session_id,
        "org_id": org_id,
        "user_id": user_id,
        "account_uuid": account_uuid,
        "github_user": github_user,
    }


def _apply_git_remote(nsession: NormalizedSession, repo: str | None) -> None:
    """Backfills `session_start.git_remote` from the manifest's per-file repo (the upload's
    own `repo_url`/`repo`, resolved client-side from a real `git remote get-url origin` at the
    session's actual cwd -- source_reader.group()'s `repo=` line) -- the evidence the causal
    graph's repo-identity ladder needs to resolve past the `local:<cwd-basename>` floor
    (worker/graph_fold.py::repo_info). A no-op when repo is absent, or a subagent session
    whose group has no repo of its own (subagent sessions inherit the parent group's repo, so
    this only happens when the parent's own repo could not be resolved either)."""
    if not repo or not nsession.events:
        return
    session_start = nsession.events[0]
    if session_start.event_type != "session_start":
        return
    session_start.payload["git_remote"] = repo


@dataclass
class ManifestRef:
    manifest: Manifest
    prefix: str  # everything before "manifest.json", e.g. "cc/logs/uploads/ep-1/"


def _discover_manifests(source_store, scope: str) -> list[ManifestRef]:
    refs = []
    for key in source_store.list_prefix(scope):
        if not key.endswith("manifest.json"):
            continue
        with source_store.open(key, 0, None) as f:
            content = f.read()
        refs.append(ManifestRef(
            manifest=Manifest.model_validate_json(content),
            prefix=key[: -len("manifest.json")],
        ))
    return refs


def run_once(
    config: CollectorConfig,
    scope: str = "uploads/",
    *,
    transport: httpx.BaseTransport | None = None,
) -> RunSummary:
    source_store = make_source_store(config.source_store)
    checkpoint_store = make_checkpoint_store(config.checkpoint_store)
    reader = ClaudeCodeSourceReader(source_store)
    normalizer_config = (
        load_normalizer_config(config.normalizer_config) if config.normalizer_config else NormalizerConfig()
    )
    normalizer = ClaudeCodeNormalizer(normalizer_config)

    msg.clnt_init(config.msging)
    producer = msg.register({"producer_desc": "collector"}, _transport=transport)
    producer_id = producer["producer_id"]

    # -- checkpoint-on-ACK bookkeeping (mutated from BOTH the main loop and the SSE thread) --
    lock = threading.Lock()
    msgid_key: dict[int, str] = {}          # msg_id -> logical_key
    pending: dict[str, set[int]] = {}       # logical_key -> not-yet-ACKed msg_ids
    identity_of: dict[str, str] = {}        # logical_key -> source identity (the checkpoint value)
    events_of: dict[str, int] = {}          # logical_key -> event count (for logging)
    sealed: set[str] = set()                # keys fully enqueued (safe to commit once drained)
    done: set[str] = set()                  # keys whose checkpoint has been committed

    def _maybe_commit(logical_key: str) -> None:
        # caller holds lock. Commit only when the source is both fully enqueued and fully ACKed.
        if logical_key in sealed and logical_key not in done and not pending.get(logical_key):
            checkpoint_store.commit(logical_key, Checkpoint(identity=identity_of[logical_key], done=True))
            done.add(logical_key)
            log.info("source_processed", logical_key=logical_key, events=events_of.get(logical_key, 0))

    def on_notify(msg_id: int, notify_ev) -> None:
        # SSE reader thread (§6.4): thread-safe and quick.
        with lock:
            logical_key = msgid_key.get(msg_id)
            if logical_key is None:
                return
            if notify_ev.code == "ack":
                bucket = pending.get(logical_key)
                if bucket is not None:
                    bucket.discard(msg_id)
                    _maybe_commit(logical_key)
            # NACK: left in `pending` on purpose -- the source stays incomplete, its checkpoint
            # never commits, and the durable record resends on the next run's reconciliation
            # (at-least-once, §2.2). TODO(collector): explicit resolve() PURGE/RETAIN policy.

    msg.on_notify(producer_id, on_notify, notify_on_ack=True)

    summary = RunSummary()
    manifest_refs = _discover_manifests(source_store, scope)
    summary.manifests_found = len(manifest_refs)

    for ref in manifest_refs:
        manifest = ref.manifest
        for group in reader.group(manifest, ref.prefix):
            logical_key = f"{manifest.declared.org_id}/{manifest.declared.user_id}/{group.rel}"
            identity = reader.identity(group)
            ckpt = checkpoint_store.get(logical_key)
            if ckpt is not None and ckpt.done and ckpt.identity == identity:
                summary.sources_skipped_unchanged += 1
                continue

            session_source = reader.read_session_source(manifest, group, ref.prefix)
            event_count = 0
            for nsession in normalizer.normalize(session_source):
                _apply_git_remote(nsession, group.repo)
                result = msg.send(
                    producer_id,
                    _envelope_payload(nsession),
                    _envelope_info(nsession, manifest.declared.org_id, manifest.declared.user_id,
                                   manifest.declared.account_uuid, manifest.declared.github_user),
                )
                event_count += len(nsession.events)
                if result.msg_id is not None:
                    with lock:
                        msgid_key[result.msg_id] = logical_key
                        pending.setdefault(logical_key, set()).add(result.msg_id)
                else:
                    log.warning("send_not_stored", logical_key=logical_key, err_code=result.err_code)
            with lock:
                pending.setdefault(logical_key, set())  # a source with no stored msg still needs a bucket
                identity_of[logical_key] = identity
                events_of[logical_key] = event_count
                sealed.add(logical_key)
                _maybe_commit(logical_key)  # in case every msg ACKed while we were still enqueuing

    # Drain: close() flushes pending, waits for outcomes (callbacks fire here), then stops.
    close_result = msg.close(producer_id, timeout_s=config.flush_timeout_s)

    with lock:
        for logical_key in sealed:
            if logical_key in done:
                summary.sources_processed += 1
            else:
                summary.sources_incomplete += 1
                summary.errors.append(logical_key)
                log.warning("source_not_fully_acked", logical_key=logical_key, unacked=len(pending.get(logical_key, ())))

    log.info(
        "run_once_complete",
        manifests_found=summary.manifests_found,
        sources_processed=summary.sources_processed,
        sources_skipped_unchanged=summary.sources_skipped_unchanged,
        sources_incomplete=summary.sources_incomplete,
        close_status=close_result.status,
        close_unresolved=close_result.unresolved,
    )
    return summary
