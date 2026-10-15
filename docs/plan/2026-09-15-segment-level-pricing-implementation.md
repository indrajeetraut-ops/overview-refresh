# Implementation plan — Track segment-level pricing

**Status (2026-09-15):** Phases 1, 3a, and 3b are **done** (see each phase's own status line).
Phase 2 was investigated and **deferred (no consumer)** per its own product gate. Phase 3c and
Phase 4 remain open, gated on the product/architecture decisions their sections describe.

**Date:** 2026-09-15
**Supersedes the open track in** [`docs/plan/2026-09-12-mvp-gap-closure-plan.md`](2026-09-12-mvp-gap-closure-plan.md):
"Track segment-level pricing" (its four unchecked boxes).
**Investigation basis:** direct read of `worker/segments/`, `worker/graph_fold.py`, and
`api/manager/aggregate.py` (2026-09-15). Sizing below corrects the gap-closure plan's estimates
where the code disagreed with them.

---

## What changed vs the gap-closure plan's sizing

The gap-closure plan framed this as "M for the `pricing_config` wire-through, L in aggregate to
consume it." Reading the code moved three of the four items:

- **The wire-through is XS, not M.** `build_segments`
  ([`beats.py:312`](../../src/server/worker/segments/beats.py:312)) *already* implements
  per-segment pricing in full — `cost_usd_micros`/`carry_usd_micros`, priced per request at the
  request's own model+date, all-or-nothing (`None` on any unpriced request), identical posture to
  `channel_cost_of`/`carry_of`. The only defect is `compute_segments`
  ([`__init__.py:26`](../../src/server/worker/segments/__init__.py:26)) passing a hardcoded
  `None`, even though its caller `info_of` ([`graph_fold.py:1039`](../../src/server/worker/graph_fold.py:1039))
  already holds `pricing`/`at`. Phase 1 is a two-signature thread-through plus tests.
- **F3 (Phase 2) is S–M, not L.** Segment dicts already carry `repo_key`
  ([`__init__.py:37`](../../src/server/worker/segments/__init__.py:37)); once they carry
  `cost_usd_micros` too, a per-repo split is a group-by. The open question is a product one (does a
  screen consume it), not an engineering one.
- **X3-phase2 (Phase 3) is bigger than "price the buckets," not smaller.** Only one of its four
  recoverable buckets is unlocked by segment pricing. The breakdown, verified against source:

  | Bucket | Current state | What "price it" actually needs |
  |---|---|---|
  | context | **already priced** (`carry.carry_usd_micros` + `channel_cost.recache`) | nothing |
  | rework | priced via `_segment_cost_by_beat` — but reads `$0` today because segments are unpriced | **free after Phase 1** |
  | resume overhead | `resume_cost.*.usd_micros` are `None` placeholders | thread pricing into `resume_cost_of`; **faithful** — `reload_tokens` are real cache-write tokens on real nodes |
  | model over-sizing | `model_fit.savings_usd_micros` | **free after Phase 1** (it multiplies segment `cost_usd_micros`); Phase 4 refines it |
  | read-then-discard | `est_tokens = bytes//4`, no dollar field | pricing a **token estimate** — conflicts with ground rule 3 (see Phase 3) |
  | retry burn | source field **not produced by GraphFold at all** | un-drop §4.C api_error + port an `errors_of` fold **first**, then price an estimate — its own mini-track |

  So "price all four" (the chosen scope) is really: two buckets fall out of Phase 1 for free, one
  (resume) is a clean pricing thread, and two (read-then-discard, retry) are blocked on a
  ground-rule-3 decision and — for retry — a missing fold extraction. Phase 3 is sequenced to bank
  the free/clean wins first and gate the estimate buckets behind an explicit product call.
- **X4-per-segment-model (Phase 4) stays L** and is genuinely the deepest item: segments have no
  per-segment model today, only a session-level dominant tier.

**Backfill: none.** Like `channel_cost`/`carry`, segment prices only apply to sessions folded from
now on; already-ingested sessions keep `null` segment costs. Per the 2026-09-15 scope call, we do
**not** build a re-derive backfill — the DB will be reloaded fresh (wipe + re-ingest), which
re-folds every session through the priced path. No `cli_backfill_*` for this track.

**Build order:** Phase 1 → Phase 2 → Phase 3 → Phase 4. Phase 1 is the shared unlock; 2/3/4 are
independent consumers and can be reordered or dropped by product priority once Phase 1 lands.

---

## Phase 1 — Wire `pricing`/`at` into `compute_segments` (the unlock)

**Problem.** `compute_segments` calls `build_segments(..., None)`
([`__init__.py:26`](../../src/server/worker/segments/__init__.py:26)), so every segment's
`cost_usd_micros`/`carry_usd_micros` is unconditionally `None`, even when `info_of` was given a
live `PricingConfig`. `build_segments` needs no change — it already prices correctly when handed a
config.

**Approach.** Thread the two values already present in `info_of` down one level:

- Add `pricing: PricingConfig | None = None, at: datetime | None = None` to `compute_segments`
  ([`__init__.py:20`](../../src/server/worker/segments/__init__.py:20)); pass them to
  `build_segments` at [`__init__.py:26`](../../src/server/worker/segments/__init__.py:26).
  Note `build_segments`'s param is named `pricing_config` and typed `pricing_mod.PricingConfig`;
  keep `compute_segments`'s name aligned with `graph_fold`'s (`pricing`/`at`) and map at the call.
- At the `info_of` call site ([`graph_fold.py:1039`](../../src/server/worker/graph_fold.py:1039))
  pass `pricing, at` — the exact pair already flowing to `channel_cost_of`/`carry_of` two lines up.
- Update the `compute_segments` docstring — its line "Pricing is not wired here" is now false.

**Posture (unchanged, inherited from `build_segments`).** Bare `GraphFold()` / `info_of(...)` with
no pricing stays unpriced (`None`), so no existing test's behavior changes just because
`config/pricing.yaml` exists on the CWD — same opt-in contract as the channel/carry fix. `at` is the
session's own `started_at`, never "today," so a later rate change never reprices old sessions.

**Files:**
- [`src/server/worker/segments/__init__.py`](../../src/server/worker/segments/__init__.py) —
  signature + call + docstring.
- [`src/server/worker/graph_fold.py`](../../src/server/worker/graph_fold.py) — one call-site edit at
  `info_of`.

**Verify.** New `tests/server/worker/test_segments_pricing.py` mirroring
[`test_graph_fold_pricing.py`](../../tests/server/worker/test_graph_fold_pricing.py): a crafted
family whose segments each contain priceable assistant nodes yields real `cost_usd_micros` under a
real `PricingConfig`, `None` without one, and `None` if any request in a segment is unpriced
(all-or-nothing). Re-run `test_segments_beats.py` unchanged (bare-call path must stay `None`).

**Size:** XS.

**Status: done (2026-09-15).** `compute_segments` now takes `pricing`/`at` and threads `pricing`
straight to `build_segments` (`at` is accepted for signature parity with `info_of`'s other
pricing call sites, but unused here -- `build_segments` already prices each request at its own
event timestamp, not the session `at`). `info_of`'s `segments_mod.compute_segments` call now
passes `pricing, at`. New `tests/server/worker/test_segments_pricing.py` covers unpriced/priced/
all-or-nothing; `test_segments_beats.py` reruns unchanged (bare `build_segments(..., None)` calls
untouched).

- [x] Thread `pricing`/`at` through `compute_segments` → `build_segments`
- [x] Pass them at the `info_of` call site
- [x] Pricing test + green existing segment/beats tests

---

## Phase 2 — F3: per-repo segment cost split

**Problem.** `repo_rollup_canonical` ([`aggregate.py:70`](../../src/server/api/manager/aggregate.py:70))
attributes a whole session's cost to its single primary `repo_id`. A mixed session that touched two
repos can't be split per repo. `TODO(F3-segment-split)`
([`aggregate.py:79`](../../src/server/api/manager/aggregate.py:79)) defers this "until a drilldown
needs it; no Phase 1 screen does."

**Approach.** Each segment already carries both `repo_key`
([`__init__.py:37`](../../src/server/worker/segments/__init__.py:37)) and — after Phase 1 —
`cost_usd_micros`. Add a helper that, for a row whose segments span >1 `repo_key`, distributes cost
by summing `segments[].cost_usd_micros` per `repo_key`; fall back to the whole-session
single-`repo_id` attribution when a session's segments are unpriced (`None`) or single-repo, so the
rollup total is unchanged for the common case. Attribute only priced segments; never re-derive from
tokens (ground rule 3).

**Product gate.** The TODO's own note stands: no current screen renders a per-repo *intra-session*
split. **Confirm a consumer exists before building this** — otherwise it's dead code. If none, mark
the checkbox "deferred (no consumer)" rather than shipping an unused path. Recommended: build the
`aggregate`-level split (small, tested) only once a drilldown is scheduled.

**Files:**
- [`src/server/api/manager/aggregate.py`](../../src/server/api/manager/aggregate.py) —
  `repo_rollup_canonical` + a `_segment_cost_by_repo` helper; remove `TODO(F3-segment-split)` on land.

**Verify.** Aggregate test: a synthetic row with two segments on two `repo_key`s splits cost to
match, and a single-repo row is byte-identical to today's output.

**Size:** S–M (gated on a consumer).

**Status: deferred (no consumer), re-confirmed 2026-09-15.** Grepped `src/server/api` and
`src/webui/src` for any reader of an intra-session per-repo split (`repo_key` × per-segment
cost) beyond the existing session-level `repo_rollup_canonical` -- none found; `src/webui` has
no consumer of `repo_key`/`repoKey` at all outside display labels. Per this phase's own product
gate, `_segment_cost_by_repo` was **not built** -- it would be dead code. `TODO(F3-segment-split)`
stays in `aggregate.py`, updated to note segment pricing (Phase 1) has removed the only
engineering blocker, so the moment a drilldown is scheduled this is a small, tested add, not a
re-investigation.

- [x] Confirm a screen/drilldown consumes a per-repo intra-session split → **none found; deferred**
- [ ] `_segment_cost_by_repo` + `repo_rollup_canonical` wiring (build once a consumer is scheduled)

**Addendum (2026-09-15): the per-session split, built.** Deferring the cross-session rollup
surfaced a real gap in the *other* direction: a session that touches two repos was being
attributed 100% to its one primary `repo_id` with **zero** cost credit to the secondary repo in
`repo_rollup_canonical` -- not double-counted, just silently wrong for the secondary repo. The
user asked for a per-session cost-by-repo view specifically (not the cross-session rollup), which
turned out to need no new fold logic: `worker/segments/_util.py::segment_repo_key` already tags
each segment with the repo it touched, and Phase 1 gives every segment a real `cost_usd_micros`.
Built:
- `manager_aggregate.segment_cost_by_repo_key(info)` -- groups a session's own segments by
  `repo_key`, sums `cost_usd_micros`, all-or-nothing per repo bucket (ground rule 3).
- `single_user/service.py::_repos_block` now adds `cost_usd_micros` to each entry in
  `GET /v1/sessions/{id}`'s existing `repos[]` (R7.1) -- the file-touch-based `share` field is
  unchanged; this is a real dollar split alongside it.
- `SessionScreen.tsx`: a new "Where the cost landed / By repo" section, rendered only when a
  session touched more than one repo (single-repo sessions already show their repo in the header).
- Tests: `test_manager_aggregate.py` (3 new, unit), `test_service_session_detail.py` (1 updated +
  1 new, DB-backed via `TALIUM_TEST_DSN`). Verified live in the browser (mock + dark mode + mobile).

This does not replace Phase 2 -- it answers "where did *this session's* cost go," not "what does
*this repo* cost across every session." Phase 2 stays deferred until a cross-session consumer
is scheduled.

---

## Phase 3 — X3-phase2: price the recoverable buckets

Chosen scope: **price all four** open buckets. Sequenced to bank the wins Phase 1 hands us, then
gate the estimate buckets behind an explicit ground-rule-3 decision.

**3a — rework + model-oversizing (free after Phase 1, verify only).**
`recoverable()`'s `rework` bucket already sums `_segment_cost_by_beat(info, "revision_loop")`
([`aggregate.py:190`](../../src/server/api/manager/aggregate.py:190), helper at
[`:143`](../../src/server/api/manager/aggregate.py:143)); it read `$0` only because segments were
unpriced. After Phase 1 it lights up with no code change. Same for `model_fit.savings_usd_micros`
([`aggregate.py:388`](../../src/server/api/manager/aggregate.py:388)). Add regression tests that
both are non-zero on a priced fixture; no new logic.

**3b — resume overhead (faithful, thread pricing).**
`resume_cost_of` ([`graph_fold.py`](../../src/server/worker/graph_fold.py)) emits per-event
`usd_micros: None`, `total_usd_micros: None`, and `projected.usd_micros: None`. `reload_tokens` are
**real** cache-write tokens re-paid on a real resuming assistant node — pricing them at that model's
cache-write rate for the session's date is faithful, not an estimate, so it honors ground rule 3.
Work: thread `pricing`/`at` into `resume_cost_of` (and its `info_of` call site), capture the
reloading node's `model`, price `reload_tokens` via `price_tokens(model, {"cache_write": n}, at,
pricing)` → `price_to_micros`, all-or-nothing per event; fill `total_usd_micros` and
`projected.usd_micros`. Then add a `resume` bucket to `recoverable()`'s `buckets` dict
([`aggregate.py:194`](../../src/server/api/manager/aggregate.py:194)) reading the priced total, and
a `_BUCKET_LABEL` entry ([`aggregate.py:150`](../../src/server/api/manager/aggregate.py:150)) in the
tone lexicon (never "waste").

**3c — read-then-discard + retry (blocked; product/architecture decision required).**
These two cannot be priced faithfully by segment pricing:
- **read-then-discard** is `est_tokens = bytes_read // 4` — a token *estimate* with no model and no
  request. Pricing it is exactly the "re-derive a dollar figure from a token estimate" that ground
  rule 3 forbids and that X3's own docstring
  ([`aggregate.py:169`](../../src/server/api/manager/aggregate.py:169)) cites as the reason it was
  excluded.
- **retry burn** is worse: its source `info.errors.retried_tokens_est` is an EconFold-era
  `metrics.error_rollup` field that **GraphFold does not produce** — §4.C api_error is dropped
  ([`graph_fold.py:1005`](../../src/server/worker/graph_fold.py:1005),
  [`:1115`](../../src/server/worker/graph_fold.py:1115)), so `recoverable()`'s `retry_tokens`
  ([`aggregate.py:192`](../../src/server/api/manager/aggregate.py:192)) and
  `_insight_5_retry_burn` ([`single_user/service.py:1223`](../../src/server/api/single_user/service.py:1223))
  read empty on every current session. Pricing it requires **first** un-dropping api_error and
  porting an `errors_of`/`retried_tokens_est` extraction into the fold — a real capture task, not a
  pricing thread — and *then* still pricing an estimate.

**Decision needed before 3c (recommended resolution).** Rather than fold estimate-derived dollars
silently into the same `recoverable_usd_micros` total as the faithful buckets — which would corrupt
a number the UI presents as real spend — surface them as priced-but-flagged: price
read-then-discard at the input rate (or session dominant-model rate) and tag the bucket
`estimated: true`, keeping it in a separate list from the faithful buckets so `recoverable_pct`
stays honest. Retry burn additionally waits on the api_error un-drop; **recommend splitting retry
into its own follow-up track** (`errors_of` extraction + pricing) rather than blocking Phase 3 on it.
Confirm this framing with product before writing 3c — it's a "what does 'recoverable $' mean"
question, not an engineering one.

**Files:**
- [`src/server/worker/graph_fold.py`](../../src/server/worker/graph_fold.py) — `resume_cost_of`
  pricing thread (3b); `errors_of` extraction (3c-retry, if approved).
- [`src/server/api/manager/aggregate.py`](../../src/server/api/manager/aggregate.py) — `recoverable`
  buckets + labels (3b/3c); remove `TODO(X3-phase2)` only when the agreed subset lands.

**Verify.** Fold test for priced `resume_cost` (faithful, all-or-nothing). `recoverable()` test: a
priced fixture yields non-zero `context`/`rework`/`resume`; estimated buckets (if built) appear
flagged and excluded from the faithful total.

**Size:** 3a XS · 3b S · 3c M+ and gated (retry is its own track).

**Status: 3a/3b done (2026-09-15); 3c not started (still gated).** 3a needed no code change, as
predicted -- `tests/server/worker/test_segments_pricing.py::test_recoverable_rework_and_model_fit_savings_are_nonzero_once_segments_are_priced`
runs a real fold-shaped fixture through `compute_segments` (Phase 1) into `aggregate.recoverable`/
`aggregate.model_fit` and asserts both light up non-zero. 3b threaded `pricing`/`at` into
`resume_cost_of` (`graph_fold.py`): `reload_tokens` price at the reloading node's own model
(grouped by model when a reload window spans more than one write -- `_price_channel`'s existing
all-or-nothing grouping, a small generalization of "the reloading node's model" for the rare
multi-model-per-gap case), `total_usd_micros` is all-or-nothing across events, and
`projected.usd_micros` prices the last assistant node's context as a hypothetical reload -- and
stays out of `recoverable()`'s total on purpose (it's a projection, not a historical spend). A new
`resume` bucket + `_BUCKET_LABEL` entry ("Resuming after a pause") were added to
`aggregate.recoverable`; see `tests/server/worker/test_resume_cost_pricing.py` and the `resume`-
bucket tests in `test_manager_aggregate.py`. 3c is untouched -- still needs the product call on
estimate-derived dollars before any code lands.

- [x] 3a: regression tests — rework + oversizing non-zero after Phase 1
- [x] 3b: price `resume_cost_of`; add `resume` bucket + lexicon label
- [ ] 3c: product decision on estimate-derived dollars (ground rule 3)
- [ ] 3c: read-then-discard priced + `estimated`-flagged (if approved)
- [ ] retry burn → split to its own track (`errors_of` extraction, api_error un-drop)

---

## Phase 4 — X4-per-segment-model: allow >1 model per segment

**Problem.** `segment_fit` ([`aggregate.py:323`](../../src/server/api/manager/aggregate.py:323))
compares each segment's task class against the **session's** single dominant tier, because segments
carry no per-segment model. `model_fit`'s `under`-first ordering
([`aggregate.py:380`](../../src/server/api/manager/aggregate.py:380)) is already written defensively
for the day a segment can carry both an over- and under-fit — `TODO(X4-per-segment-model)`
([`aggregate.py:367`](../../src/server/api/manager/aggregate.py:367)).

**Approach.** Give each segment its own model attribution in the fold. `build_segments` already
walks each segment's assistant nodes for pricing (Phase 1) — extend that same loop to record a
per-segment `models` rollup (or dominant model), keyed the way session-level `models_of` is. Then
`segment_fit` takes the segment's own tier instead of the session's, and `model_fit` can legitimately
report both verdicts. This is additive to the segment dict; the A/B differential allowlist needs a
new `info.segments[].models` (or `.model`) entry, same as prior fold-field additions.

**Files:**
- [`src/server/worker/segments/beats.py`](../../src/server/worker/segments/beats.py) —
  per-segment model rollup inside `build_segments`.
- [`src/server/api/manager/aggregate.py`](../../src/server/api/manager/aggregate.py) — `segment_fit`
  reads per-segment tier; drop `TODO(X4-per-segment-model)`; `model_fit` unchanged (already ready).
- A/B differential allowlist — new per-segment model field.

**Verify.** Fold test: a segment whose assistant nodes are all one model reports that model; a mixed
segment reports the intended rollup. Aggregate test: a session with a light-task segment on a `high`
model and a `revision_loop` segment on a `low` model now yields both `over` and `under`, with `under`
winning per the existing ordering.

**Size:** L (the only genuinely deep item; do as its own PR).

- [x] Per-segment model rollup in `build_segments` (2026-09-16 —
      `docs/plan/2026-09-15-peer-features-frontend-segment-tasks.md` Task 5)
- [x] `segment_fit`/`model_fit` use per-segment tier via `_segment_tier`, falling back to the
      session's dominant tier when a segment predates this; `TODO(model-fit-per-segment)`
      removed. `dominant_model` widened to sum only the four token fields (not `.values()`) so
      a segment bucket's extra `cost_usd_micros` key can't pollute the ranking.
- [x] A/B allowlist entry: none needed -- the existing `info.segments*` wildcard rule (already
      "accepted wholesale like turns[]") covers the new `models` sub-field; verified by running
      `tests/graph_migration/` after the change (still green). Tests added in
      `tests/server/worker/test_segments_pricing.py` (fold-level `models` breakdown +
      unpriced-is-None) and (aggregate-level `_segment_tier` fallback + a session surfacing
      both `over` and `under` with `under` winning).

---

## Testing & rollout

- Backend: `pytest tests/server/worker/test_segments_pricing.py tests/server/worker/test_segments_beats.py`
  after Phase 1; per-phase tests above thereafter. Keep the whole `tests/server` suite green (the
  known unrelated `test_otel_e2e.py` failure aside, per the gap-closure plan's own note).
- No re-ingest / backfill step: the fresh DB reload re-folds every session through the priced path.
- Per CLAUDE.md rule 4, remove each `TODO(...)` marker from source only as its phase lands, and check
  the matching box in [`2026-09-12-mvp-gap-closure-plan.md`](2026-09-12-mvp-gap-closure-plan.md).

**2026-09-15 verification (Phases 1, 2, 3a, 3b):** `pytest tests/server` — 411 passed, 528
skipped, 0 failed. Full-repo `pytest` — 2037 passed, 530 skipped, 0 failed. New/changed test
files: `tests/server/worker/test_segments_pricing.py` (new — Phase 1 + Phase 3a),
`tests/server/worker/test_resume_cost_pricing.py` (new — Phase 3b),
`tests/server/api/test_manager_aggregate.py` (updated: existing `recoverable` bucket assertion now
includes `resume`; added `resume`-bucket + `projected`-exclusion tests). `TODO(F3-segment-split)`
kept (Phase 2 deferred, no consumer); `TODO(X3-phase2)` narrowed to only the still-open 3c items
(read-then-discard, retry burn) now that 3b (resume) has landed.

## Open decisions (carry into implementation)

1. **F3 (Phase 2):** is there a scheduled drilldown that consumes a per-repo intra-session split? If
   not, defer rather than ship dead code. **Resolved 2026-09-15: no consumer found — deferred.**
2. **X3 3c (Phase 3):** may estimate-derived dollars (read-then-discard, retry) enter the
   `recoverable` total, or must they be flagged/separated to keep the headline number faithful to
   ground rule 3? Recommended: flag + separate. **Still open** — 3c was not attempted this pass.
3. **Retry burn:** confirm it splits into its own track (needs api_error un-dropped + an `errors_of`
   fold before any pricing is even possible). **Still open.**

## Remaining work

- **Phase 3c** (gated on open decision 2 above): read-then-discard priced + `estimated`-flagged;
  retry burn split into its own follow-up track (`errors_of` extraction, api_error un-drop, then
  pricing).
- **Phase 4** (X4-per-segment-model): done 2026-09-16, its own commit per the plan's own sizing
  note (turned out proportional to M, not L -- no new query, no schema change; the existing
  `info.segments*` A/B allowlist wildcard already covered the new field).
- **Phase 2** (F3 per-repo split): revisit once a drilldown consumer is scheduled; the pricing
  half of its precondition (segment `cost_usd_micros`) is already satisfied by Phase 1.
