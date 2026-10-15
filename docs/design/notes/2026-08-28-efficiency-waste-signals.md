# Efficiency & Waste Signals — the two-sided ledger

**Date:** 2026-08-28 · **Author:** Lead Architect · **Status:** Design note (feeds dashboard design)
**Feeds:** `docs/design/2026-08-28-repo-project-insights-analysis.md` (the Efficiency hero view), the
feature-cost cards, and the tenant Repository view.

---

## 0. Why this exists — the differentiation thesis

Spend-per-repo and spend-per-user are table stakes: an LLM gateway sits at the API boundary and
already reports them. What a gateway **cannot** see is what the spend *bought* and *why it was spent*
— the session interior — and what the produced good then *cost the team to accept*. Talium has both.

> **A gateway meters the bill. We meter the work.**

The product's defensible metric is therefore never raw dollars. It is dollars **decomposed by cause**
and **divided by accepted outcome.** This note defines the signal model behind that.

A hard framing rule, load-bearing throughout: internally these are "waste" signals; **on every
user-facing surface they are "recoverable spend" / "efficiency," never "waste" and never a verdict on
a person** (tone lexicon, `session-storytelling.md` §2; the ProductSpec's own developer-snooping open
question). Recoverable is actionable and non-personal; wasted is an accusation that gets the tool
banned. The metric is attached to *work*, aggregated, and coaching-oriented — not a per-dev
stack-rank.

---

## 1. The model: two costs, one join

Every produced good carries two costs, on opposite sides of the moment the PR is opened:

- **Making cost (process, input side).** How efficiently the tokens got spent producing the good.
  Lives entirely in `session_info`. Invisible to a gateway.
- **Acceptance cost (product, output side).** The tax the output then imposed on humans downstream —
  review effort, post-open rework churn, reopen/revert. Lives in the GitHub PR lifecycle. Invisible
  to a gateway *and* to any tool that doesn't tie the PR back to the dollars that made it.

**The moat is the join**, keyed by `pr_list` (session→PR) and `repo_id`: dollars-in ↔ acceptance-tax,
per PR, per developer, per feature. Neither side alone is the product; the cross is (§4).

---

## 2. Making cost — the recoverable-spend waterfall (process side)

Decompose each unit's (dev / repo / feature / session) spend into a **productive** floor and a set of
**recoverable** buckets, each mapped to an existing `session_info` field and each implying a concrete
fix. This is the aggregate-up of the per-session cost decomposition the session drill-down already
renders (Carry / Re-cache / Output / New input).

> **Decision 2026-09-16 (supersedes the original table).** The first X3 implementation counted 100% of
> `carry` + `recache` as recoverable and summed overlapping buckets with no cap, producing recoverable
> spend of $2,919 against actual spend of $1,213 (one user, Sep 1–16). The bucket set below replaces it.
> Measured after the re-fold (90 sessions / $2,812, per-request segment pricing): bucket 1 = $405,
> bucket 2 = $64, bucket 3 = $5.
>
> **Update 2026-09-16 — rework moved to signals.** Priced correctly, rework still read ~60% of spend:
> the `revision_loop` beat fires for any file edited ≥3 times anywhere in the session, so 138 of 455
> segments (61% of spend) carried it. That is a detector problem, not a pricing one. Rework is a
> coaching signal until the detector is tightened (`TODO(rework-detector)`, `manager/aggregate.py`).

**The rule for every bucket:** *recoverable = actual cost − the cost of doing the same work well.* A
bucket that fails this test is not recoverable, however large.

**Not recoverable, by decision:**

- **Context carry (cache reads).** Re-reading the cached prefix every request is the *cheap* path
  (~0.1× input price); the counterfactual still needs the context. A high cache-hit rate is good.
- **Normal cache writes.** Each turn writes its new suffix to cache — that is growth, not waste. Only a
  re-write of already-cached context counts (buckets 1 and 2).

**Recoverable spend — the dollar total** (measured, request-level pricing; no token estimates):

| # | Bucket | Definition | Source | The fix it implies |
|---|---|---|---|---|
| 1 | Cache expired on resume | The single request after a gap > cache TTL that re-writes ≥50% of the prior context. Waste = re-written tokens × (cache-write price − cache-read price) | assistant nodes + turn gaps (`resume_cost_of`, re-scoped from "all writes in the next turn at full write price") | fewer cold resumes; wrap up before a long break |
| 2 | Cache busts | A request re-writing ≥50% of the prior context (prior context > 20K) while the cache should still be warm (gap < TTL), not explained by a compaction or a model switch. Same delta pricing as bucket 1 | assistant nodes (new detection) | keep CLAUDE.md / MCP set / model stable mid-session |
| 3 | Interrupted replies | The full priced cost of the assistant request the user interrupted and discarded | interrupt sentinel → its assistant node's own usage (`interrupted_of`, priced) | clearer first prompts; plan before building |

Buckets are disjoint by construction: 1 and 2 split on the TTL gap; 3 is a different request. Per
session, the total is capped at that session's own cost.

**Signals — shown for coaching, no dollar figure, never summed into recoverable spend:**

| Signal | Why not in dollars | Source |
|---|---|---|
| Model over-sizing | Counterfactual judgment ("a cheaper tier would have worked"), not a measured waste | X4 model-fit `over` segments |
| Rework loops | Detector too broad: any file edited ≥3 times flags its whole segment (61% of spend). Returns as a dollar bucket once tightened — segment cost minus buckets 1–3 inside it (they carry `ts`), under the same cap | `segments[].beats.revision_loop` |

**Deferred (not shipped, not shown):**

| Candidate | Why deferred |
|---|---|
| Retry loops on failing tool/MCP calls | Measured ~$19 on 146 adjacent same-tool retries over ~$2.8K spend (~0.7%) — too small to justify now. The causal graph carries per-tool `result.outcome` / `error_text` / `parent_id` but has no retry-sequence concept; detection would be new work. (`info.errors.retried_tokens_est` was api_error-only and was dropped 2026-08-29.) |
| Compaction churn | Undecided: frequent compaction and never-compacting (running near the context limit) are both costly; needs a two-sided definition. `info.read_then_discard` (reads within 600s before a compaction) belongs here, not to a "never used" bucket. |
| Read-and-never-used | No field measures it; an un-edited read can still inform reasoning, so a detector needs its own design. |
| Context bloat (task-boundary carry) | Depends on inferring task boundaries; least certain. |

**Headline metric — Recoverable spend:** the summed buckets 1–3 as a dollar figure and a % of total,
*with the dominant cause named.* Recoverable spend can never exceed spend (§2 cap).

**Practice efficiency (the behavioral lens).** The `info.practices` signals — plan-mode use,
`delegation_ratio`, `verifier_pass_rate`, auto/manual compactions, interrupts, `fresh_session` —
explain *why* a bucket is large. Often high spend is a **workflow habit**, not a skill gap, and
workflow is coachable. This is what lets us say "John's resume re-caching is high because he leaves sessions idle past the cache TTL,"
not "John is inefficient."

---

## 3. Acceptance cost — the produced-good signals (product side)

What happened to the good *after* it was opened. Acquirable within the git-source design already
specced — webhooks give the **live** stream, the GraphQL backfill gives **history** (the note already
pulls `reviews`, `comments`, `commits` connections). Ranked by how objective vs. noisy each is:

| Signal | Event / source | Objectivity |
|---|---|---|
| **Post-open push churn** | `pull_request` action `synchronize` + commit add/del after open | **Cleanest** — feedback *acted on*, real rework |
| **Review rounds** | `pull_request_review` state `changes_requested`, counted | Strong — each cycle is a concrete redo |
| **Reopen / close-without-merge** | `reopened`, `closed`(merged:false) | Strong — thrash / abandonment |
| **Later revert** | `pr_class` revert-recall (already structural) | Strong — *lagging truth* |
| **PR grew during review** | changed-lines at open vs at merge | Medium |
| **Inline review comments** | `pull_request_review_comment` count | **Noisiest** — see §5 |
| Time-to-first-review / time-to-merge | timestamps | Context, not waste itself |

**Design consequence — rank churn and review-*rounds* above raw comment count.** Churn measures
feedback that was *acted upon*; comment volume measures a reviewer's disposition (§5). The primary
acceptance-cost metric is **post-open churn ratio** (lines changed after open ÷ lines at open),
complemented by review-round count and the reopen/revert flags.

---

## 4. The join — the hero framings

### 4.1 The efficiency 2×2

Cross making-cost with acceptance-cost and you separate *"expensive because hard"* from *"expensive
because wasteful"* — which raw spend can never do:

|  | **Clean acceptance** (low churn) | **Messy acceptance** (heavy churn/reopen) |
|---|---|---|
| **Efficient making** (low process cost) | ✅ **Clean** — the template to replicate | ⚠️ **Quick & dirty** — cheap plausible code the reviewers pay for |
| **Expensive making** (high process cost) | 💪 **Heavy lift** — hard problem, done right (*not* waste) | 🔴 **Costly rework** — burned tokens *and* shipped smells → top optimization target |

The two off-diagonal cells are the whole point: ⚠️ the cheap-but-messy **Quick & dirty** cell is the
agent dishing out verbose/smelly code that detonates in review; 💪 the costly-but-clean **Heavy lift**
cell is the good-but-expensive dev whose spend was earned — and only the two-sided view exonerates them.

**Axis definitions (decision 2026-09-17).** The first implementation read making cost as "≥25% of the
PR's session spend is recoverable". That measures *efficiency*, not *cost*: a $400 PR with 10%
recoverable read "cheap", a $3 PR with 30% read "Heavy lift". Replaced by:

- **Making cost is high** when the PR's cost (the summed cost of its linked sessions) is at or above
  the **75th percentile** of the same repo's priced merged PRs over the **90 days up to this PR's own
  merge** (itself included; an open PR anchors at now). Anchored at the PR's merge so a label is stable
  and doesn't drift as later PRs land. **Unlabeled** when the PR has no linked, priced sessions, or the
  repo has fewer than **5** priced merged PRs in that window — never a guessed quadrant. All three
  knobs live in `config/api.yaml` `quality:`.
- **Acceptance is messy** when any of review rounds / reopens / post-open churn ratio meets its cutoff
  (unchanged; an uncaptured signal never flips a PR to messy).
- **Recoverable share is not an axis.** It is an efficiency figure in its own right (§2), shown
  alongside, never used to pick the quadrant.

Known limits: size is not normalized, so large features read costly by construction ($/KLOC within a
task-class × size bucket is the refinement, blocked on per-file line stats that can exclude generated
code — one repo PR changes ~400K lines); and a session linked to several PRs counts in full toward each.

Labels are deliberately plain dev shop-talk, not coined terms: **Clean** (cheap, merged clean),
**Heavy lift** (costly but a hard problem done right), **Quick & dirty** (cheap but needed cleanup),
**Costly rework** (costly *and* heavy rework). Axes keep the internal names **Making cost** and
**Acceptance cost**.

### 4.2 Quality-adjusted yield

Raw "cost per merged PR" is gameable (split PRs, merge junk). The two-sided model upgrades it to
**cost per *cleanly-merged* PR** — merged with low post-open churn, no reopen, not later reverted.
Defensible to a CFO *and* to the engineers.

### 4.3 The positive-sum move

The efficient-and-clean quadrant is not there to praise; it's a **template**. Extract what those
sessions do differently (plan mode, delegation, model choice, session hygiene) and publish it as a
team playbook — one dev's habits become team-wide recoverable spend. This is the framing a leader
champions instead of hides.

---

## 5. Measurement honesty & trust guardrails (non-optional)

Without these the metric is a liability that gets gamed and drives adoption friction:

1. **Review comments measure the reviewer as much as the code.** A nitpicky reviewer inflates counts;
   a senior's PR is rubber-stamped regardless of quality. Raw comment count must be **normalized to
   per-reviewer and per-repo baselines** — and is why §3 ranks churn/rounds above it.
2. **Attribute to the session→PR lineage, not "the agent's code" absolutely.** Humans often edit
   agent output before opening; commits between the session and PR-open aren't the agent's. Credit
   the lineage, and say so.
3. **Leading vs lagging.** Review-round signals are *leading*; the worst code sometimes merges with
   *no* comments (nobody reviewed) and surfaces later. **Reverts, reopens, and later-bug linkage are
   the lagging truth** — carry both; never let a quiet PR read as a clean one.
4. **Not all churn/comments are waste.** Incremental development and healthy review are normal. The
   signal is *excess relative to size/complexity*, or churn that reverts earlier churn (thrash) —
   never absolute volume.
5. **No per-dev stack-rank.** Surface recoverable spend attached to work, aggregated, with a named
   cause and a fix. Personal review-comment KPIs get gamed the day they ship.

---

## 6. Feasibility — what's built vs new

- **Making cost:** the fields exist (`carry`, `resume_cost`, `read_then_discard`, `errors`,
  `channel_cost`, `practices`, `segments[].beats`, `models`, `session_type`). The waterfall is an
  **aggregation + presentation** layer over data already produced. No new capture.
- **Acceptance cost:** review/comment/commit history is in the git-source GraphQL backfill contract;
  `synchronize`/`reopened`/`closed` are webhook actions; revert/rework recall is already in
  `pr_class`. New work is **carrying the post-open lifecycle counts onto the PR artifact** and the
  **session↔PR correlation** (which `pr_class/correlate.py` already establishes).
- **The join** is the net-new analysis surface, keyed on `pr_list` + `repo_id`.

So: differentiated, and largely an assembly of primitives already in hand — not speculative capture.

---

## 7. Open questions

1. **Churn normalization baseline** — per-repo median, per-task-class, or per-PR-size bucket? (Recommend
   per-task-class × size bucket, so a big refactor isn't flagged against a one-line fix.)
2. ~~**Recoverable-vs-earned threshold**~~ — **Resolved 2026-09-16** (§2): carry is not recoverable at
   all; every bucket is defined as actual − do-it-well counterfactual rather than a raw channel cost.
3. **Later-bug linkage** — how far to chase the lagging signal (revert PRs only, or issue/incident
   links too)? Ties to the Jira issue-linkage note.
4. **Human-authorship discount** — do we down-weight acceptance-cost attribution when the human
   rewrote a large fraction between session and PR-open?
5. **Aggregation privacy** — per-dev efficiency is the ProductSpec's snooping-friction risk; lead-only
   by default, or team-aggregate with opt-in personal detail?
