# The Fidelity Advantage — what the JSONL layer gives us that nothing else can

*2026-07-30. Companion to `Competitive-Landscape-2026-07-30.md`, which this document corrects. That doc argued differentiation from market whitespace and treated the local collector mainly as an **actuation** path. That was the wrong frame: the transcript layer is first an **information** asset, and the information asymmetry is what makes the differentiation defensible. Everything below is tied to fields `src/collector/normalizer/claude_code.py` already emits.*

---

## 1. Four vantage points, and what each one structurally cannot see

Every competitor observes agent work from exactly one of four layers. The layer determines the ceiling on what they can ever measure — not their roadmap, their architecture.

| Vantage | Who | Sees | **Structurally cannot see** |
| :---- | :---- | :---- | :---- |
| **Agent-vendor analytics API** | DX, Jellyfish, Faros, Waydev | Daily per-user aggregates: tokens, model, cost, acceptance rate | Anything sub-daily. Any ordering. Any causality. Non-edit tool calls. Files. The aggregation already happened upstream — the detail is gone before they receive it. Verified schema in §1.1. |
| **OTel** | Grafana, Datadog, Faros-via-telemetry | Metrics (counters/gauges) plus a vendor-curated event stream | Metrics are aggregation by definition, and label cardinality forbids file paths, tool inputs, or per-turn identity. The event stream is richer and real-time, but it remains a *projection the vendor chose to export* — opt-in, configured by the org, with attributes the vendor selects — not the agent's own complete working record. |
| **Router / gateway / proxy** | Weave Router, Portkey, LiteLLM, Helicone | Request/response pairs on the wire — full assembled prompt and completion | **Provenance.** It receives a 40k-token prompt with no record of what assembled it. Cannot separate re-read file content from history from MCP schemas. Also blind to everything local: compaction decisions, locally-truncated tool results, permission mode, hooks, uncommitted edits. And the parent/child agent tree must be *inferred* from concurrent traffic on one key. Plus: it only sees traffic that passes through it — seat traffic never does. |
| **Git / repo** | Onro, Exceeds, Jellyfish, Waydev | Committed diffs, PRs, reverts, survival | **Process, and everything uncommitted.** Attribution back to a session is probabilistic — Onro publishes `confidence 0.96` precisely because it is inferring from the outcome side. Work the agent produced and the developer discarded before commit does not exist at this layer. |
| **JSONL transcript** | **us** | The agent's own append-only causal record of its execution | — |

The transcript is not "more of the same data at finer grain." It is a **different kind** of data: a causal graph with provenance, covering work that never reached the wire *or* the repo.

### 1.1 Terminology: "vendor" means the agent maker, not the analytics company

Throughout these docs (and in `Coding_Agent_Economics-review.md`'s landscape table), **vendor = the maker of the coding agent being measured** — Anthropic, Cursor, GitHub/Microsoft, OpenAI. It does *not* mean DX, Jellyfish, or Faros, even though those are vendors in the ordinary procurement sense of selling us-or-them software.

The distinction is the whole point: **DX, Jellyfish, Faros and Waydev are consumers of the agent vendors' APIs.** DX's connector is literally named "Claude Code (Anthropic Console)." They do not own their data supply — Anthropic does.

**Verified against the primary source** ([Claude Code Analytics Admin API](https://platform.claude.com/docs/en/build-with-claude/claude-code-analytics-api), `/v1/organizations/usage_report/claude_code`), which is the single richest agent-vendor API in this market and free to all Admin-API orgs:

- **Grain:** one record = **one user, one UTC day**. `starting_at` takes a date and "returns metrics for this single day only." The docs answer it flatly — *"Can I get real-time metrics? No, this API provides daily aggregated metrics only."*
- **What it gives:** `num_sessions`, `lines_of_code.added/removed`, `commits_by_claude_code`, `pull_requests_by_claude_code`; accept/reject counts for four tools (Edit, MultiEdit, Write, NotebookEdit); per-model `tokens.input/output/cache_read/cache_creation` and `estimated_cost`; dimensions `actor`, `customer_type` (`api` vs `subscription`), `terminal_type`.
- **What it does not give — the load-bearing part:**
  - `num_sessions: 5` is a **count**. Sessions have no identity, so *which* session burned the money is unrepresentable.
  - **No subagent field at all.** Agent fan-out — the failure mode actually burning budgets — has no representation whatsoever.
  - No Read, Bash, MCP, WebSearch or Task tool data. Only the four edit tools, and only as daily accept/reject tallies.
  - No compaction, no cache TTL split, no API errors or retries, no file paths, no hunks, no permission/plan mode, no ordering, no causality.
  - Not available on Bedrock, Vertex, Microsoft Foundry, or Claude Platform on AWS — multi-cloud enterprises have structural blind spots.

Two consequences worth stating plainly:

1. **Their ceiling is set by someone else's product decisions.** If Anthropic never adds a subagent field, no amount of engineering at DX or Jellyfish produces one. They can only re-cut what they are handed.
2. **The exposed surface skews flattering, and everyone downstream inherits the skew.** This is a sharper form of the review doc's incentive argument (§2.3). It is not only that an agent vendor won't recommend a competitor's cheaper model — it is that the agent vendor *defines the measurement surface on which its own product is judged*. Every metric in that API is an adoption-flattering numerator: lines added, commits, PRs, acceptance rate. Not one is a waste denominator — no compaction churn, cache decay, retry burn, subagent fan-out, or abandoned edits. No conspiracy is required for this; it is what a team instruments when measuring its own success. But a platform built on it inherits the framing.

The acceptance-rate confound (§3.5) also gets concrete here: the accept/reject fields exist, but **nothing in the API reports whether the user was in auto-accept mode** — where everything is "accepted" by construction. Competitors selling acceptance rate as a quality signal are computing it from exactly these fields.

---

## 2. The concrete inventory — what we already extract

Grounded in `claude_code.py`. Each item is annotated with which competing layer is blind to it.

| # | Signal | Fields we emit | Blind to it |
| :---- | :---- | :---- | :---- |
| a | **The causal DAG** — `uuid`/`parentUuid` on every record. Not a timeline: a tree. We know exactly which prompt caused which turn caused which tool call caused which edit. | `record_id`, `parent_id` on every `EventIn` | API (no ordering), OTel (no identity), router (independent requests), git (no process) |
| b | **The real agent tree, recursively** — `sub_agent_spawn` carries `child_session_id`; `_normalize_stream` recurses and stamps `parent_session_id` | `agent_type`, `child_session_id`, `parent_session_id` | Everyone. Router sees N unrelated requests; OTel sees a counter |
| c | **Compaction economics** — every compaction event with before/after token counts, trigger, and duration | `tokens_before`, `tokens_after`, `tokens_reclaimed`, `trigger`, `duration_ms` | Everyone. A router just sees a differently-shaped prompt and cannot know a compaction occurred |
| d | **Cache economics with TTL structure** — cache writes split 5m vs 1h, plus reads | `cache_write_detail[{ttl_s, tokens}]`, `cache_read_tokens`, `cache_write_tokens` | API (daily totals only), router (sees cache headers, not TTL structure or the idle gap that expired it) |
| e | **Retry and rate-limit waste** — the agent's own retry bookkeeping | `error_class`, `http_status`, `retryable`, `retry_count`, `retry_after_ms` | API (netted out upstream), git (n/a) |
| f | **Every edit at the moment of edit — pre-commit** — hunk-level, with line counts | `structuredPatch` → `hunks[{start,len}]`, `lines_added`, `lines_removed`, `operation` | **The entire git layer, by construction.** See §3.4 |
| g | **Read amplification & per-MCP-server overhead** | `bytes_read`, `input_chars`, `files[{path,operation}]`, `mcp_server`, `mcp_tool` | Router (cannot attribute prompt bloat to a specific MCP server's schemas), API, git |
| h | **Supervision and workflow mode** — slash commands, plan mode, permission mode, why each turn ended | `slash_command`, `plan_mode`*, `mode`/`permission-mode` passthrough, `stop_reason` | Everyone. See §3.5 — this one invalidates a metric all of them publish |
| i | **Tool-level execution shape** — failures, durations, ordering | `exit_status`, `duration_ms`, tool ordering via (a) | API, OTel, git |

\* `plan_mode` is currently `None` with a `TODO(normalizer)` in `_make_user_message`. Per §3.5 that TODO is now strategically load-bearing, not cosmetic.

---

## 3. What we make of it — differentiation derived from the fidelity

Ordered by defensibility. Each is a thing a competitor's architecture forbids, not merely something they haven't built.

### 3.1 Token provenance — a cost decomposition, not a "finding" ⭐ *build this first*

**From (c)(d)(g)(a)(b).** For any turn, decompose input tokens by **origin**: system prompt + CLAUDE.md, MCP tool schemas per server, file content re-read into context, conversation history, compaction survivors, subagent return values. Roll that up to session, team, org.

This turns waste from a heuristic label into an **accounting identity**. Compare the two outputs:

> **Faros:** "wasteful: $312k — patterns include duplicated context, agent loops, frontier overuse."
> **Us:** "Of $2.1M annual spend: $340k is MCP tool schemas, of which $180k is three servers with zero tool calls in 90 days. $220k is files re-read inside the same session. $190k is compaction churn. $95k is retries on 429s."

Same shape of claim; categorically different epistemic status. Theirs is an estimate produced by classifying sessions; ours is a decomposition where every token is traced to its origin and the parts sum to the invoice. **A router cannot produce this at any price** — it receives the assembled prompt and provenance is already lost. This is the single strongest asset and it is close to reach given (c)(d)(g) already land in the event stream.

It also reframes the moat argument correctly: **we don't need to claim we own the brain — we own the substrate.** Faros's productive/inefficient/wasteful classification is a legitimate brain-layer output computed over coarse input. We can derive the same three buckets *as a consequence* of the decomposition. Whoever has the higher-resolution substrate wins that comparison on the merits, in a demo, in front of the buyer's own data.

### 3.2 Real-time convergence vs. thrash — with no git dependency ⭐

**From (f)(a)(i)(h).** Hunk-level edit churn *within* a session — the same file and overlapping hunk ranges rewritten N times — plus tool-failure loops, plus `stop_reason` truncation patterns, plus sub-agent fan-out without edit progress.

The important property: **this needs neither git nor a waiting period.** Every competitor's quality proxy — rework rate, revert rate, code survival — is a 7-to-30-day git lookback. Ours is computable in-session, live. That is what makes control *smart* rather than arbitrary: Claude Code's native `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION=200` truncates a converging refactor and a thrashing loop at the same number, because a counter cannot discriminate. Hunk-overlap churn can.

This is the correct technical foundation for the D1 "agent run economics" position, and the reason it is not merely a market gap.

### 3.3 Exact attribution, down to the hunk

**From (a)(f).** `user_message.uuid` → `assistant_message` → `tool_use.id` → `structuredPatch` hunks → file → commit. A chain of recorded facts, not a correlation window.

Onro's headline attribution ships a confidence score because they infer from the git side. We can be exact — and go a layer deeper than they can express: **per-hunk cost.** "This 12-line function cost $4.30 across 3 attempts." Nobody at the git layer can attach a dollar figure to a hunk, because they never saw the attempts.

### 3.4 Dark waste — work that was never committed ⭐ *the novel number*

**From (f).** We record every edit the agent made. The git layer records only what survived to a commit. The difference is invisible to Onro, Exceeds, Jellyfish, Waydev, and DX **by construction** — and it is likely a large fraction of real waste, because the most common failure mode is an agent producing code the developer throws away before it ever reaches the index.

"23% of the code your agents wrote last month never reached a commit, at a cost of $180k" is a number no competitor can compute, and it is exactly the kind of number that makes a CTO take a meeting. It also closes a real hole in our own M-06, which currently reasons only session→PR.

### 3.5 Supervision-mode-adjusted quality — a competitive attack, not a feature

**From (h).** Onro sells *cost per accepted suggestion*. Exceeds sells *acceptance rate*. Jellyfish and DX both report acceptance. **None of them know whether auto-accept was on.** Acceptance rate under auto-accept measures nothing but throughput; it is a confounded metric being sold as a quality signal.

We can read permission mode and plan mode from the transcript, segment every quality metric by supervision level, and demonstrate the confound on the buyer's own data. That is a sales weapon, and it is a two-line normalizer change away — which is why the `plan_mode` TODO should be promoted now.

### 3.6 Cache-decay and compaction-regression detection

**From (c)(d).** Priced cache TTL waste: tokens written to a 5-minute cache that expired before reuse because the session went idle — computable exactly, because we have the TTL bucket and the inter-turn timing. And compaction-induced token inflation, tracked per agent version.

This last one is a *strategically* interesting product. Compaction regressions are real, shipped, vendor-caused cost events — the tokenomics survey documents Codex v0.116→v0.118 doubling compaction frequency and roughly tripling token consumption, and Claude Code's compaction engine has documented cache and correctness failure modes. **Being the platform that detects a vendor's bad release from your own spend curve** is a report no vendor will ever write about itself, and it is the most concrete possible proof of the neutrality everyone else only claims.

### 3.7 Retry and rate-limit waste

**From (e).** Straightforward once (e) is in the stream; priced tokens burned on attempts that 429'd/529'd, and separation of retry storms from genuine work.

### 3.8 Pre-flight cost estimation — now much stronger

Predictive economics (previously D5) is far more credible from this substrate. You cannot forecast the cost of a task from daily aggregates. From per-task, per-model, per-hunk history with known supervision mode, you can put a cost band and a rework-risk band on a task before it runs. Competitors at the API and git layers lack the training signal.

---

## 4. What this changes in the earlier assessment

- **Demote D3 (cost per surviving change).** Good metric, but it lives at the git layer — Jellyfish and Onro can and will build it. It should be table stakes for us, not a differentiator.
- **Promote the transcript-native metrics** (§3.1–3.6) to the top of the differentiation stack. These are the ones a competitor's architecture forbids.
- **Keep D2 (seat-plane actuation), reframed.** Actuation stays valuable, but it is downstream: the reason our brake is better than a counter is §3.2, not the hook mechanism itself.
- **Keep D4 (seat-plan economics).** Unaffected and still unowned.
- **Retire "we own the brain."** Replace with **"we own the substrate."** It is defensible, demonstrable on the buyer's own data, and doesn't require claiming a competitor's shipped feature doesn't exist.

Faros made an architectural commitment — explicitly no developer-machine install — that they cannot reverse without becoming a different company. Weave's router sits on the wire and structurally cannot see local truncation, compaction, permission mode, or uncommitted edits. Onro has a collector but is spending its differentiation *git-side*, on commit attribution and champion playbooks — using the transcript to reach the commit rather than mining the transcript itself. That is the gap.

---

## 5. Honest costs of this bet

Not reasons against it — things that must be resourced, and which are mostly barriers to entry once paid.

1. **Format fragility.** JSONL is an undocumented internal format that changes without notice. `_as_dict`'s present-but-wrong-type defense and the passthrough-unknown stance in `_dispatch` are the right posture and already in place. This is a permanent tax — and also a moat, since the reverse-engineering and the retained `raw` payloads let us re-derive history retroactively when we learn what a field meant.
2. **Uneven cross-tool depth.** This resolution is Claude Code today. Codex, Cursor, and Copilot write transcripts of varying richness, so table stake #7 ("one yardstick across tools") will be genuinely tiered. Handle it the way Onro does — publish the tiering honestly ("high-confidence / inference-augmented") rather than implying uniform depth.
3. **Privacy gets sharper, not looser.** Transcripts contain prompts and file content; `redact.py` exists and defaults to no patterns. But note the strong story available here: **provenance decomposition needs token *counts and origins*, not content.** §3.1 through §3.7 are all computable from sizes, paths, hunk ranges, and timings. That makes "we measure the shape of your context, not its content" technically true rather than marketing — and it partly dissolves the prompt-gist conflict logged as open risk #4 in the review doc.
4. **Data volume.** Per-turn provenance at org scale is a real data-engineering cost — `out.json` is already 8.7MB from modest local data. Aggregation strategy and retention tiering need designing before this scales, not after.

---

## 6. Suggested next step

Prove §3.1 on our own data before writing more strategy. We have `out.json` and the normalizer already emits (c), (d), and (g). A one-off analysis that decomposes input tokens by origin for a handful of real sessions would establish whether the accounting identity closes — whether the parts actually sum to the observed input tokens. If it does, that decomposition is the demo, the differentiator, and the thing to build the product around. If it doesn't close, we learn which fields are missing while it's still cheap to find out.
