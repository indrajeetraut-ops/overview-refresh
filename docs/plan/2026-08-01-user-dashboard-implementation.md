# User Dashboard — Implementation Plan

**Goal:** Ship the v1 single-user dashboard: pricing, fold rollups (story/flags/practices/provenance), a read-only API module, and a framework-free SPA implementing the storytelling UX.

**Spec set (read the referenced section before starting any task — do not improvise against the spec):**
- UX/IA & component inventory: `docs/design/dashboard-ux-blueprint.md` (**BLUE**)
- Metrics, data mapping, provenance algorithm, insights 1–13, APIs: `docs/design/user-dashboard-design.md` (**DASH**)
- Story anatomy, beats, tone lexicon: `docs/design/session-storytelling.md` (**STORY**)
- Practice signals, insights 14–25, scorecard: `docs/design/best-practice-insights.md` (**PRACT**)
- **Datasource modes, capability matrix, cost precedence, the `unavailable` state: `docs/design/dashboard-datasource-modes.md` (MODES)**
- Existing fold contract & conventions: `docs/plan/2026-07-27-worker-session-analytics.md`, `src/server/worker/fold_api.py`
- OTel intake as shipped: `docs/design/2026-08-04-otel-intake-design.md`, `src/server/worker/otel_trueup.py`

**Status:** Planned, revised 2026-08-06 for the hybrid intake. No DB migration exists yet — schema changes are SQLAlchemy-model + fold-dataclass edits only, same as the worker plan.

> **What the 2026-08-06 revision changed.** This plan was written against a JSONL-only world. Three things an implementer must not carry over from the previous version:
>
> 1. **P0's "nothing in the fold writes dollars" is void.** The true-up worker already writes three dollar figures per session, in every datasource mode. DASH §1.3 is rewritten around it; the cost block is the cost.
> 2. **A new phase, PM, comes before P2** — mode-awareness is a data and contract change, and retrofitting it into an API that assumes JSONL means rewriting every endpoint.
> 3. **Every UI task ships five states, not four** (BLUE §6). The fifth is `unavailable`, and confusing it with cold-start is a bug with a user-visible lie in it.

---

## 0. Ground rules for implementers (binding)

1. **Idempotency is the fold contract.** Every new rollup must be a pure function of the full deduped blob (`fold_api.py` docstrings). Re-folding the same session twice must byte-identically reproduce `session_info`. Add this assertion to every new fold test.
2. **No content in `session_info`.** Token counts, sizes, paths, names, timings only. Specifically: Bash command text is matched at fold time against `dashboard.verifiers` regexes and only booleans/counts persist (PRACT §2). User-message text never leaves the blob.
3. **Tone lexicon is a hard rule.** No user-facing string may contain: waste/wasted/wasteful, inefficient, overkill, grade/score language (STORY §2 table). Copy the lexicon into `src/server/api/static/js/lexicon.md` as a review checklist. Violation = bug.
4. **Conventions (CLAUDE.md):** structured JSON logging per module; pytest for everything, fix failures immediately; CLI entry points named `cli_{function}.py`, never bare `cli.py`; leave `TODO(dashboard): …` markers in source for anything deferred and remove them when done; update `requirements.txt` immediately on any new dependency; no AI-tool references in commits or code.
5. **Vendor-aware code stays in extractors.** Anything reading `payload.raw` shapes belongs in `src/server/worker/fold/extract_claude_code.py` (or a new sibling), never in vendor-neutral driver/metrics code (worker plan §2).
6. **Config over constants.** Every threshold named in the specs (`dashboard.flags.*`, `dashboard.verifiers`, `insights.min_sessions`, `story.min_sessions_for_band`, `fold.long_turn_threshold_s`) lives in YAML under `config/`, loaded through the module's config model.
7. **Test data:** recorded fixtures in `tests/fixtures/normalizer/recorded/` (incl. the 27-task `session-turns` set) fed through the real normalizer → `EconFold`, per the worker plan. New fixture needs are called out per phase.

---

## Phase P0 — Pricing engine (unblocks every $ figure)  ✅ **DONE 2026-08-05**

**New:** `config/pricing.yaml`, `src/common/pricing.py`, `tests/common/test_pricing.py`

- [x] `config/pricing.yaml`: `models: {<model_id>: {input, output, cache_read, cache_write_5m, cache_write_1h}}` in USD per MTok, plus `effective_from` date per price block (list of dated blocks per model; latest block ≤ query date wins). Include entries for the model ids present in the fixtures; unknown model → price as `null`, never crash.
- [x] `src/common/pricing.py`: `price_tokens(model, {input, output, cache_read, cache_write}|cache_write_detail, at: date) -> Decimal | None`. Pure, no I/O beyond config load; loader follows `src/collector/normalizer/config.py` layering (defaults → YAML → env).
- [x] ~~Pricing is **query-time only** (DASH §1.3): nothing in the fold writes dollars~~ — **void as of 2026-08-06.** The true-up worker writes `cost_usd_micros`, `cost_est_usd_micros` and `cost_effective_usd_micros` into `session_info.otel` for sessions in *all three* datasource modes, and `price_tokens` is what it prices with. Query-time pricing survives only as `GET /v1/pricing/models`. `session_info.cost_usd` stays untouched and is **not** the dashboard's cost — see PM.1.
- [x] Tests: known-price math, dated-block selection, unknown-model → None, cache TTL split pricing.

**Done when:** `pytest tests/common/test_pricing.py` green; a scratch script can price one fixture session end-to-end.

## Phase P1 — Fold additions (scalars, rollups, story, practices)

**Edit:** `src/server/storage/schema/session_info.py`, `src/server/worker/fold_api.py`, `src/server/worker/fold/{driver,metrics,extract_claude_code}.py`, `config/worker.yaml`
**New:** `src/server/worker/fold/story.py`, `src/server/worker/fold/practices.py`

> **Implementation-ready detail: [`2026-08-06-dashboard-fold-implementation.md`](2026-08-06-dashboard-fold-implementation.md).** Tasks F0–F7. **Start with F0** — it fixes two existing defects, one of which (`rollup_subagents` resetting fields it does not pass) would zero P1.1's new scalars on any session with subagents.

- [ ] **P1.0 Fix two fold defects first** (F0): `session_info.files` is never computed by `_recompute` despite being marked ✅ in DASH §3.1/§4.2; and `rollup_subagents` constructs a `SessionInfo` omitting `files`/`pr_list`/`cost_usd`, which `persist_session_info` then writes as defaults.
- [ ] **P1.1 Scalars** (DASH §7): add `tokens_total`, `compaction_count`, `subagent_count`, `error_count` columns (+ dataclass mirrors). `tokens_total` = existing `info.tokens_total` promoted (keep both in sync; single source in metrics.py).
- [ ] **P1.2 Event rollups** (DASH §7 table): `info.compactions`, `info.errors`, `info.cache` (hit ratio, TTL-split writes, decay events from inter-turn gap > TTL), `info.repo` (`{cwd, git_branch, repo_key}`; `repo_key` = last path segment of cwd until normalizer `repo_id` lands), `info.title` (from `ai-title` passthrough — raw shape goes in the extractor), `info.mcp_servers`, `tools[name].bytes_read_total/duration_ms_total`, `skills[i].args_gist` (≤80 chars of Skill args).
- [ ] **P1.3 Story rollup** (STORY §6) in `story.py`: `info.story = {curve: [[t_rel_s, cum_tokens, ctx_tokens] × ≤64 downsampled], shape, anchors: {superlatives: []}}`. Shape classification per STORY §5 (geometry heuristics; config thresholds under `worker.story.*`). `anchors.median_ratio` is **not** stored (computed at query time from history — API phase).
- [ ] **P1.4 Practices vector** (PRACT §4.1) in `practices.py`: `info.practices` = verifier runs/pass-rate (Bash `input.command` vs `dashboard.verifiers` regexes — counts only, rule 0.2), `exitplan_count` (tool_call name `ExitPlanMode`), `first_prompt_chars`, `at_refs`, `delegation_ratio`, `interrupts` (sentinel already pinned in turns.py — reuse), `manual/auto_compactions` (`compaction.trigger`), `subagent_research`, `review_subagent` (spawn `agent_type` ∈ `dashboard.review_agent_types` after first file_change). **Defer** (leave `TODO(dashboard)` + null fields): `plan_mode` spans, `/clear` detection, rewind branch points — each needs empirical pinning (PRACT §5) with a recorded fixture before shipping.
- [ ] **P1.5 Flag derivation:** flags are *derived at query time* from scalars + config thresholds — do **not** store badge booleans (thresholds must be tunable without refold).
- [ ] Tests per rollup against recorded fixtures + the idempotency assertion (rule 0.1); extend the `session_info` shape-contract golden if present, create it if not (worker plan §7 open item).

**Done when:** `pytest tests/server/worker` green; fold of `session-turns` fixture shows populated `info.story`, `info.practices`, new scalars.

## Phase PM — Datasource modes (new 2026-08-06; blocks P2)

**Edit:** `src/server/storage/schema/{session_info,sessions}.py`, `src/server/worker/otel_trueup.py`, `config/otel/claude_code.yaml`, `config/api.yaml`
**Spec:** MODES throughout. Read §2, §3 and §8 before starting.

This is data and contract work, not UI. It comes before P2 because an API built on the assumption that JSONL always exists has to be rewritten rather than extended.

> **Implementation-ready detail: [`2026-08-06-datasource-modes-implementation.md`](2026-08-06-datasource-modes-implementation.md).** The checkboxes below are the sequencing summary; that doc carries the column definitions, the stub-row contract, the loop hazard in `touched_sessions`, and the per-mode test matrix. Work from it, not from this list.

- [ ] **PM.1 Promoted columns** (MODES §3.2): `cost_effective_usd_micros`, `datasource` (`both|jsonl_only|otel_only`), `jsonl_lag_s` on `session_info`, written by the true-up in the same transaction as the block. `cost_effective_usd_micros` outside the `otel` key is deliberate — it holds JSONL-only sessions' estimated cost too, and a cost inside a column named `otel` reads as a bug to everyone who meets it. These are the sortable cost/mode keys the sessions list needs (DASH §3.4 forbids JSONB sorting).
- [ ] **PM.2 Stub `sessions` rows** (MODES §8): the true-up creates a `sessions` row for an OTel-only session — `status='otel_only'`, `started_at` from earliest `event_ts`, `agent` from vendor, `org_id` from the token, empty `signal_blob`. **Without this the entire zero-install tier is invisible to every endpoint, including the cost figures it does have**, because `session_info` carries no `user_id`/`started_at`/`agent` and every query filters on them. Test that must exist: ship OTel for a session, then ship its JSONL, and assert the same `session_id` fills in place — `otel_only → both` with no migration and nothing discarded. That expandability is what the tier is sold on.
- [ ] **PM.3 `by_query_source` rollup** (DASH §7.1): `{source: {requests, tokens, cost_usd_micros, cost_est_usd_micros}}` in the cost block, from the ledger. Plus query_source-split measures in `config/otel/claude_code.yaml` — the label is already on `token.usage`/`cost.usage` datapoints, so a metrics-only fleet gets the split too. Keep the two side by side; do not reconcile them (same rule `build_cost_block` already follows for `measures` vs `requests`).
- [ ] **PM.4 `active_time_s` surfaced** — already mapped, never read. It is the exact counter behind DASH §3.1's active-time tile, and the only version of that number that works in `otel_only`.
- [ ] **PM.5 Lag detection** (MODES §7): `jsonl_lag_s` = newest `otel_api_requests.event_ts` − `sessions.last_event_at`, null when not applicable. Nothing new in the pipeline; `sources.otel_only > 0` already says OTel saw requests JSONL has not delivered.
- [ ] ~~**PM.6 Precedence config**~~ — **moved to P2.** Nothing in PM consumes the chain: it is resolved by the read path and `config/api.yaml` is created by P2, so landing it here would leave config with no reader to validate it. Contents are still specified in MODES §3.3; only the file moves. Rationale in the PM plan's PM.6.
- [ ] **PM.0 Bump `BLOCK_VERSION` to 3** — first, alone. PM.3 changes the block's shape, and the `touched_sessions` version arm is what re-folds sessions holding the old one; without it they strand silently.
- [ ] Tests: a fixture per mode (both / jsonl_only / otel_only) folded end to end; idempotency assertion on each (rule 0.1); the PM.2 expandability test above.

**Done when:** all three fixture modes produce a coherent `session_info` row; an `otel_only` session is reachable by a timeframe+user query; `by_query_source` splits the reconciliation session's background calls.

## Phase P2 — API module

**New:** `src/server/api/` — `app.py` (the only FastAPI import, mirroring `src/server/uploader/app.py`), `config.py` (`config/api.yaml`), `queries.py` (SQL via `config/queries/dashboard.yaml`, following `QueryCatalog` pattern), `service.py` (framework-free), `cli_serve_api.py`, `errors.py`; `tests/server/api/`

Endpoints (DASH §8; JSON field names exactly as specced there and in BLUE §8 component bindings):

> **Implementation-ready detail: [`2026-08-06-dashboard-api-implementation.md`](2026-08-06-dashboard-api-implementation.md).** Tasks A0–A9, with the response envelope built before any endpoint and the read-path story computation (beats, acts, narrator, personal band) isolated as its own long-pole task. Work from that doc; the checkboxes below are the sequencing summary. PM.6's precedence config lands there as A2.

**Mode contract (DASH §8.1) applies to every endpoint below — implement it once, in `service.py`, not per route:** each response carries mode (`datasource`+`vendor` per session; `modes_present`+`sessions_by_mode` on aggregates); each number reports the source that produced it and each dollar its `cost_basis`/`cost_complete`; an uncomputable block returns `null` plus `{unavailable: {block, requires}}` — **never `0`, never `[]`**. Zero is a measurement; null-with-a-reason is the truth, and the UI cannot pick between the `unavailable` and cold-start states without it.

- [ ] `GET /v1/users/{id}/overview?from&to` — aggregates over `session_info` + the cost block; includes provenance mix only when present (else `provenance: null` → cold-start state) and only in JSONL modes (else `unavailable`). Adds `by_query_source` and the lag rollup.
- [ ] `GET /v1/users/{id}/sessions?from&to&sort&flags&vendor&limit&offset` — scalar-column sorts only (cost sorts on the PM.1 column); badge filters from query-time flag derivation (P1.5); each row carries `glyph: info.story.curve` (downsampled), `shape`, `title`, `repo_key`, badge list. **`otel_only` rows are reduced rows**: cost, model, time, vendor, no glyph/title/badges, and no S4 link.
- [ ] `GET /v1/sessions/{id}` — session_info panel payload; cost from `cost_effective_usd_micros`, never re-derived from `models`.
- [ ] `GET /v1/sessions/{id}/timeline` — **404 in `otel_only`** (no blob to condense; the list already withholds the link, this is for anyone who guesses the URL).
- [ ] `GET /v1/sessions/{id}/timeline` — condensed events (collapse same-tool runs, cap ~2k entries, `?from_ts&to_ts` zoom) **plus** `story: {narrator, acts, beats}` computed read-path in `service.py` per STORY §4 (beat detection) and §3.2 (narrator templates — deterministic string templates, no LLM). Personal band: p25/p75 cumulative-vs-elapsed envelope from the user's trailing sessions (`story.min_sessions_for_band`, default 5; below → `band: null`).
- [ ] `GET /v1/users/{id}/insights?from&to` — Phase-1 insight set computed on read: DASH §6 #1,3,4,5,6,7,8 + **#26,27 (OTel modes)** + PRACT §3 #14,15,18,22 (only those whose signals exist after P1; each card: `{insight_id, kind, mode, observation, evidence, action_text, est_tokens, est_usd, sessions[]}` — copy strings through the tone lexicon). Filter the catalog by the caller's mode; an insight uncomputable in that mode is **omitted, never rendered as "0 found"** — absence of signal is not absence of the phenomenon.
- [ ] `POST /v1/sessions/{id}/feedback` — `{beat_ref, verdict}` into a new `feedback` table (small model + dataclass; same no-migration stance).
- [ ] `GET /v1/pricing/models` — active price table.
- [ ] Static mount: `app.mount("/", StaticFiles(...))` serving `src/server/api/static/` (SPA, P3).
- [ ] Structured JSON logs (`api.request` logger): route, duration, session/user ids — never payload content.

**Done when:** `pytest tests/server/api` green (endpoint tests over a seeded in-memory/em DB with folded fixture sessions); `python -m server.api.cli_serve_api` serves overview JSON for a fixture user.

## Phase P3 — SPA shell + Sessions + Story screen

**New:** `src/server/api/static/` — `index.html`, `css/tokens.css` (lift the mockup's custom-property system verbatim — palette, both theme scopes, type roles), `css/app.css`, `js/{router,api,components/*,screens/*}.mjs` — vanilla ES modules, **no framework, no build step, no external requests** (BLUE §9).

> **Implementation-ready detail: [`2026-08-06-dashboard-ui-implementation.md`](2026-08-06-dashboard-ui-implementation.md).** Tasks U0–U8, with the substrate (tokens, component idiom, five-state primitive, chart module) separated from the screens that assemble it. Work from that doc; the checkboxes below are the sequencing summary.

- [ ] **P3.0 Mode plumbing (first):** the router and each screen read `datasource` / `modes_present` off the response and select components from BLUE §8's Modes column. An `otel_only` session has no S4 route at all — a `/session/{id}` request for one resolves to a `SourceNote`, not a 404. Getting this wrong is not a layout bug; it puts a sentence on screen that is false and never becomes true.
- [ ] **P3.1 Shell + substrate** (U0–U3): design tokens, hash router, the component idiom, the five-state primitive (including `unavailable`, contract in MODES §5), `SourceNote` and `CostBasisCaption`, the hand-rolled SVG chart module.
- [ ] **P3.2 S2 Sessions** (U4): `SessionRow` + `Glyph`, sort/filter controls, badges, **the reduced `otel_only` row**, the lag indicator. Row → S4.
- [ ] **P3.3 S4 Story** (U5): the mockup exactly — header, narrator line, `BurnChart` with band and beat markers, `ActsList` with "mark as expected", `Receipt` + `OriginStack` mini + `PerUnitLine`, `MetricsTabs`. Renders for lagging sessions too.
- [ ] **P3.4 S1 Overview** (U6): Z1 tiles, Z2 `ModelDonut`+`CostBars`+`QuerySourceSplit`, Z3 top lists, Z4 flagged rows, Z5 scorecard (hidden below min sample **and** for non-claude_code vendors), Z6 teasers. In `otel_only` the screen ends after Z2.
- [ ] **P3.5 S3 Insights** (U7) — **was missing from this plan.** The route was listed and the teasers built, but nothing built the screen they link to. Ranked cards, filter row, dismiss/mark-as-expected; BLUE §5.
- [ ] **P3.6 Responsive, accessibility, lexicon gate** (U8): breakpoints, keyboard reachability of beat markers, `aria-label` on every chart, `prefers-reduced-motion`, and the STORY §2 review of `copy.mjs` as a merge gate.

**Done when:** manual walkthrough against a seeded fixture DB shows all four routes in both themes **in all three datasource modes**; no console errors; no network requests beyond `/v1/*`.

## Phase P4 — Provenance (gate first, then ship)

- [ ] **P4.1 Offline validation gate (do this before any provenance product code):** standalone script `utils/provenance_probe.py` (+ shell wrapper per CLAUDE.md) running the DASH §5.2 delta-attribution against `out.json` + recorded fixtures. Output: per-session category table + residual R. **Gate: p50 R ≤ 10%** (DASH §5.4), evaluated **against OTel truth wherever the fixture has both sources** — the 2026-08-02 reconciliation session does. A residual computed against vendor truth is evidence; one computed against JSONL alone is arithmetic, since both sides come from the same file. Report both with `residual_basis` when both exist. If the gate fails, stop and report which category leaks — do not proceed to P4.2.
- [ ] **P4.2 Fold integration:** `src/server/worker/fold/provenance.py` → `info.provenance` per DASH §5 output shape (contribution + carry + `files_top` + `mcp_by_server` + `calib_ratio` + `residual_pct` + `residual_basis`). Cross-session baselines (categories B/C) are query-time, not fold-time.
- [ ] **P4.2b Category K** (DASH §5.1): background utility calls from `otel_api_requests.query_source`, exact, OTel modes only. Confirm K carries **zero carry** before the carry figures ship (DASH §10.6) — these calls are not turn-scoped and never persist into context, so applying the §5.3 carry model would overstate K by the session's turn count. Interrupted requests are the mirror case: include them, flagged, or their unmatched output distorts category E.
- [ ] **P4.3 Surface it:** overview Z2 mix stack goes live; S4 receipt stack switches from coarse buckets to true origins; per-unit line gains "re-read content"; insights #1 (re-reads), #2 (unused MCP), #6 (verbose output), #10 (memory size) upgrade to provenance-backed dollar estimates; residual always displayed in the S4 [Metrics] tab as "unattributed".

**Done when:** probe report checked into `docs/superpowers/reports/` (same convention as storage/msging reports); gate met; fixture fold shows `info.provenance` with residual ≤ target.

## Phase P5 — Insights maturation + correlations

- [ ] Persist insights (`insights` table: DASH §6 record shape + `status`) replacing computed-on-read; dismiss/track wired to the feedback endpoint pattern.
- [ ] Cross-session baselines → insights #2, #9, #10, #24, #25; correlation cards (PRACT §4.3: adherence × cost, adherence × churn, context-size × churn regression, delegation payoff) behind `insights.min_sessions`.
- [ ] 🔬 pinning tasks as data allows (PRACT §5): plan-mode spans, `/clear` wrapper, rewind DAG signature — each lands with a recorded fixture + unlocks its deferred insights (#16/17/20/21/13).

## Phase P6 — Deferred / explicitly out of scope for this plan

Real DB migration (Alembic) when the DB lands; git correlator (`pr_list`, insight #12 dark waste, receipt "Produced: PR merged"); M-04 task classification (insight #11, task-cluster scoping); auth/multi-user; org dashboard; any enforcement/governance features.

---

## Sequencing & hand-off notes

```
P0 ──┐
P1 ──┼──► PM ──► P2 ──► P3      (demoable dashboard, no provenance)
     │
     └──► P4.1 gate ──► P4.2 ──► P4.3
                        P5 after P2+P1; P4 independent of P3
```

- P0 and P1 are independent; PM needs P0 (it prices) and is independent of P1; P2 needs all three. P3 needs P2. P4.1 can start immediately (it needs only `out.json` + fixtures).
- **PM is not optional and not deferrable to "after the dashboard works."** Two of its items (PM.1 columns, PM.2 stub rows) are the difference between the zero-install tier being visible and being absent, and both are cheaper to build before the API than to retrofit into it.
- Suggested hand-off granularity: one checkbox = one work item; one phase = one PR (P3 may split per screen: P3.1+P3.2, P3.3, P3.4).
- Each PR: tests for the touched module green (`pytest tests/server/worker`, `tests/server/api`, `tests/common`), shape-contract golden updated deliberately (never regenerated blindly), and a check of every user-facing string against the STORY §2 lexicon — **including the `unavailable` copy**, which is the first thing a new user reads and is aimed at someone who has done nothing wrong.
- **Deployment requirement to carry into the install docs** (MODES §8): the JSONL uploader must be configured with the same user and org identifiers the OTel attributes carry. There is no mapping between `sessions.user_id` and OTel's `user_key`, so a mismatch silently splits every developer in a hybrid org in two, with no error anywhere. A mapping table is the v1.1 fix.
- Out of scope, and not to be designed against (MODES §1.1): the OTel logs firehose. `tool_result`, `tool_decision`, `user_prompt` and friends would give `otel_only` much of Z3 and parts of provenance with no collector, but they are dropped by v1's `accept: ["api_request"]` filter and need a logs-normalization module first — the otel design's §9 v2 seam.
