# Hand-off: carrying the `design/api-error-reliability-payload` additions into v2 `main` (2026-07-14)

> **For a fresh thread.** This document is self-contained — you do not need the originating
> conversation. Goal of the next thread: **add the missing design pieces produced on the branch
> into the now-authoritative v2 `main`, additively, without reworking the v2 architecture.**
> Tags: **VERIFIED** (checked against real files/docs) · **DECIDED** (settled in-thread) ·
> **OPEN** (needs a call or a spike).

---

## 0. How to use this

Five commits of design additions were made on branch `design/api-error-reliability-payload`
while `main` was independently reworked into **v2** (a new authoritative architecture doc plus
a v2 revision of the master design). The branch is built on the **pre-v2 base** (`31683aa`) and
was never merged. The branch's *content* (schema signals, GitHub-App webhook model, collector
store-and-forward design) is still wanted; its *framing* (metadata-only, device, queue, per-event
envelope) is superseded by v2. This hand-off tells you **what to carry, where it lands in v2, and
what not to touch.**

Read order for the next thread:
1. This doc (§1–§9).
2. `docs/design/system-architecture.md` on `main` — the v2 authority.
3. `docs/design/coding-econ-design-draft.md` on `main` — the v2 master doc (see its v2 revision note).
4. Only if reconciling the collector: the parked doc `docs/design/collector-store-and-forward.md`
   (on this branch; see §6).

---

## 1. Repo state (as of 2026-07-14)

- **`main` @ `c19187a`** — v2. Authoritative architecture = `docs/design/system-architecture.md`
  (Draft v1 for review; heavily expanded in `c19187a`). Master doc = `coding-econ-design-draft.md`
  **v2** (carries a revision note listing four reversals). The old
  `docs/plan/component-design-implementation-plan.md` (C1–C11) is **superseded** by the arch doc.
- **`design/api-error-reliability-payload` @ `d29564a`** — this branch. Five commits on base
  `31683aa`. Parked, not merged.
- A sixth collector commit (`fa5de5d`) was briefly committed to `main` by mistake, reverted, and
  re-parked here as `d29564a`. `fa5de5d` remains recoverable from the reflog if ever needed.

---

## 2. The v2 world the additions must fit (do **not** change these)

The next thread must respect these settled v2 decisions — they are the "don't rework the design"
guardrails. All are in `system-architecture.md` unless noted.

1. **Full content ships** (§6.2, §17). Metadata-only is withdrawn; message text and tool inputs
   travel with the metadata inside the org's trusted boundary. The salted **`input_hash` is
   rejected outright** (kills the intent signal; poisons queryability). Redaction in the
   normalizer is the only content filter, minimal by default. **Privacy drives no decision.**
2. **No queue** (§4.1, §8, §15). Postgres-as-queue / `SKIP LOCKED` is **permanently rejected**.
   Workers are **pollers** or **doorbell**-woken REST handlers; the DB query is the sole work
   authority; a **catch-up sweep** at startup + idempotency give crash-safety. Redis is the future
   concurrency seam.
3. **db-core mandate** (§4.4, §10). All DB access goes through the `db-core` talium package: every
   DDL/DML is a **named query in YAML** (`config/queries/*.yaml`); no SQL in Python; no ORM
   (Alembic rejected → versioned DDL + `schema_version`). Constraint (§10.2): **one transaction =
   one named query's statement list** — atomic "write results + mark progress" must be one
   multi-statement named query. db-core returns errors in a dict (never raises); check every call.
4. **llm-router package** (§11). All LLM calls go through the extracted `llm-router` package (thin
   API over LiteLLM, swappable, mandatory per-session metering, prefix/suffix caching).
5. **Producer identity** (§7, §18.1). `device_id` → server-issued **`producer_id`** at
   self-registration; api_key = ingest credential; zero-setup default org/user; `last_seen_at`
   liveness. Replaceable **auth module** (bearer keys v0; JWT the designated successor).
6. **Batch envelope** (§5.1). The wire unit is now **one `header` + `events[]` + `debug`** — a
   *batch* from one agent+session under a single header; `ext` → `debug`; `source: backfill` →
   `batch`; `num_events`; `org_id`/`user_id` optional (defaulted server-side). Per-event payload
   catalog stays in master doc §6.1.2.
7. **Data-driven metrics** (§9): a config metric registry, rollups from *session info* (never raw
   events), JSONB metric-map (no column-per-metric), rankings as read-time views.
8. **Module-per-design-doc** (§20). The component plan is replaced by a folder-level module map +
   a suggested write order (**Storage & DB-management first**, then event contract, ingest/producer,
   collector, workers, classifier, correlator/reconciler, llm-router).

Terminology deltas to apply everywhere: **backfill→batch mode · device→producer · ext→debug ·
queue/claim→doorbell/poll · input_hash→(removed)**.

---

## 3. The five branch commits — what & why (rationale)

| SHA | Title | What it added | Rationale (VERIFIED evidence) |
|---|---|---|---|
| `89493c8` | api_error reliability payload | New `api_error` event type + payload (HTTP status, retry counters, `x-should-retry`, retry_after_ms, request_id) in §6.1; feeds M-03 anomaly alerting; §4.7 governance-signals note; component wiring (error_counts/retry_total on session_metrics) | Claude Code JSONL `system` records with `subtype:"api_error"` carry the full HTTP error — status (401/429/529), headers, `maxRetries`/`retryAttempt`/`retryInMs` — **richer than OTLP** (VERIFIED, 52-transcript inventory). Our schema had no place for it. |
| `001f0f1` | compaction signal | New `compaction` event + payload (trigger, pre/post context tokens, duration, tools_pre); §2 term; normalizer note; session_metric aggregates (compaction_count, context_reclaimed_tokens); classifier segmenter boundary | Claude Code writes `compact_boundary` + `compactMetadata` (VERIFIED). It is a context-bloat/waste signal **and** the classifier design already *assumed* a compaction boundary the schema didn't carry — this closed that gap. |
| `68f948b` | cost-detail fields | `assistant_message` gains: cache-write **5m/1h TTL split**, **`server_tool_use`** counts, **`cache_miss`** diagnostics, **`reasoning_tokens`**; cost engine made TTL-aware + line-item complete (§4.5); pricing tables extended (§7.4, §12) | **Two are cost-correctness fixes.** Cache writes are priced by TTL (5m ≈ 1.25× input, 1h ≈ 2×; reads ≈ 0.1× — VERIFIED via claude-api skill), so a flat `cache_write_tokens` mis-prices. Server tools (web_search/fetch, code-exec) bill as **separate line items** → billed cost was undercounted. `cache_miss.missed_tokens` = recoverable waste (M-05). `reasoning_tokens` = a decomposition of `output_tokens` (never additive), the product's "reasoning budget" concern made measurable. |
| `c733330` | GitHub App webhook model | §6.4 rewritten to the **GitHub App installation model**; alternatives table; `installation` contract; §8 `INSTALLATION` entity; §14.2 enrollment tie-in; component/roadmap wiring | VERIFIED against GitHub docs: one App = one webhook endpoint for all tenants, disambiguated by `installation.id`; short-lived installation tokens replace PATs; **receiving webhooks needs only signature + installation.id** — token minting deferred to the v2 server-side diff fetch. GitLab/Bitbucket have no identical install model (separate receivers). |
| `d29564a` | collector store-and-forward design (C2–C4) | New component design `docs/design/collector-store-and-forward.md` supplying the reliability particulars C4 left open | The component plan literally flagged C4 "needs a design." See §6 below — this is the one piece needing genuine reconciliation, not just additive carry-over. |

---

## 4. Evidence base carried forward (so it isn't re-researched)

- **Private exploration hand-off** (a *different* repo):
  `/Users/anil/workspace/Talium/coding-agent-economics/docs/design/2026-07-09-system-design-handoff.md`
  and its companions `2026-07-09-jsonl-otlp-datapoint-inventory.md` (field-by-field JSONL vs OTLP,
  grounded on 52 Claude Code transcripts / ~19,900 records) and `2026-07-09-high-level-architecture.md`.
  Key VERIFIED facts used above: api_error/compaction/cache-5m1h/server_tool_use/cache_miss all in
  JSONL; **`effort` is NOT in JSONL** (reasoning spend recovered from `thinking` blocks); **Cursor
  local storage is economically empty** (no tokens/cost/model — economics are Admin-API/server-side).
- **claude-api skill** (Anthropic API reference): caching price tiers (5m 1.25× / 1h 2× / read 0.1×),
  Batch API 50% off, code-execution ~$0.05/hr — the cost-correctness basis for `68f948b`.
- **GitHub App verification** (web, 2026): installation-id disambiguation, single endpoint,
  short-lived installation tokens preferred over PATs, GitLab per-project / Bitbucket
  Connect-Forge differ. Marketplace *listing* is a separately reviewed process (making an app
  publicly installable is not).

---

## 5. Field-evaluation ledger — included vs dropped (DECIDED; don't re-litigate)

We went through candidate JSONL signals one by one. Decisions:

**Included** (carried in the branch commits): `api_error`, `compaction`, cache 5m/1h split,
`server_tool_use`, `cache_miss`, `reasoning_tokens`.

**Dropped / deferred, with reason:**

| Signal | Verdict | Why |
|---|---|---|
| `active_time` | Drop | OTEL-only; we ship JSONL-only. A timestamp-based engagement proxy is derivable server-side from existing `event_ts`/`duration_ms` — no schema field needed. |
| `start_type` (fresh/resume/continue) | Drop (noise) | Marginal; fresh-vs-continue only contextualizes cache metrics. User called it noise. |
| assistant response length | Drop | Redundant with `output_tokens`; the useful split (visible vs thinking) is served by `reasoning_tokens`. |
| refusals (`stop_details`) | Drop | Basic refusals already covered by existing `stop_reason`; security use is out of scope. |
| `service_tier` | Deferred | Cost-correctness edge (batch 0.5× / priority premium); low frequency for interactive CC. |
| `iterations[]` | Deferred | Finer intra-turn granularity; mostly redundant for totals; a struggle-count at most. |
| `inference_geo` | Drop | Compliance signal; compliance out of scope. |

Still-open catalog items never folded in (from the original reconciliation list): **edit
accept/reject** (RQ-03 auto-accept rate — arguably a real miss), `revision`/last-writer-wins
idempotency, git-email identity resolver, `pricing_coverage_pct` canary, OSS-viewer metrics (tool
success-rate, heatmap). Revisit if desired; not blocking.

---

## 6. Collector store-and-forward (C2–C4) — parked, needs reconciliation

The design is in `docs/design/collector-store-and-forward.md` **on this branch** (commit
`d29564a`). It is the one piece that is **not** a simple additive carry — v2 changes its
foundations. What it decided (still worth keeping):

- **DECIDED (in-thread):** the spool **owns durability** — the source checkpoint advances at
  `fsync`-to-spool time, decoupling reading from shipping (D1).
- **DECIDED:** **two lanes by upstream durability class** — a *durable lane* (Claude Code/Codex
  JSONL, Cursor SQLite; backpressures losslessly by pausing reads) and an *ephemeral lane* (plan/
  quota snapshots; never blocked). Cursor is polled but **durable-upstream** → durable lane.
- **DECIDED:** **NDJSON** segments (compact one-event-per-line) with a two-part torn-tail recovery
  rule; **one tailer + one shipper** thread multiplexing all agents/sessions/pollers via a work
  queue; segment lifecycle, crash matrix, and BOTE (100 MB durable cap ≈ 20 dev-days offline).

**Conflicts with v2 to resolve if this is carried into a Collector module doc:**

1. **D1 vs v2 §5.2.** The v2 arch re-asserts *"checkpoints advance only on ack"* — the exact
   checkpoint-on-ship model our D1 overturned. This is a **genuine open re-argument** (our critique:
   double-buffering + re-read-on-restart), not a settled point. Flag it explicitly in the module doc.
2. **Content collection changes the format calculus.** Our NDJSON rationale leaned on "metadata-only,
   low-stakes." v2 ships content (a large edit can be tens of KB). NDJSON still works (compact JSON
   escapes newlines) but the *rationale* and the BOTE sizing need redoing with a content term.
3. **Batch-envelope reshape changes the storage unit.** Our spool stores one `{header,payload,ext}`
   event per line; v2's wire unit is a *batch* with one header over `events[]`. The spool must store
   per-event records carrying enough header to regroup, and the shipper assembles same-agent/
   same-session events into envelopes at ship time.
4. **Cosmetic-but-pervasive:** device→producer, backfill→batch, ext→debug; and our backpressure
   proposal (D2/D3) vs v2 §5.2's "eviction alarms" — still worth proposing as the better policy.

**Recommendation:** carry this as the **Collector module design doc** (v2 module-write order #4),
reconciled per the above — not shoehorned into the arch doc, which keeps §6.3 as a stub on purpose.

---

## 7. Where each surviving addition lands in v2 (the actionable map)

| Addition | v2 home | Kind of edit |
|---|---|---|
| `api_error`, `compaction` event types + payloads | master doc §6.1.2 payload catalog (as `events[]` payload types) | **Additive** — add payload definitions |
| cost-detail fields on `assistant_message` (cache 5m/1h, server_tool_use, cache_miss, reasoning_tokens) | master doc §6.1.2 `assistant_message` payload | **Additive** |
| TTL-aware cache pricing + server-tool line items + cache-miss waste | Session/metrics worker (§8.4) + pricing rows (db-core named queries; §10) | Additive to worker + pricing; respects db-core |
| GitHub App webhook + `installations` | Arch already names "Git App webhook → ingest" (§2) signature-verified (§18.2); add the installation model detail to ingest/producer §7 and the `installations` table to the **Storage module doc** | Additive; rename device→producer |
| session_metric reliability/compaction/cost aggregates | metric registry (§9) as config entries (JSONB metric-map, not columns) | Additive, data-driven |
| Collector store-and-forward (C2–C4) | **Collector module design doc** (§6 above) | Reconcile, not just add |

**`input_hash` note:** the v1 classifier "no-progress-loop" signature keyed off `input_hash`, now
removed. With content shipped, that detection compares real content instead. The companion
classifier doc is already flagged for revision (arch §20.6) — carry this note into that revision.

---

## 8. Guardrails for the next thread ("don't change the design too much")

- **Additive only.** Add payload fields and worker outputs; do **not** restructure the batch
  envelope, the no-queue model, db-core, or producer identity.
- **Respect the §2 decisions** verbatim. No `input_hash`, content ships, no queue, db-core named
  queries, producer, batch mode.
- **One genuinely open re-argument** you may raise: collector D1 (checkpoint ownership) vs arch
  §5.2. Everything else is carry-with-rename.
- **Do not merge this branch into main.** Cherry-pick the *content* into v2's structure by hand;
  the branch's framing is stale. The branch stays as the rationale record.

---

## 9. OPEN verification items (carry the flags)

1. **reasoning-token subtotal** — confirm whether JSONL `usage` exposes a reasoning subtotal, or
   `reasoning_tokens` must be measured from thinking-block size (transcript spike).
2. **code-execution billing** — time-based ($/hr) but `server_tool_use` likely reports *counts*;
   code-exec cost stays approximate until a duration field is found.
3. **exact vendor field names / `cache_miss.reason_type` values** — pin against real transcripts.
4. **pricing-table source** for TTL cache-write + server-tool rates — the LiteLLM base map may lack
   the 5m/1h split and server-tool rates; maintain on top (§12 gap).
5. **`session_id: null` for `plan_usage_snapshot`** — the header says "null only for git_event";
   plan snapshots are also session-less. Small header-contract correction.
6. **GitLab/Bitbucket receivers** differ from GitHub's App model — separate integration each.

---

## 10. Pointers

- **Branch:** `design/api-error-reliability-payload` @ `d29564a` — 5 commits on base `31683aa`:
  `89493c8` (api_error), `001f0f1` (compaction), `68f948b` (cost-detail), `c733330` (GitHub App),
  `d29564a` (collector store-and-forward).
- **Reverted-from-main collector commit** (recoverable): `fa5de5d`.
- **v2 main** @ `c19187a`: `docs/design/system-architecture.md`, `docs/design/coding-econ-design-draft.md` (v2).
- **Private exploration:** `Talium/coding-agent-economics/docs/design/2026-07-09-*` (hand-off,
  jsonl-otlp inventory, high-level-arch).
