# P3 — Server-side redesign for the msging-rewrite

**Goal:** Rebuild the server side (ingest + worker) onto the new msging model per the revised
`docs/modules/ingest.md`: ingest becomes a msging `srv` host that folds each arrived session inline,
`event_queue` is dropped, and the worker shrinks to the rollup trigger. Follows P0–P2 (collector side
done, commit `0509c71`).

**Reference:** `docs/modules/ingest.md` (revised design), `docs/design/event-messaging-design.md` (§6.2
server API), `docs/modules/storage.md` §3.3–3.4.

---

## 1. Architecture decisions (from the revised ingest.md)

1. **Ingest = msging `srv` + the fold.** Mount `srv_init("ingest")`, `on_event(handler)`,
   `send_notice(ack/nack)` after commit. No hand-rolled `MsgServer`, no batch.
2. **Fold at arrival, inline in `on_event`.** One msg = one session → append `signal_blob` + own
   `session_info`, then ACK. **ACK-after-fold** (v1); ACK-after-land is a seam (ingest.md §12).
3. **`event_queue` deleted.** `sessions.signal_blob` is the landing zone; durability is the client's
   (at-least-once, resend until ACK).
4. **Per-session serialization = `SELECT … FOR UPDATE`** on the `sessions` row.
5. **`queue_jobs` retained, re-scoped** to the parent-rollup trigger (+ future `cost`/`git`); the
   rollup worker consumes it.

## 2. Coupling deltas (what P0 broke, what P3 fixes)

| Area | Old | New |
|---|---|---|
| `src/server/ingest/*` | `MsgServer`, `AckResponse`, `make_store`, `Envelope.header`, `event_queue` staging | msging `srv` host; `on_event` fold handler; reads `envelope.payload.events` + `info` |
| `src/server/worker/fold_session.py` | drains `event_queue`, `EnvelopeHeader` | gone — the arrival fold moves to ingest; the worker keeps only `rollup` |
| `src/server/worker/fold/*` | `fold(session, new_events: list[Envelope])`, `.header.*` | `fold(session, new_events: list[dict])` from `payload.events`; identity from `info` |
| `schema/event_queue.py` | present | **removed** |
| `schema/queue_jobs.py` | per-event dispatch | retained, re-scoped (topic `rollup`) |
| `config/queries/{ingest,fold}.yaml` | stage/drain/sweep | `lock_session`, `ring_rollup`, `claim_job`, `children_of` |

## 3. Phased tasks

- [x] **P3.0 — Fold input contract.** Done. `SessionFold.fold(session, session_events: list[dict])`
      (`payload.events`), **positional dedup** (append-only prefix-extension) replacing the
      `record_id` ledger; `fold/` (EconFold, extractors, turns, metrics) + `fold_stub` + test helpers
      converted. 41 fold tests pass; idempotency validated on the real 27-task session.
- [x] **P3.1 — Schema + queries.** Done. Deleted `schema/event_queue.py`; re-scoped `queue_jobs`
      (topic `rollup`); `config/queries/ingest.yaml` → `ensure_session`/`lock_session`/`ring_rollup`,
      `fold.yaml` → `claim_job`/`mark_*`/`children_of`/`session_info_of` (event_queue drains removed).
      Storage schema tests + golden DDL updated.
- [x] **P3.2 — Ingest as a `srv` host + fold handler.** Done. `app.py` mounts `srv_init`/registers
      `on_event`/`make_app` lifecycle; `fold_handler.py` = ensure-row → `FOR UPDATE` lock → fold →
      ring parent → `send_notice(ack)` (NACK on error) in one `session_scope`, offloaded via
      `asyncio.to_thread`. Shared `worker/persistence.py`. Old `MsgServer` routes removed.
- [x] **P3.4 — Wire + config.** Done as part of P3.2 (`cli_serve` boots the srv host; `IngestConfig`
      trimmed to reference `config/msging.yaml`).
- [x] **P3.3 — Rollup worker.** Done. `src/server/worker` reduced to `dispatch.py` (generic
      claim/run, orphan-sweep removed) + `rollup.py` (claim `rollup` → lock parent → `rollup_subagents`
      from children → propagate up). Deleted `fold_session.py`; `cli_work` runs the `rollup` topic.
      Ingest handler rings `ring_rollup(self)` when it has children (covers children-arrive-first).
      New tests: `test_rollup`, `test_dispatch` (generic), `test_fold_handler`; old event_queue/route
      tests removed. The full-pipeline acceptance test (`test_ingest_to_fold.py`) is a DSN-gated,
      uvicorn-in-thread run of the real chain. **Full suite: 984 passed** with a live Postgres.

**End-to-end verified** (real uploader → collector → msging `srv` ingest → fold → Postgres): the
recorded `session-subagents` fixture folds into 3 `sessions` + 3 `session_info` rows (parent 440
events/12 turns/18M tokens/1 skill; two children linked via `parent_session_id`, turns=0), ACK
after commit, idempotent on the cleaner's resend, and the subagents fire the parent's `rollup`
`queue_jobs` trigger — ready for P3.3.

## 4. Test plan

- **Fold (P3.0):** golden over recorded sessions — same `session_info` from the new `dict` input as
  the P2 payloads carry; idempotent re-fold.
- **Ingest (P3.2):** an in-process msging client (P2's `_FakeIngest` inverse — use msging's real
  `srv` + the client) sends a session → one `sessions` row + `session_info`, ACK after commit; fold
  error → NACK, no partial row; resent session → zero economic change; two concurrent msgs for one
  session → row-lock serializes.
- **Rollup (P3.3):** child-first/parent-first converge; child resume replaces `subagents[child]`;
  two-level nesting propagates; lost trigger recovered by sweep.
- **Crash safety:** kill between commit and `send_notice` → resend re-folds as a no-op.

## 5. Open decision to confirm

**ACK timing** (ingest.md §12 item 1): v1 is **ACK-after-fold** (commit `sessions` + `session_info`
before ACK — simplest, ACK = fully processed). Switch to **ACK-after-land** (commit only the blob
append, defer `session_info` to a `refold` trigger) only if fold cost dominates ACK latency. Confirm
before P3.2.
