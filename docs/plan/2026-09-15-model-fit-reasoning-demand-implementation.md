# Model-fit rework — judge by reasoning demand, not by code output

**Status:** Ready to implement. Self-contained; can be handed to a fresh session.
**Supersedes:** the `X4` / `_LIGHT_TASK_LABELS` heuristic in
[`aggregate.py`](../../src/server/api/manager/aggregate.py) (`segment_fit`, `model_fit`,
`_LIGHT_TASK_LABELS`, `_TIER_DOWNGRADE_SAVINGS_RATIO`).
**Origin:** review on 2026-09-15 of the "model-fit" dashboard feature (the card that tells a
user *"you used an expensive model on work that didn't need it — you could have saved $X"*).

---

## 1. Why this rework exists

The "model-fit" feature decides whether a session's model choice was oversized or undersized.
Today it decides this from a single static list:

```python
_LIGHT_TASK_LABELS = {"COMPREHEND", "PLAN", "REVIEW", "MISC"}
```

A segment is flagged **"over"** (wasted money on a strong model) when the session's dominant
model is high-tier (Opus) **and** the segment's task label is in that "light" set **and** the
segment had no rework loop. See [`segment_fit`](../../src/server/api/manager/aggregate.py:360).

**The bug:** "light" here actually means *"this segment produced little or no code"*. It uses
"did not write files" as a proxy for "did not need a strong model." Those are opposite things
for exactly the two labels at issue:

- **PLAN** (thinking through an approach / architecture) and **COMPREHEND** (understanding an
  unfamiliar codebase) produce few file edits but are the most *reasoning-heavy* work a session
  does.

**External consensus (dev communities + vendor guidance, researched 2026-09-15)** is the
mirror image of the current rule:

- Planning / architecture → **top-tier model** (the widely used "opusplan" pattern in Claude
  Code runs Opus for plan mode, then Sonnet for execution).
- Understanding an unfamiliar codebase / cross-file reasoning → **top-tier model**.
- Routine implementation, single-file fixes, quick iteration → **mid-tier** (Sonnet default).
- Boilerplate, renames, mechanical transforms, file navigation → **low-tier** (Haiku).
- Code review → split (top-tier for complex multi-file, mid-tier for straightforward).

The measured reason: the top-vs-mid gap is *large on reasoning benchmarks* (~91% vs ~74% on
GPQA Diamond) but *~1 point on code-execution* (SWE-bench). So the strong model earns its
premium on **reasoning**, not on **producing code** — which is the axis the current heuristic
ignores.

**Consequence in production:** the "over" flag fires on precisely the planning/comprehension
sessions where Opus is most justified, then multiplies by a placeholder ratio to assert a
fake dollar saving. This makes the whole "recoverable spend" story overstate waste.

---

## 2. Goal

Replace the "did it write code" heuristic with a **per-segment reasoning-demand** signal, and
compare the model tier actually used against the demand the work actually showed:

- **over** — a strong model ran genuinely *low-demand* work (mechanical, no signs of
  difficulty). **Never** fires on PLAN/COMPREHEND/DESIGN.
- **under** — a weak model ran genuinely *high-demand* work (struggle signals present: rework
  loops, repeated stalls, heavy cross-file/orchestration), where a stronger model would
  plausibly have done it right the first time.
- **good** — tier matched demand.

Two secondary goals, both flagged in the current code as TODOs:

1. Replace the placeholder savings ratio (`0.6`) with a real tier-to-tier price ratio from
   `config/pricing.yaml`.
2. Move every tunable to `config/api.yaml` (global config now; the product intent is to make it
   UI-configurable later — design the config shape so that migration is trivial).

**Non-goal (explicitly out of scope):** per-segment *model* attribution. Segments still only
know the session's single dominant tier. This plan is designed to be correct with that
limitation and to drop in cleanly if/when per-segment models land (see §7).

---

## 3. The design

### 3.1 Reasoning-demand score per segment

Add a new pure function `segment_reasoning_demand(segment: dict, config) -> str` returning one
of `"low" | "medium" | "high"`. It reads only fields already present on a fold's
`info["segments"][i]` — no new extraction, no worker/fold change. Available per segment
(confirmed in [`beats.py`](../../src/server/worker/segments/beats.py) `build_segments` and
[`session_type.py`](../../src/server/worker/segments/session_type.py) `classify_segment`):

- `label` — one of `BUG_FIX, IMPLEMENT, DESIGN, OPS, PLAN, REVIEW, COMPREHEND, MISC`
- `beats` — `{kind: count}` over: `setup, survey, build, check, revision_loop, delegation,
  context_squeeze, stall, pause, interruption`
- `files` — files touched in the segment
- `turns` — assistant turns in the segment
- `confidence` — the classifier's own confidence in `label`
- `cost_usd_micros` — the segment's own priced cost

**Demand is a small, explainable weighted tally, not an ML score.** Suggested evidence
(weights live in config, §5):

Signals that *raise* demand (favor a strong model):
- `label ∈ {PLAN, COMPREHEND, DESIGN}` — the consensus "strong-model" tasks. (Large weight.)
- `revision_loop` beat present — the work actually went wrong / was hard.
- `stall` / repeated retries present — struggle.
- `delegation` beat present — sub-agent orchestration (holding many concepts).
- `context_squeeze` present or high segment cost from context carry — long-context coherence,
  the top model's measured edge.
- Many distinct `files` touched (cross-file reasoning) — above a configurable file count.

Signals that *lower* demand (a cheaper model is fine):
- `label ∈ {MISC}` or a routine/mechanical `OPS` with a single file and a clean first-pass
  `check` (no rework, no stall).
- Few files, few turns, no rework, no stall.

Map the tally to a band with two configurable cutoffs (`demand_high_at`, `demand_low_below`).
Keep the function total and deterministic; document each signal inline the way the existing
aggregate functions do.

> **Design note for the implementer:** resist making this a long formula. Three or four
> weighted signals that a human can read off the card ("flagged because: PLAN label + a rework
> loop") is the target. If a signal isn't defensible in the card's note text (§3.3), drop it.

### 3.2 New `segment_fit`

Replace the body of [`segment_fit`](../../src/server/api/manager/aggregate.py:360) with a
tier-vs-demand comparison. Keep the same signature and return values (`good|over|under`) so no
caller changes.

```
demand = segment_reasoning_demand(segment, config)   # low | medium | high
# expected tier per demand: high -> high/mid, medium -> mid, low -> low/mid
if tier == "high" and demand == "low":
    return "over"
if tier == "low" and demand == "high":
    return "under"
return "good"
```

Note this **inverts** the current rework handling: today a `revision_loop` *suppresses* an
"over" flag; now a `revision_loop` is a high-demand signal that can *drive* an "under" flag
when a weak model was used. That is the intended correction, not a regression.

Delete `_LIGHT_TASK_LABELS`. Keep the concept of a "mechanical" / low-demand label set, but as
a config list feeding the demand scorer, not a direct fit verdict.

### 3.3 Note text

[`_model_fit_note`](../../src/server/api/manager/aggregate.py:380) is a deterministic template
(no LLM — keep that). Rewrite the templates to explain the *demand* reason, e.g.:

- over: `"A high-tier model ran N low-effort segment(s) (<labels>) with no signs of difficulty;
  a lighter model would likely have handled these for less."`
- under: `"A low-tier model ran N demanding segment(s) (<labels/rework>); a stronger model may
  have avoided the rework and cost less overall."`
- good: unchanged.

Keep it lexicon-clean per `src/webui/AGENTS.md` / `schemas/README.md` (no colours, no raw
model marketing names in prose beyond the tier word).

### 3.4 Real savings figure

Replace `_TIER_DOWNGRADE_SAVINGS_RATIO = 0.6` with a real ratio derived from
[`config/pricing.yaml`](../../config/pricing.yaml):

- For an **over** verdict: estimate savings as
  `flagged_segment_cost * (1 - price_ratio(cheaper_tier, actual_tier, at))`, where
  `price_ratio` is the blended $/token of a representative model in the cheaper tier over the
  actual tier's model, priced at the segment's own date (`at`), matching the
  `channel_cost_of`/`carry_of` date convention already in `graph_fold.py`.
- Choose the representative cheaper-tier model from a config mapping (e.g. `tier_reference_model:
  {high: claude-opus-*, mid: claude-sonnet-*, low: claude-haiku-*}`), or the actual next tier
  down from the dominant model.

**Honesty constraint (ground rules 3/4).** This is a *counterfactual estimate* — the same real
tokens re-priced at a real cheaper rate, which is legitimate — but it is still hypothetical.
Surface it as an estimate (the schema field name and the note wording already say "would
likely"), never as a booked saving. If the cheaper-tier reference model has no price block
covering `at`, return the savings as `None`, not a guessed number (same cold-start posture as
the rest of the module).

### 3.5 `model_fit` orchestration

[`model_fit`](../../src/server/api/manager/aggregate.py:396) keeps its structure: compute the
session tier, attach per-segment `fit`, pick a session verdict (`under` wins over `over` — keep
that precedence; a weak model causing rework is the sharper finding), build the note, compute
savings. Only the internals it calls change.

---

## 4. Files to touch

| File | Change |
|---|---|
| [`src/server/api/manager/aggregate.py`](../../src/server/api/manager/aggregate.py) | New `segment_reasoning_demand`; rewrite `segment_fit`, `_model_fit_note`, savings math in `model_fit`; delete `_LIGHT_TASK_LABELS`; move `model_tier` substrings + all weights/cutoffs to config. |
| [`config/api.yaml`](../../config/api.yaml) | New `model_fit:` block (weights, cutoffs, mechanical-label set, tier name mapping, tier reference models). |
| [`src/server/api/config.py`](../../src/server/api/config.py) | Load the new `model_fit` config block into the `ApiConfig` object the aggregate functions receive. |
| [`config/pricing.yaml`](../../config/pricing.yaml) | No change — read for the real tier ratio. |
| `src/server/api/single_user/service.py` | No signature change expected (`model_fit(row)` gains a `config`/pricing arg — thread it from where the block is built, `_session_economics_block`, [line 909](../../src/server/api/single_user/service.py:909)). |
| `src/webui/src/screens/SessionScreen.tsx` | Only if the note/verdict copy or a new "demand" affordance is surfaced. The `ModelFit` type (`verdict/note/savings_usd_micros`) is unchanged, so **no required** front-end change; optional polish only. |
| `src/server/api/schemas/*` | Only if a per-segment `demand` field is added to the response (optional, §8). If added, regen `openapi.json` + `api-types.ts`. |

---

## 5. Config shape (proposed)

Add under `api:` in `config/api.yaml`. Values below reproduce today's *intent* as defaults so
behaviour is explainable, not a silent change:

```yaml
  model_fit:
    # model name substring -> tier (was hardcoded _MODEL_TIER_HIGH/LOW_SUBSTR)
    tier_by_name:
      high: ["opus"]
      low:  ["haiku"]
      # everything else -> mid
    # representative model per tier, for the real savings ratio (priced at the segment's date)
    tier_reference_model:
      high: "claude-opus-4-8"
      mid:  "claude-sonnet-5"
      low:  "claude-haiku-4-5"
    # reasoning-demand scorer
    demand:
      label_weights:
        PLAN: 3
        COMPREHEND: 3
        DESIGN: 2
        REVIEW: 0
        MISC: -1
      beat_weights:
        revision_loop: 3
        stall: 2
        delegation: 2
        context_squeeze: 1
      cross_file_at: 4        # files touched at/above this adds a cross-file reasoning signal
      cross_file_weight: 1
      demand_high_at: 3       # total tally >= this -> "high"
      demand_low_below: 1     # total tally < this  -> "low" (else "medium")
```

The exact numbers are the product-tunable knobs; the defaults above encode "PLAN/COMPREHEND are
demanding; a rework loop is demanding; MISC is mechanical." **Do not treat these numbers as
final** — they need a product pass (same posture as the other `config/api.yaml` thresholds).

---

## 6. Tests

Add to `tests/server/api/` (mirror the existing `aggregate`/model-fit test file):

1. `segment_reasoning_demand`: a PLAN segment with no rework scores **high** (regression against
   the old bug — this is the whole point).
2. A COMPREHEND segment scores **high**; a single-file MISC segment scores **low**.
3. `segment_fit`: high tier + high-demand PLAN segment → **good** (not "over"). This is the
   direct assertion the old code got wrong.
4. `segment_fit`: high tier + low-demand mechanical segment → **over**.
5. `segment_fit`: low tier + a segment with a `revision_loop` → **under** (rework now drives
   under, no longer suppresses over).
6. Savings: an **over** verdict with a known segment cost and known pricing yields the real
   ratio, and returns `None` when the cheaper-tier reference model has no price block at the
   segment's date.
7. `model_fit`: `under` still wins over `over` when both are present (precedence preserved).
8. Cold start: a session with no segments → `None` (unchanged).

Run only the touched module's tests per CLAUDE.md ("Verify: run the *specific* test for the
module you touched"), then the full `tests/server/api` suite before finishing since aggregate is
widely imported.

---

## 7. Interaction with per-segment model attribution (future)

Segments carry no per-request model today, so `tier` is the session's single dominant tier and a
session cannot truly be both "over" and "under". This plan is correct under that limit. When
per-segment model attribution lands (tracked separately), `segment_fit` should take the
*segment's own* tier instead of the session tier — the demand comparison is unchanged. Leave a
one-line `TODO(model-fit-per-segment)` at the `tier = ...` site pointing here.

**Resolved 2026-09-16** (`docs/plan/2026-09-15-segment-level-pricing-implementation.md` Phase
4 / `docs/plan/2026-09-15-peer-features-frontend-segment-tasks.md` Task 5): `build_segments`
now accumulates a per-segment `models` breakdown; `aggregate._segment_tier` takes the
segment's own tier when present, falling back to the session's dominant tier otherwise. The
`TODO(model-fit-per-segment)` marker is removed.

---

## 8. Optional polish (do only if cheap)

- Add `demand: "low"|"medium"|"high"` to each segment in the economics response so the front end
  can show *why* a segment was flagged (schema + `api-types.ts` regen + a small SessionScreen
  chip). Improves explainability of the card; not required for correctness.

---

## 9. Open decisions to confirm before/while implementing

1. **Default weights (§5)** — need a product sign-off on the actual numbers, like the other
   `config/api.yaml` thresholds. Safe to ship with the defaults above and tune later.
2. **REVIEW's demand** — consensus says review is split (complex vs straightforward). Default
   above treats it as neutral (`0`) so only *other* signals (rework, cross-file) push it up.
   Confirm that's the desired posture rather than always-low.
3. **Savings as counterfactual** — confirm product is comfortable surfacing a re-priced
   hypothetical as "you could have saved ~$X (estimate)". If not, keep the verdict/note and drop
   the dollar figure to `None`.

---

## 10. Definition of done

- [ ] `segment_reasoning_demand` added, `segment_fit` rewritten, `_LIGHT_TASK_LABELS` deleted.
- [ ] Real tier-ratio savings from `config/pricing.yaml`; `None` when unpriceable.
- [ ] All tunables in `config/api.yaml` under `model_fit:`, loaded via `config.py`.
- [ ] Notes explain the demand reason; lexicon-clean.
- [ ] Tests 1–8 green; full `tests/server/api` green; `openapi.json`/`api-types.ts` regenerated
      if a schema field was added.
- [ ] `TODO(X4-config)` markers at the old constants removed (CLAUDE.md rule 4); one
      `TODO(model-fit-per-segment)` left at the session-tier site pointing at §7.
- [ ] Verified live on a real planning/comprehension session: it no longer reads "over".
