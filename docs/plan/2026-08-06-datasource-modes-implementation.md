# Phase PM — Datasource Modes: Implementation Plan

**Goal:** make the three datasource modes (`both` / `jsonl_only` / `otel_only`) real in the data layer, so the dashboard API can be built against them instead of around them.

**Why this comes first:** two items here (PM.2 stub rows, PM.1 promoted columns) are the difference between the zero-install tier being visible and being absent from the product entirely. Both are cheaper to build before the API than to retrofit into it, and both touch the true-up worker's transaction — the part of this system with real correctness risk.

**Spec set (read before starting the task that cites it):**
- Mode model, capability matrix, cost precedence, `unavailable` state: `docs/design/dashboard-datasource-modes.md` (**MODES**)
- Metrics/data mapping, provenance, insights, APIs: `docs/design/user-dashboard-design.md` (**DASH**) — §7.1 is this phase's output contract
- OTel intake as shipped: `docs/design/2026-08-04-otel-intake-design.md` (**OTEL**) §4.3, §4.4
- Parent plan and its ground rules: `docs/plan/2026-08-01-user-dashboard-implementation.md` (**PLAN**) §0

**Status:** planned 2026-08-06. No Alembic migration exists yet — schema changes are SQLAlchemy-model edits, same stance as PLAN.

---

## 0. Ground rules specific to this phase

PLAN §0 applies unchanged. Four more, all learned from the code this phase edits:

1. **Idempotency is still the contract.** The true-up's whole design rests on "the fold reads, it never accumulates" (`otel_trueup.py` module docstring). Every value added here must be a pure function of stored rows. Re-ticking must produce a byte-identical block — that is what makes a retried tick free and a crashed tick safe.
2. **Two constructors build a `SessionInfo` row.** `persistence.py:56` (JSONL fold) and `otel_trueup.py:509` (true-up). They pass different argument sets, and a new NOT NULL column without a default breaks whichever one forgets it. Add columns nullable or defaulted; touch both call sites deliberately.
3. **The JSONL fold must never clobber true-up columns.** `persist_session_info` assigns fields one by one and deliberately omits `otel` — that omission is the ownership boundary. **Do not add the new columns to that assignment list.** A test must prove a JSONL re-fold leaves them intact.
4. **Nothing this phase writes may re-trigger its own re-fold.** `touched_sessions` has five arms that read the block and the `sessions` row to decide what to revisit. A value written here that satisfies one of those arms creates an infinite re-fold loop that looks like a busy worker, not like a bug. PM.2 has a live instance of this hazard — see its note.

---

## PM.0 — Bump the block version (do this first, it is one line)

**Edit:** `src/server/worker/otel_trueup.py`

- [ ] `BLOCK_VERSION = 3`, with a comment line describing shape 3 alongside the existing 1 and 2.

This is not bookkeeping. The `touched_sessions` block-version arm exists because a session holding an old-shaped block matches none of the other arms and is stranded silently — the comment at `otel_trueup.py:82-91` records that going unnoticed for 20 real sessions. PM.3 changes the block's shape, so every already-folded session must be revisited. Bumping first means every subsequent test in this phase runs against re-folded data rather than a mix.

**Done when:** an existing session with a v2 block is picked up by the next tick.

## PM.1 — Promoted columns

**Edit:** `src/server/storage/schema/session_info.py`, `src/server/worker/otel_trueup.py`, `docs/modules/storage.md`

- [ ] Add to `SessionInfo` (MODES §3.2), all three written by the true-up, never by the JSONL fold:

```python
# Written by the OTel true-up worker, not the JSONL fold (MODES §3.2). Outside the `otel`
# block because the sessions list sorts on cost and DASH §3.4 forbids JSONB sorting -- and
# because this figure covers JSONL-only sessions too, where a value inside a column named
# `otel` reads as a bug.
cost_effective_usd_micros: Mapped[int | None]
datasource:                Mapped[str | None]   # both | jsonl_only | otel_only
jsonl_lag_s:               Mapped[int | None]
```

- [ ] All three **nullable** — ground rule 2. A row created by `persistence.py:56` before any tick has no mode yet, and `None` is the honest value for that, not a guessed `jsonl_only`.
- [ ] `_write_cost_block` sets all three in the same statement as `row.otel = block`, from values `build_cost_block` already computed or that PM.4/PM.5 add.
- [ ] Index on `(datasource)` and on `cost_effective_usd_micros` — both are sessions-list sort/filter keys, which is the entire reason they are columns.
- [ ] Record the three columns in the storage module doc's `session_info` section, noting the second writer.

**Derivation of `datasource`** — from what the fold already has in hand, in this order:

| Condition | Value |
| :---- | :---- |
| any `sources.both > 0` | `both` |
| OTel evidence (`measures` non-empty or any OTel request) **and** no JSONL request rows | `otel_only` |
| JSONL request rows and no OTel evidence | `jsonl_only` |
| both kinds present but no matched request | `both` |

The last row is the case worth thinking about: a session whose JSONL and OTel both exist but whose request ids do not overlap is still a two-source deployment. Mode describes **which pipes are deployed**, not whether a particular join succeeded — conflating the two would flip a healthy org's mode on a single interrupted request.

Note the second row cannot be written as "no JSONL": a metrics-only fleet (`OTEL_LOGS_EXPORTER=none`) produces `measures` and **zero request records**, so a derivation that keys on the request ledger alone leaves it unclassified.

**Done when:** each of the three fixture modes folds to the expected `datasource`, and a JSONL re-fold of a `both` session leaves all three columns untouched (ground rule 3).

## PM.2 — Stub `sessions` rows for the zero-install tier

**Edit:** `src/server/worker/otel_trueup.py`, `config/queries/otel.yaml`

The gap: `_write_cost_block` (`otel_trueup.py:497`) creates the `session_info` row and stops. Nothing outside the JSONL ingest path creates a `sessions` row. Since `session_info` carries no `user_id`, `started_at` or `agent`, and every dashboard query filters on exactly those, **the zero-install tier is currently invisible to the dashboard including the spend truth it does have.**

- [ ] New `_ensure_session_row(s, vendor, session_id, org_id, earliest_event_ts, user_key)` called from `_fold_session` **before** `_write_cost_block`, in the same transaction.
- [ ] Creates only when absent; **never updates an existing row.** The JSONL path owns that row wherever it exists, and a true-up that edited it would fight the ingest on every tick.

| Column | Stub value | Note |
| :---- | :---- | :---- |
| `session_id` | the folded id | |
| `producer_id` | `f"otel:{org_id}"` | NOT NULL with no default; there is no producer, so name the absence rather than inventing a plausible id |
| `org_id` | from the token, via `org_for_session` | never from payload attributes (OTEL §4.5) |
| `user_id` | mapped `user_key` | null-safe; MODES §8's identity requirement is what makes this joinable |
| `agent` | `vendor` | NOT NULL |
| `status` | `'otel_only'` | a new value beside `live`/`ended`/`idle_closed` |
| `started_at` | earliest OTel `event_ts` for the session | **see the hazard below** |
| `last_event_at` | latest OTel `event_ts` for the session | **see the hazard below** |
| `signal_blob`, `info_blob`, `subagents` | ORM defaults (`{}`, `{}`, `[]`) | `jsb`/`jsl` carry python-side defaults; `ts` does not |

- [ ] New named query `otel_session_bounds` returning `min(event_ts), max(event_ts), any user_key` for `(vendor, session_id)` across `otel_api_requests`. For a metrics-only session with no request rows, fall back to the series' point timestamps.

> **Loop hazard (ground rule 4).** `touched_sessions`' fourth arm re-folds any session where `sess.last_event_at > (i.otel ->> 'computed_at')`. Setting the stub's `last_event_at` to `now()` makes that condition true on the very next tick, forever — a worker pinned at 100% that looks like load. It **must** come from OTel event timestamps, which are in the past by construction. Add a test that ticks twice and asserts the second tick touches zero sessions.

- [ ] **Expandability test (the promise the tier is sold on):** ship OTel for a session, tick, assert `datasource == 'otel_only'` and a stub row exists; then ship that session's JSONL through the real ingest path; tick again; assert **the same `session_id`** now has full `info`, `status` no longer `otel_only`, `datasource == 'both'`, and nothing from the OTel side was discarded. Verify the ingest upsert overwrites `producer_id` and `status` rather than skipping an existing row — if it does not, that is a second small change and it belongs here, not in the API phase.

**Done when:** a user+timeframe query of the kind DASH §8 specifies returns an OTel-only session; ticking twice is a no-op; the expandability test passes.

## PM.3 — `by_query_source` rollup (answers "what are background calls costing me?")

**Edit:** `src/server/worker/otel_trueup.py`, `config/otel/claude_code.yaml`

Today `query_source` is on every ledger record (`otel_trueup.py:216`) but nothing aggregates it, and the metrics side is worse: `config/otel/claude_code.yaml:47-51` selects `token.usage` by `type` only, so background spend is summed into the session total and cannot be separated. There is no way to answer the question.

- [ ] `_by_query_source(records) -> dict` in `otel_trueup.py`; add to `build_cost_block` output:

```python
"by_query_source": {
    source: {"requests": n, "tokens": {...}, "cost_usd_micros": ..., "cost_est_usd_micros": ...}
}
```

- [ ] Key ordering sorted, values formatted through `_fmt` — the block must stay diffable so idempotency is observable (the reason `TOKEN_MEASURES` is a fixed tuple).
- [ ] Interactive requests carry `query_source: null`; use a stable literal key (`"interactive"`) rather than a JSON null key, and say so in a comment.
- [ ] `jsonl_only` records always have `query_source: None` (`otel_trueup.py:246`) — correct, JSONL has no such concept. The rollup must therefore be **absent, not empty**, in `jsonl_only` mode, so the UI can render `unavailable` rather than "no background spend" (MODES §5). Those are different claims.
- [ ] Add query_source-split measures to the mapping YAML. The label is already on `token.usage` and `cost.usage` datapoints, verified against the recorded dump, so this is a config change with no code:

```yaml
  tokens_input_bg:  {metric: "claude_code.token.usage", where: {type: "input", query_source: "!interactive"}}
```

  If the mapping's `where` cannot express negation, add the two or three concrete `query_source` values observed in the dump instead and leave a `TODO(dashboard)` — a selector language is not worth growing for this.

**Keep the two side by side; do not reconcile them.** `build_cost_block` already keeps `measures` and `requests` apart on the principle that they are two independent views and their disagreement is the signal. The same applies here: the metrics split works in a metrics-only fleet, the ledger split is exact, and averaging them would destroy both properties.

**Done when:** the reconciliation session's background calls appear as their own line with a plausible share of session cost, from both the ledger and the metrics path.

## PM.4 — Surface `active_time_s`

**Edit:** `src/server/worker/otel_trueup.py`

- [ ] `active_time_s` is already mapped (`config/otel/claude_code.yaml:52`) and already folded into `measures` — it is simply never promoted anywhere a reader looks. Expose it in the block's top level beside `tokens`.

Small, but it is the only exact active-time figure available and the only one that exists at all in `otel_only`. DASH §3.1's fallback (Σ turn durations) needs JSONL.

**Done when:** the fixture session's block reports a non-zero `active_time_s`.

## PM.5 — JSONL lag detection

**Edit:** `src/server/worker/otel_trueup.py`, `config/queries/otel.yaml`

MODES §7: the two sources disagree routinely, and the disagreement is a state to report rather than a bug to hide.

- [ ] `jsonl_lag_s = max(otel event_ts) - sessions.last_event_at`, in whole seconds, computed in `_fold_session` from values PM.2's `otel_session_bounds` already fetches.
- [ ] Written **only** when `sources.otel_only > 0` — OTel has seen requests JSONL has not delivered. Otherwise `None`.
- [ ] Clamp negatives to `None`. JSONL ahead of OTel is the normal steady state (JSONL is the live path, OTel is 1–2 min behind), and reporting it as negative lag would put a "still arriving" caption on every healthy session.
- [ ] Null it out on the tick a session's JSONL catches up, so the UI treatment clears by itself. This falls out of recomputing per tick — but assert it, because a stuck lag badge is exactly the kind of thing nobody notices.

**Done when:** an OTel-first fixture reports lag; after its JSONL lands, the next tick clears it to `None`.

## PM.6 — Precedence config → **moved to P2**

The parent plan put `dashboard.precedence.{tokens,cost}` here. **It should move to P2**, and this plan differs from PLAN deliberately on that point.

The reason: nothing in PM consumes it. The chain is resolved by the read path, `config/api.yaml` is created by P2, and config with no reader is config nobody validates — it drifts from the code it is supposed to govern before the first consumer exists. The chain's *contents* are already specified (MODES §3.3); only its file lands later.

- [ ] Update PLAN's PM.6 checkbox to point here, so the two plans do not disagree silently.

## PM.7 — Test matrix

**Edit:** `tests/server/worker/test_otel_trueup.py`, `tests/fixtures/otel/make_fixtures.py`

The existing battery tests four properties of the fold (its docstring). This phase adds a fifth: **mode is derived correctly and does not leak between deployments.** These are `requires_db` tests, same as the current file.

- [ ] Fixture per mode. `metrics_session.jsonl` + `logs_session.jsonl` + `jsonl_session.jsonl` already exist; the modes come from which subset is loaded, so this is a fixture *combination* problem rather than new recorded data. Extend `make_fixtures.py` only if a metrics-only-with-no-events case cannot be built from what is there.
- [ ] One test per mode: fold → assert `datasource`, `cost_effective_usd_micros`, presence/absence of `by_query_source`, and the stub row's existence.
- [ ] **Idempotency per mode** — re-tick, assert byte-identical block (PLAN §0.1). The three modes take different paths through `_join_requests`, so one shared idempotency test does not cover them.
- [ ] **Second-tick-is-a-no-op** — the PM.2 loop hazard.
- [ ] **Expandability** — the `otel_only → both` transition, PM.2.
- [ ] **JSONL fold does not clobber** — fold a session's JSONL after a tick, assert the three columns survive (ground rule 3).
- [ ] Lag set, then cleared (PM.5).

**Done when:** `pytest tests/server/worker/test_otel_trueup.py` green, and each of the three modes has a named test that fails if its mode is misderived.

---

## Sequencing

```
PM.0 ──► PM.1 ──► PM.2 ──┐
              └─► PM.3 ──┼──► PM.7
                  PM.4 ──┤
                  PM.5 ──┘   (PM.5 needs PM.2's bounds query)
```

- PM.0 first and alone — it changes what every later test sees.
- PM.1 before PM.2: the stub write and the column write happen in the same statement.
- PM.3 and PM.4 are independent of PM.2 and can go in parallel.
- PM.5 needs PM.2's `otel_session_bounds`.
- Suggested PRs: (PM.0+PM.1+PM.2), (PM.3+PM.4+PM.5), PM.7 folded into whichever PR introduces the behaviour it tests rather than landing as a lump at the end.

## Out of scope, recorded

- **The OTel logs firehose.** `tool_result`, `tool_decision`, `user_prompt` and friends would give `otel_only` much of the usage-patterns zone and parts of provenance with no collector. Dropped by v1's `accept: ["api_request"]` filter; needs a logs-normalization module first (OTEL §9 v2 seam, MODES §1.1). **Do not design against it.**
- **Provenance category K.** Specified in DASH §5.1, belongs to P4 with the rest of provenance. PM.3's `by_query_source` is the rollup K will read; that is the whole dependency.
- **Identity mapping.** v1 requires the uploader to be configured with the same user/org identifiers the OTel attributes carry (MODES §8). A mapping table is the v1.1 fix. What belongs in *this* phase is only that the stub row's `user_id` comes from the mapped `user_key`, so the two sides can meet at all.
- **Any UI.** The `unavailable` state, the mode ladder, the cost-basis captions — all P3, all reading the fields this phase writes.
