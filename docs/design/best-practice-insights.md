# Best-Practice Lapse Detection — correlating published practice with our telemetry

*2026-07-31. Research: what the canon says about using coding agents effectively; correlation: which practices leave fingerprints in data we already collect; output: lapse-detection rules and new dashboard insights extending `user-dashboard-design.md` §6. The key design idea is §4: we don't just detect lapses against generic advice — we correlate practice adherence with outcomes **in the user's own history**, so every tip ships with personal evidence.*

**Sources.** Primary: [Claude Code best practices](https://code.claude.com/docs/en/best-practices) (Anthropic, canonical — including its named failure patterns), [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) (Anthropic: compaction, structured note-taking, subagent delegation). Secondary: community token-efficiency guides ([MindStudio](https://www.mindstudio.ai/blog/how-to-manage-claude-code-token-usage), [KDnuggets](https://www.kdnuggets.com/7-practical-ways-to-reduce-claude-code-token-usage), [dev.to 60%-reduction writeup](https://dev.to/numbpill3d/how-i-cut-my-claude-code-token-usage-by-60-and-got-better-output-48b0)) — consistent with the vendor guidance; nothing below rests on a community-only claim.

**The load-bearing vendor claim** (verbatim theme of the whole doc): *"Claude's context window fills up fast, and performance degrades as it fills."* Every practice below is downstream of that constraint — which is convenient for us, because context state per turn is precisely what we measure exactly (§4.3).

> **Implementation note (2026-09-01).** The practice-adherence vector (§4) is computed by
> `server/worker/graph_fold.py` (`practices_of`, root-session only); `delegation_ratio` is patched at the
> subagent rollup. It is a behavior-preserving port of the retired event-fold — still an event-stream scan
> for the raw per-message signals the causal graph doesn't model as nodes (verifier commands + `exit_status`,
> `char_count`, `@`-refs, the interrupt sentinel, spawn `agent_type`). `plan_phase`/`clear_used` remain
> deferred (null), as designed.

> **Applicability gate (2026-08-06).** Two hard preconditions on everything in this doc, from [`dashboard-datasource-modes.md`](dashboard-datasource-modes.md):
>
> 1. **JSONL required.** Every fingerprint in §2 reads normalized events or `session_info.info`. In an `otel_only` deployment none of it exists — the scorecard and insights 14–25 do not render, in the `unavailable` state (modes doc §5), not as zeroes.
> 2. **Claude Code only.** This is a Claude Code document: the canon is Anthropic's published guidance, and `/clear`, `/compact`, plan mode, `ExitPlanMode`, skills and subagents are Claude Code concepts. Scoring a Gemini CLI or Codex session against it produces a confident wrong answer, which is worse than no answer. For any other vendor the scorecard **hides** — not zero, not empty-state, not "no data". When the practice canon for another agent is researched, it gets its own doc and its own vector; it does not get shoehorned into this one.

---

## 1. The practice canon, condensed

From the Anthropic docs, deduplicated into ten practices (P) and the five named failure patterns (F):

| # | Practice | Essence |
| :---- | :---- | :---- |
| P1 | **Verify with a runnable check** | Tests/build/lint/screenshot in the loop; evidence, not assertion. "If you can't verify it, don't ship it." |
| P2 | **Explore → Plan → Code → Commit** | Plan mode for multi-file/uncertain work; *skip* planning when the diff fits in a sentence |
| P3 | **Specific, scoped prompts** | Name files, constraints, example patterns; vague prompts only when exploring deliberately |
| P4 | **Lean CLAUDE.md** | "Would removing this cause mistakes?" — bloat makes Claude *ignore* rules; move on-demand knowledge to skills |
| P5 | **Aggressive context hygiene** | `/clear` between unrelated tasks; `/compact <instructions>` at phase boundaries; `/btw` for side questions |
| P6 | **Delegate investigation to subagents** | Research burns tens of thousands of tokens in a disposable child context; only a 1–2k summary returns |
| P7 | **Course-correct early; two-strikes rule** | Esc/interrupt fast; after 2 failed corrections, `/clear` + a better prompt beats a third correction |
| P8 | **Adversarial review in fresh context** | Reviewer subagent sees the diff, not the reasoning that produced it |
| P9 | **Skills/hooks over prompt bloat** | On-demand loading instead of every-session injection; deterministic hooks over advisory rules |
| P10 | **Fresh session per workstream** | Spec in one session, execute in a clean one; named/resumed sessions as branches |
| F1 | *Kitchen-sink session* | unrelated tasks piled into one context |
| F2 | *Correcting over and over* | context polluted with failed approaches |
| F3 | *Over-specified CLAUDE.md* | rules lost in noise |
| F4 | *Trust-then-verify gap* | plausible code, no check |
| F5 | *Infinite exploration* | unscoped investigation fills context |

---

## 2. Correlation: practice → fingerprint in our data

Status: ✅ detectable from current normalized events/`session_info` · 🔬 detectable but the exact raw marker must be pinned empirically against real JSONL first (same method as the worker plan's turn-boundary rule) · ⏳ blocked on deferred subsystem.

| Practice | Fingerprint in our data | Status |
| :---- | :---- | :---- |
| P1 verify | `tool_call` Bash `input.command` matched against a config list of verifier commands (pytest, npm test, go test, cargo, tsc, lint, build…) + `exit_status` for pass/fail; sequence-position relative to `file_change` events via the event order | ✅ (verifier regex list = config, `dashboard.verifiers`) |
| P2 plan | `ExitPlanMode` tool_calls ✅; `mode`/`permission-mode` passthrough records for plan-mode spans 🔬 (raw shape to pin — the normalizer's `plan_mode` TODO) | ✅/🔬 |
| P3 scoped prompts | first `user_message.char_count` + `@`-reference count in text vs. turn-1 exploration cost (Read/Grep/Glob `bytes_read`, tool count before first `file_change`) | ✅ |
| P4 lean memory | provenance category B (project memory injection size, dashboard design §5) vs. peers; adherence proxy = correction rate (P7 signals) in high-B sessions | 🛠 needs provenance |
| P5 hygiene | `/clear` and `/compact` arrive as `<command-name>` wrappers in user records 🔬 (wrapper already pinned by worker plan §3 for turn rejection — reuse, don't re-derive); `compaction.trigger` distinguishes auto vs manual ✅; `/btw` 🔬 | ✅/🔬 |
| P6 delegation | delegation ratio = child exploration tokens ÷ fold-back summary size (`subagents` + G category); root-session exploration load = Read/Grep bytes in root vs children | ✅ |
| P7 early correction | interrupt sentinel `[Request interrupted by user]` (already pinned, worker plan §3) ✅; correction bursts = short user messages arriving < N min apart in one task cluster ✅; rewind/checkpoint restore = DAG branch points (one `parentUuid`, multiple user-record children) 🔬 |
| P8 review | `sub_agent_spawn` with review-shaped `agent_type`/purpose occurring **after** file_change activity | ✅ (purpose taxonomy = config) |
| P9 skills-over-bloat | `skills` usage ✅ × provenance B size 🛠 — the *ratio* is the signal |
| P10 fresh sessions | sessions-per-day vs session wall-span; mega-sessions (span > Nh, ≥2 compactions); disjoint file-cluster shifts inside one session (F1 signal) | ✅ |
| F4 trust-gap | `file_change` events with zero P1 verifier runs in the same session (or same task cluster) | ✅ |
| F5 exploration | root-session turn with high read `bytes_read`, ≥M read-type tool_calls, zero `file_change`, no subagent spawn | ✅ |

Two things worth pausing on:

1. **Almost everything is ✅ or a small 🔬.** The practice canon was written against the same JSONL surface we collect — of course it correlates. The 🔬 items (plan-mode spans, `/clear` wrapper shape, rewind branch shape) all follow the worker plan's established "pin empirically against recorded fixtures" method, and two of the three markers are already partially pinned for turn detection.
2. **`tool_call.input` carries Bash command text.** P1 detection wants only a boolean match against a verifier regex list — extract `is_verifier` + `verifier_kind` at fold time and store *that*, never the command string, keeping Level-1 metadata-only posture intact (dashboard design §1.2).

---

## 3. New insights for the catalog

Extends `user-dashboard-design.md` §6 (numbering continues; same record shape; same evidence → action → $ discipline). The five Anthropic-named failure patterns give us citable names — the insight card can literally link the vendor's own doc, which defuses "says who?" instantly.

**Mode: all of 14–25 are `jsonl` / `both`, claude_code only** — the gate above, applied uniformly, which is why the table below carries no Mode column of its own. DASH §6's insights 26–27 are the OTel-side counterparts; there is no practice insight computable from OTel metrics alone, because practice is a property of *how a session was conducted* and metrics only see what it consumed. Costs on these cards come from `cost_effective_usd_micros` like everywhere else, so in `jsonl_only` their dollar figures are priced rather than vendor-reported and are captioned accordingly.

| # | Insight | Lapse rule (defaults config-driven) | Action shown | Evidence shown | Phase |
| :---- | :---- | :---- | :---- | :---- | :---- |
| 14 | **Unverified changes** (F4) | session (or task cluster) has ≥N `file_change` events and zero verifier runs | "Add a runnable check — ask for tests/build in the same prompt" | your verified sessions' churn vs unverified (§4) | 1 |
| 15 | **Fix-loop without a check** | ≥3 edit→edit cycles on same file, no verifier run between | "You're the verification loop — give Claude the test command instead" | tokens per fix-loop vs checked loops | 1 |
| 16 | **Unplanned multi-file work** | ≥N files touched, ≥M turns, no plan phase (P2 markers) | "Enter plan mode for multi-file changes" | your planned vs unplanned $/file-changed and churn | 1–2 (🔬 markers) |
| 17 | **Plan-mode overhead on trivial tasks** (inverse of 16) | plan phase present, session ends ≤1 file, ≤2 turns | "Skip planning when the diff fits in a sentence" | plan-phase token cost on these sessions | 1–2 |
| 18 | **Vague-prompt exploration tax** (F5) | first prompt < N chars, no `@`-refs, then turn-1 read cost > threshold before any edit | "Name the files and constraints up front; or delegate the investigation to a subagent" | turn-1 exploration cost vs your scoped-prompt sessions | 1 |
| 19 | **Undelegated research** (P6) | root-session read bytes > threshold with zero subagent spawns; carried context (provenance carry) quantifies the compounding | "Use 'use subagents to investigate…' — exploration stays out of your main context" | carry cost of turn-N reads vs a subagent's fold-back size | 1 (carry: 🛠 provenance) |
| 20 | **Correction spiral** (F2, the two-strikes rule) | ≥3 interrupts/correction bursts in one task cluster, session continues without `/clear` | "After two failed corrections: /clear and restate with what you learned" | tokens after 2nd correction vs fresh-session restarts | 1–2 (🔬 /clear marker) |
| 21 | **Kitchen-sink session** (F1) | ≥2 disjoint file clusters + span > Nh + auto-compactions ≥1, no `/clear` between clusters | "/clear between unrelated tasks" | per-turn cost drift across the session (§4.3) | 2 |
| 22 | **Passive context management** | 100% of compactions auto-triggered across user's sessions, `/compact` never used | "Compact deliberately at phase boundaries with instructions — auto-compaction picks what survives, you should" | what auto-compaction discarded vs what got re-read after (re-read spike post-compaction) | 1 (`trigger` ✅) |
| 23 | **No adversarial review** (P8) | high-stakes sessions (files > N or long turns) with no review-shaped subagent and no verifier gate | "Add 'use a subagent to review the diff' before finishing" | rework rate ⏳(git) or churn rate of reviewed vs unreviewed | 2 |
| 24 | **CLAUDE.md bloat with ignored rules** (F3) | provenance B > peer median × correction-rate above user baseline | "Your memory injects ~Nk tokens/session and corrections haven't dropped — prune, or convert rules to hooks/skills" | B carry $ + correction trend | 2 (🛠 provenance) |
| 25 | **Everything-in-memory, nothing-in-skills** (P9) | B high, `skills` usage ≈ 0 | "Move on-demand knowledge into skills — loaded only when needed" | B carry $ vs measured skill-invocation cost | 2 |

Insights 1–13 from the dashboard doc remain; 3 (compaction churn), 9 (session hygiene), 10 (oversized memory) are the same phenomena as 21/22/24 seen from the cost side — merge at implementation time into single cards with both framings (cost + practice citation).

---

## 4. The correlation engine — our actual differentiator here

Generic tips are free (the docs above are public; CodeBurn ships pattern-matching). What nobody ships: **the practice canon tested against the user's own outcomes.** Three pieces:

### 4.1 Practice-adherence vector (per session, computed in the fold)

`info.practices`: `{verified, verifier_runs, verifier_pass_rate, plan_phase, exitplan_count, first_prompt_chars, at_refs, delegation_ratio, subagent_research, review_subagent, interrupts, correction_bursts, clear_used, manual_compactions, auto_compactions, fresh_session}` — booleans and small ints, all metadata-derived.

### 4.2 Outcome metrics (already designed)

Per session: tokens per file changed, hunk-churn rate (same-range rewrites), long-turn count, error/retry burn, provenance waste categories; later ⏳ rework-within-N-days from the git correlator.

### 4.3 The correlations to compute (per user, then org later)

1. **Adherence × cost:** "Your planned multi-file sessions: $1.10/file-changed. Unplanned: $2.30." (P2)
2. **Adherence × churn:** verified vs unverified sessions' same-hunk rewrite rate. (P1)
3. **The vendor's core claim, tested on your data:** per-turn edit-churn probability as a function of context tokens at that turn — we know context state exactly at every turn (Σ appended − compacted). If churn climbs past ~Xk tokens *for this user*, every hygiene insight (20–22) gets a personalized threshold instead of a folk number. **No API-, router-, or git-layer product can run this regression; it needs per-turn context state + per-edit churn, which only the transcript carries.**
4. **Delegation payoff:** turn-1-heavy-exploration sessions vs subagent-delegated ones — total cost and main-context carry. (P6)

Honesty rule: these are within-user observational correlations, not causal claims. Phrase evidence as "in your sessions, X co-occurs with Y" and require a minimum sample (config `insights.min_sessions`, default 5) before a correlation-backed card renders; below that, cards fall back to the citation-only form ("Anthropic recommends X; we detected its absence in N sessions").

### 4.4 Practice scorecard (new dashboard widget, Level 1)

A compact panel: the ten practices, each with adherence % over the timeframe, trend arrow, and the strongest personal-evidence line we have. This is the individual-first, private-by-default counterpart of Onro's org-level "champion playbooks" — same coaching territory, but grounded in per-session evidence they cannot compute, and aimed at the developer rather than their manager (M-09 posture: opt-in, dev-facing first; org rollup only when the org dashboard lands).

---

## 5. Data additions (delta to dashboard design §7)

- `info.practices` — the §4.1 vector (fold).
- `tools["Bash"]` gains nothing content-bearing: verifier matching is fold-time; only `is_verifier` counts/`verifier_kind` histogram persist (`info.practices.verifier_runs`, `verifier_pass_rate`).
- 🔬 empirical pinning tasks (recorded-fixture method, before their insights ship): plan-mode span markers (`mode`/`permission-mode` raw shapes); `<command-name>` wrapper shapes for `/clear`, `/compact`, `/btw`; rewind/branch DAG signature. First two piggyback on markers the worker plan already partially pinned for turn boundaries.
- Config: `dashboard.verifiers` (regex list), `dashboard.review_agent_types`, `insights.min_sessions`, per-insight thresholds.

## 6. Open questions

1. **Task clustering dependency.** Insights 14/15/20/21 are sharper per task cluster than per session. Full clustering is M-04 ⏳, but a cheap boundary (file-set disjointness + time gap) may be enough for these — decide during implementation rather than blocking on M-04.
2. **Bash `input.command` retention.** Fold-time matching keeps command text out of `session_info`, but the raw blob retains it (it's in `payload.raw` today regardless). Fine under current posture; revisit when the org privacy story hardens.
3. **Scorecard tone.** The same widget reads as coaching (dev view) or surveillance (manager view). This design assumes M-09 posture — dev-private first. The org-level aggregate must not ship per-dev practice scores without revisiting the review doc's DORA-style team-only decision.
4. **The dev-private posture now has a physical problem** (2026-08-06). OTel intake is org-token authenticated and stamps `org_id` on every row from the token itself, so org-level data already exists in the same tables the dev view reads — the separation is a query convention, not a boundary. v1 stays single-user (BLUE §9.5), which defers the question rather than answering it. Before any org surface ships, this doc's dev-private stance needs restating as an access rule with teeth, not an assumption inherited from a time when the data was single-user by construction.
