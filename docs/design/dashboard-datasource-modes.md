# Dashboard Datasource Modes — what each deployment can show

*2026-08-06. The hub doc for the fifth dimension the v1 dashboard set was written without: **which signal sources an org actually deploys.** The four dashboard docs ([`dashboard-ux-blueprint.md`](dashboard-ux-blueprint.md), [`user-dashboard-design.md`](user-dashboard-design.md), [`session-storytelling.md`](session-storytelling.md), [`best-practice-insights.md`](best-practice-insights.md)) were written 2026-07-31 → 08-01, three days before the OTel intake froze ([`2026-08-04-otel-intake-design.md`](2026-08-04-otel-intake-design.md)). They assume JSONL is always present. It is not.*

*Every widget-level consequence lives in the source docs; this doc owns the mode model, the capability matrix, the cost precedence chain, and the UI contract for absence. Cite it, don't restate it.*

---

## 1. Mode is a deployment property, not a maturity state

The existing design treats missing data as *temporary*: "the fold hasn't run yet", "below the minimum sample". Datasource mode is different — it is a **standing fact about how an org is set up**, and for a given org it does not resolve on its own.

Three modes, from the two independent intake paths:

| Mode | Deployment | Posture |
| :---- | :---- | :---- |
| **both** | Talium collector + OTel telemetry enabled | The complete product. Spend truth and session fidelity. |
| **jsonl_only** | Collector installed, telemetry declined or not yet enabled | Full session interiors; spend is priced, not vendor-reported; background calls invisible. |
| **otel_only** | Managed-settings enrollment, no collector — the zero-install tier | Spend truth at request granularity; **no session interiors at all**. |

`otel_only` is not a degraded install to be apologised for. It is the deliberate 15-minute-trial tier (otel design D-OT1) and the top of the funnel. What it *cannot* show is the reason to climb.

**The ladder is the product story.** Each mode a user is not in is a concrete "connect this, see that" — not an upsell banner, an accurate statement of what a signal source would reveal. §5 governs how that is said.

### 1.1 The named v2 rung

The OTel **logs** signal carries far more than v1 accepts. The recorded dump's `tool_result`, `tool_decision`, `user_prompt`, `assistant_response`, `hook_execution_*` and `plugin_loaded` events between them hold tool names, durations, success/error, input and result byte sizes, prompt lengths, edit accept/reject decisions, and the hooks/plugins inventory. All are count-and-dropped today by `accept: ["api_request"]` (otel design D-OT2).

Widening that filter would give `otel_only` most of §3's Z3, the byte sizes behind provenance F1/F3/F4, and PRACT's P3 and P9 signals **without a collector** — but it needs a logs-normalization module, which is the otel design's §9 v2 seam and is explicitly **out of scope here**.

Consequence for this doc: the `otel_only` column of §2 describes v1's accept list. It is a floor that rises when the seam lands, not a permanent ceiling. Do not design UI against rung-2 capability, and do not tell users it exists.

## 2. Capability matrix

The binding table. A component's mode support belongs in BLUE §8's inventory; this is the source it derives from.

| Surface | both | jsonl_only | otel_only |
| :---- | :----: | :----: | :----: |
| **Z0 active days, longest streak, heatmap, peak hour, favorite model** | ✅ | ✅ | **✅** ⁶ |
| Z1 tokens, cost, session count | ✅ | ✅ ¹ | ✅ |
| Z1 turns, long turns, files, repos, sub-agents | ✅ | ✅ | ❌ |
| Z1 active time | ✅ | 🔶 ² | ✅ |
| Z2 model split (tokens / $) | ✅ | ✅ ¹ | ✅ |
| Z2 spend by `query_source` (background calls) | ✅ | ❌ | ✅ |
| Z2 provenance mix | ✅ | ✅ ³ | ❌ |
| Z3 top tools · skills · MCP servers · repos | ✅ | ✅ | ❌ |
| Z4 flagged sessions + badges | ✅ | ✅ | ❌ |
| Z5 practice scorecard | ✅ ⁴ | ✅ ⁴ | ❌ |
| Z6 / S3 insights | ✅ | most | cost-shaped only |
| S2 row: glyph, shape, title, repo | ✅ | ✅ | ❌ |
| S2 row: cost, tokens, model | ✅ | ✅ ¹ | ✅ |
| **S4 Session Story (entire screen)** | ✅ | ✅ | ❌ |
| Interrupted requests | ✅ | ✅ | ❌ ⁵ |

¹ dollars from the price table, `cost_basis: estimated` — see §3.2.
² wall-clock and Σ turn durations only; the exact `active_time.total` counter is an OTel metric.
³ complete for observed requests; the K category (§4) is structurally absent.
⁴ Claude Code only — see §6.
⁵ OTel fires on request completion; an interrupted request appears in neither signal when JSONL is absent.
⁶ **Z0 is the one zone that renders in full in every mode** — added 2026-08-07 (FEAS, the rhythm feasibility note). Peak hour degrades through a precedence fallback in `otel_only` (session start times, one point per session, rather than per-turn) but that is a captioned source substitution, not the `unavailable` shape this section otherwise governs (§5 rule 1 still applies to the caption itself, naming the source not the user). Every date/hour Z0 buckets is UTC in every mode, deliberately — there is no user timezone anywhere in the system (FEAS §5); see the `TODO(rhythm-tz)` markers at each bucketing site.

**Read the S4 row again.** In `otel_only` the storytelling layer — the axis the whole design turns on — does not render. That is an architectural fact, not an empty state, and BLUE §1 must say so before an implementer discovers it in P3.

**Z0 is the counter-example worth stating plainly.** Every other zone in this matrix either needs JSONL or degrades to a caveat; Z0 needs neither — it is the argument that `otel_only` is a real tier and not a stub screen (FEAS §11).

## 3. Cost and tokens: one surface, configurable precedence

### 3.1 The cost block is the cost

DASH §1.3's "dollars are computed at query time from token counts × a versioned price table" no longer describes the system. The true-up worker (`src/server/worker/otel_trueup.py`) already writes three dollar figures per session, and its `touched_sessions` query has an arm for **JSONL-only sessions specifically**, so every session in every mode gets a cost block.

That gives one unified surface. **The dashboard reads cost from the block. It never re-derives dollars from `session_info.models`.**

| Field | Meaning |
| :---- | :---- |
| `cost_usd_micros` | vendor-reported only |
| `cost_est_usd_micros` | priced from tokens only |
| `cost_effective_usd_micros` | vendor where present, estimate otherwise — **what the UI shows** |
| `cost_basis` counters | `vendor` / `estimated` / `unknown` per request |
| `cost_complete` | false while any request has no cost at all |
| `calibration` | per-model estimate-vs-vendor drift — **internal, never rendered** |

Query-time pricing survives only as the re-price-history story, and it is no longer free: a changed `pricing_fingerprint` (`Pricer.fingerprint`: the catalog version plus any history hash) triggers a refold rather than a recomputation on read.

### 3.2 Promoted columns

`session_info.otel` is a JSONB block, and DASH §3.4 forbids JSONB sorting on the sessions list. Three values are promoted to first-class `session_info` columns:

```python
cost_effective_usd_micros: Mapped[int | None]   # the dashboard's cost, sortable
datasource:                Mapped[str]          # both | jsonl_only | otel_only
jsonl_lag_s:               Mapped[int | None]   # §7; null when not applicable
```

`cost_effective_usd_micros` living outside the `otel` key also fixes a naming problem: a column called `otel` holding a JSONL-only session's estimated cost reads as a bug to everyone who meets it.

`datasource` is derivable from the block's `sources` counters plus the presence of `info`, but as a scalar it is filterable and sortable — which is what a mode-aware sessions list needs.

### 3.3 Precedence is configuration

Token and cost sources are ordered chains, resolved per session, **first available wins** — not baked into query code:

```yaml
dashboard:
  precedence:
    tokens: [otel_measures, otel_ledger, jsonl_models]
    cost:   [otel_vendor, estimated]
```

Every response that carries a number resolved this way also carries **which source produced it**. A figure whose provenance the UI cannot name is a figure the UI cannot caption honestly.

## 4. Provenance under mixed sources

Two changes to DASH §5.

**New origin category K — background utility calls.** `prompt_suggestion`, `generate_session_title` and their siblings are ~5.7% of session cost, carry `query_source` on both the ledger and the metric datapoints, and today have no home in the §5.1 table — they would either vanish or silently inflate the residual R. K is exact from `otel_api_requests.query_source`, and is structurally absent in `jsonl_only`.

Interrupted requests are the mirror case: JSONL-only, real input-side tokens, placeholder output. They belong in the decomposition, flagged, or they distort category E.

**The residual gate gets a real referee.** §5.2's identity `Σ categories + R = Σ(input + cache_write)` currently checks JSONL against itself — true by construction, which is exactly what makes it weak. Where OTel is present there is an independent measurement of the same quantity, so the §5.4 gate (p50 R ≤ 10%) should be evaluated against vendor truth. In `jsonl_only` the old self-check is all there is, and the gate result should say which one it ran.

## 5. The fifth UI state

BLUE §6 ships four states: loading, empty, cold-start, error. None fits mode-absence. Telling an `otel_only` org *"Provenance decomposition hasn't run for these sessions yet"* is a lie that never resolves.

**`unavailable` — not derivable from this data source.** Rules:

1. **Name the source, not the user.** "Session interiors come from the collector" — never "you haven't installed", never "missing", never "limited".
2. **Say what the source would show,** concretely and in this component's own terms. The enrollment path is the affordance; the sentence is the reason.
3. **It is not an error and not empty.** Distinct visual treatment from both — quiet, factual, no alarm colour, no icon of absence.
4. **Never on a per-session basis when it is an org-wide fact.** One statement at the screen or zone level beats the same sentence repeated down forty rows.
5. **The STORY §2 lexicon governs every word of it.** A tone violation here is worse than elsewhere: this is the copy a brand-new user reads first.

### 5.1 Coverage copy — use the measured numbers

`jsonl_only` needs a standing caveat, and it must not overstate the error. Three different quantities get conflated here, so the honest version:

| Claim | Measured |
| :---- | :---- |
| Token counts | **Exact.** Deduped JSONL matched OTel on all four token fields for 36/36 joined requests (2026-08-02). |
| Coverage | **~5.7% of spend not visible** — background utility calls only OTel sees. The total is a **floor**. |
| Dollars | Priced from the rate table; `calibrate()` measured worst error at **1 micro over 102 requests**. |

So the caveat is about *coverage*, not accuracy: tokens are exact for requests we can see, some requests aren't visible, dollars are priced rather than reported. The provenance residual's ≤10% gate is a **different measurement entirely** (how much context we can attribute to an origin) and must never be borrowed as a token-accuracy figure.

## 6. Vendor scoping

D-OT6 makes `vendor` a first-class dimension; all joins are `(vendor, session_id)` / `(vendor, request_id)`. The dashboard docs are implicitly Claude Code throughout. Every query, endpoint and rollup carries `vendor`.

The sharp edge is **PRACT**. That document is a Claude Code artifact: the canon is Anthropic's published guidance, and `/clear`, `/compact`, plan mode, `ExitPlanMode` and subagents are Claude Code concepts. Scoring a Gemini CLI session against it produces a confident wrong answer. The scorecard and insights 14–25 **hide** — not zero, not empty-state — for any other vendor.

Confidence differs too: only `claude_code` has passed a reconciliation spike. Other vendors' rows fold `cost_basis: estimated` by rule until their own spike lands, and the UI must not present them with equal authority.

## 7. Two clocks, and detecting the lag

OTel spend truth is ~1–2 min behind (otel design §6.2). JSONL is 10–30 s live, or **days** behind in batch-upload mode. So the two sources routinely disagree, and the disagreement is a *state to report*, not a bug to hide.

Lag is computable from what already exists:

- `otel.sources.otel_only > 0` — OTel saw requests JSONL has not delivered.
- newest `otel_api_requests.event_ts` minus `sessions.last_event_at` — the size of the gap, stored as `jsonl_lag_s`.

A session with lag shows its cost as settled (OTel is complete) and its **structure** as still arriving. The story, the receipt's Produced column and the turn timeline are the parts that grow; the burn chart's time axis stays JSONL-timed with OTel totals annotated, because a request ledger has no turn boundaries to plot against.

## 8. Onboarding consequences

**Stub sessions (B1).** `_write_cost_block` (`otel_trueup.py:497`) creates a `session_info` row for an OTel-only session but no `sessions` row — and `session_info` has no `user_id`, `started_at` or `agent`, which is what every DASH §8 endpoint filters on. The entire zero-install tier is therefore invisible to the dashboard, **including the cost figures it does have.**

The true-up writes a stub `sessions` row: `status = 'otel_only'`, `started_at` from earliest `event_ts`, `agent` from vendor, `org_id` from the token, `signal_blob` empty. It is a real row on the identity spine, and it is **expandable**: when an org later uploads JSONL for that session, the normal ingest path fills the same `session_id` in place and the session becomes `both` with no migration and no loss. That property is the promise the tier is sold on — nothing is thrown away by starting at the bottom of the ladder.

**Identity (B2).** JSONL carries `sessions.user_id` from what the uploader was told; OTel carries `user_key` from `user.account_uuid` / `user.email` / `user.id`. Nothing maps between them, so on a hybrid deployment one human is two users and `GET /v1/users/{id}/overview` is ambiguous about which id space it is in.

v1 resolves this **operationally, not in code**: the uploader must be configured with the same user and org identifiers the OTel attributes carry. This is a documented deployment requirement with a real failure mode — get it wrong and a hybrid org silently splits every developer in half. A mapping table is the v1.1 fix; recording the requirement here is what makes its absence deliberate.

## 9. Open questions

1. **Mode at what granularity?** Modelled per session above. An org mid-rollout has both kinds simultaneously, so screen-level copy (§5 rule 4) needs an org-level rollup — probably "modes present in this timeframe", computed at query time. Decide when the overview endpoint is written.
2. **Does `datasource` belong on `sessions` instead?** It is arguably identity, not intelligence. Kept on `session_info` because the true-up owns it and writes it on the same tick as the block.
3. **Stub-session timeframe fidelity.** `started_at` from earliest `event_ts` is the first *completed* request, not session start — an OTel-only session appears slightly shorter and later than it was. Acceptable; worth a note in the UI if it ever drives a duration figure.
