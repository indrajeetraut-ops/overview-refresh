# msging-rewrite Integration Plan

**Goal:** Integrate the `feature/msging-rewrite` library (a delivery-semantics rewrite of
`src/common/msging`) into `adapter-normaliser-impl`, keeping the normalizer and adapter
behaviourally intact. Ingest + the server-side pipeline are redone separately (P3).

**Reference:** `docs/design/event-messaging-design.md` (the rewrite's design, 1037 lines),
replacing the deleted `docs/modules/event-messaging.md`.

---

## 1. The design shift (how far it moved)

A **delivery-semantics rewrite**, not a refactor:

| Dimension | Old | New |
|---|---|---|
| Send | batch POST, ACK/NACK **in the response** | **fire-and-forget** POST → 202; ACK/NACK **async over SSE** (`notify_ev`) |
| Durability | server-side spool | **client-side durable msg store** (file-per-msg, block-reserved `msg_id`), restart-reconciled |
| Granularity | **one event = one envelope** | **one msg = one POST = one session file** (producer batches) |
| Server→client | none | **SSE channel** with `server_epoch`, keepalive, cleaner loop |
| Ordering | seq-ordered | none on the wire; ordering lives in the payload |

Load-bearing consequence: the collector **can no longer checkpoint at send time**. The design
explicitly supports our case — `on_notify(pid, cb, notify_on_ack=True)` is documented as *"for
producers that gate on confirmed delivery, e.g. marking a source file done."* Checkpointing
moves from "flush succeeded" to "ACK notification arrived."

## 2. Code-level changes

- Restructured into `clnt/` (`api, transport, window, sse, cleaner, store, msg_id, binding`)
  and `srv/` (`api, router, auth, notify, queue, producer_store`). Deleted: `client.py`,
  `server.py`, `shipper.py`, `spool.py`, `store.py`, `ids.py`.
- **`EventIn` removed.** **`Envelope` flattened** to `{schema_version, event_id, producer_id,
  msg_id, service_name, payload, info}` — **`EnvelopeHeader` no longer exists**. `payload`/`info`
  are deliberately unschema'd. **`ProducerRecord` survives unchanged.**

## 3. Coupling blast radius

**Client side (collector):**
| Import | File | Fate |
|---|---|---|
| `EventIn` | `normalizer/models.py` | **P1 — done** (now normalizer-owned) |
| `EventClient` | `engine.py` | P2 — `client.py` gone → `clnt/api.py` |
| `FileProducerStore, MsgServer` | `dump_transport.py` | P2 — server moved to `srv/` |
| `ClientConfig` | `config.py` | P2 — config rewritten (§5.1) |

**Server side:**
| Import | Files | Fate |
|---|---|---|
| `ProducerRecord` | `storage/producer_store.py` | **survives** — no change |
| `MsgServer, AckResponse, make_store, Envelope` | `ingest/*` (5 files) | P3 |
| `Envelope` (7 files), `EnvelopeHeader` (`fold_session.py`) | `worker/fold/*` | **P3** — the built fold reads the *old* per-event envelope; under decision #2 it must read the new per-session payload instead |

## 4. Confirmed decisions

1. **Wholesale replacement** of `src/common/msging` (+ tests, config, design doc) via
   `git checkout origin/feature/msging-rewrite -- <paths>`, not a full branch merge (avoids
   doc conflicts on `normalizer.md`/`source-adapter.md`).
2. **One `NormalizedSession` = one msg**: events → `Envelope.payload`, identity (session_id,
   vendor, parent_session_id) → `Envelope.info`.

## 5. Phased plan

- [x] **P1 — Decouple the normalizer.** `EventIn` now defined in `normalizer/models.py` (fields
      verbatim). Zero msging imports in the normalizer. **Proof:** full suite 55/55 green,
      including the shape contract **with no golden regeneration** → output byte-identical.
- [x] **P0 — Land the new library.** Done (uncommitted on `adapter-normaliser-impl`). Removed
      the 6 old modules + 5 old tests + `docs/modules/event-messaging.md`; checked out the
      rewrite's `src/common/msging`, `tests/common/msging`, `config/msging.yaml`, and design docs.
      Merged deps (bumped `fastapi>=0.115`, added `pytest-asyncio>=0.24`, **kept `boto3`** — the
      rewrite drops it but our uploader needs it). Registered the `slow` marker in `pytest.ini`.
      **Proof:** msging suite **777 passed**; normalizer **55 passed** (P1 held). Confirmed-broken
      and scoped: `collector.engine`/`dump_transport` → P2; `ingest`/`worker.fold_session`
      (`EnvelopeHeader` gone) → P3.
- [x] **P2 — Rebuild the adapter transport binding.** Done (uncommitted). `engine.py` rewired
      to the `clnt` API (`register` → `on_notify(notify_on_ack=True)` → per-session `send` →
      **checkpoint committed in the ACK callback**, race-safe via a `sealed`/`pending` map →
      `close`). Decision #2 implemented: one `NormalizedSession` = one msg, events in
      `payload`, identity in `info`. `dump_transport.py` rebuilt as a fake in-process Ingest
      (`_FakeIngest`) that captures Envelopes and streams real `event: notify` ACKs back over
      SSE, so the checkpoint-on-ACK path runs for real. `config.py` → `MsgingConfig`;
      `config/collector.yaml` + `cli_collect.py` updated. **Proof:** `tests/collector` **78
      passed** (4 rewritten e2e incl. skip/reship/crash-resume, 1 dump, 55 normalizer); manual
      `cli_collect --dump-to` ships 2 per-session Envelopes and re-run skips.
- [ ] **P3 — Redo server-side (deferred, after P1+P2).** Rebuild `ingest` on `srv/`
      (`srv_init`/`on_event`/`send_notice`); revisit `event_queue`/`queue_jobs`; **adapt the
      fold's input contract** to the new per-session payload (replaces its `Envelope`/
      `EnvelopeHeader` coupling).
- [x] **P4 — Worker fold built** (`EconFold`, commit `c7f2684`) — but against the old envelope;
      its input contract is re-pointed in P3.

## 6. Test plan — "normalizer and adapter continue to work as they did"

**Normalizer — bit-identical (already proven, P1).** The shape contract
(`tests/sample-data/normalizer-shape-contract.json`) freezes every payload key-path + type per
event_type; it passes post-decouple **without regeneration**, proving the rewrite didn't perturb
the normalizer. Corroborated by the recorded-fixture real-data tests + idempotency/losslessness.
Re-run after P0 to confirm the library swap is still transparent.

**Adapter — equivalent output, new mechanics.** "As it did" can't apply to the wire protocol
(async ACK, per-session granularity are the point). What we pin:
1. **Golden of shipped payloads** — capture what the adapter ships for the recorded fixtures
   before P2 (current `--dump-to`) and after; assert the **set of normalized events per session
   is identical** (content-equivalence, not byte-equivalence).
2. **Re-create the e2e harness** (`test_engine.py`'s 4 tests) against the new transport using
   `srv/_testing.py` in place of `httpx.MockTransport`.
3. **New tests for genuinely-changed semantics** — checkpoint-on-ACK, NACK → `resolve`,
   crash-resume under the durable store.

Honest headline: the **normalizer is provably identical**; the **adapter ships the same data**
but its delivery/checkpoint semantics change by design.
