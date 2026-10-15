# User Dashboard — Design

> **v2 note (2026-08-01).** This doc is now part of a four-doc set: overall UX/IA lives in [`dashboard-ux-blueprint.md`](dashboard-ux-blueprint.md) (what goes where); the session deep dive (§4 here) is superseded in presentation by [`session-storytelling.md`](session-storytelling.md) — §4.2's metrics move into tabs beneath the story; practice insights 14–25 live in [`best-practice-insights.md`](best-practice-insights.md). §9 phasing is superseded by the implementation plan `docs/plan/2026-08-01-user-dashboard-implementation.md`. Data mappings, provenance (§5), insights 1–13 (§6), additions (§7) and APIs (§8) here remain authoritative.
>
> **v3 note (2026-08-06) — the OTel revision.** This doc was written three days before the hybrid intake froze ([`2026-08-04-otel-intake-design.md`](2026-08-04-otel-intake-design.md)) and originally assumed JSONL is the only source. It is not: an org may deploy the collector, OTel telemetry, or both, and the difference is a standing deployment fact. **[`dashboard-datasource-modes.md`](dashboard-datasource-modes.md) is now a fifth member of the set and is binding on everything here** — the capability matrix (§2 there), the cost precedence chain (§3 there), and the coverage copy (§5.1 there). The sections revised for it: §1.3 (pricing — materially rewritten), §2, §3.1, §3.2, §3.4, §5.1, §5.4, §6, §7, §8.

*2026-07-31. Scope: the **single-user** dashboard (org/team/cohort deferred). Method: work backwards from what each screen shows → map every widget to data we already have (`session_info`, `sessions`, `signal_blob.events`) → whatever is missing becomes a `session_info` addition computed by the fold. Companion docs: `docs/ProductSpec/Fidelity-Advantage.md` (§3.1 provenance is the centerpiece here), worker plan `2026-07-27-worker-session-analytics.md` (P0–P6 shipped substrate this design consumes).*

**Status legend used throughout:** ✅ have now · 🔶 derivable at query time from existing data · 🛠 needs a fold/`session_info` addition · ⏳ blocked on a deferred subsystem (pricing engine, git correlator, task classification).

---

## 1. Principles

1. **Two levels, one spine.** Level 1 (user overview) is aggregation over `session_info` rows; Level 2 (session deep dive) reads one session's `signal_blob.events`. Nothing on Level 1 may require touching event blobs at query time — if the overview needs it, the fold must have rolled it up.
2. **Metadata, not content, by default.** Every widget below is computable from counts, sizes, paths, names, and timings. Prompt text appears only in the deep-dive journey view and only under the org-level opt-in (review doc, open risk #4). The `ai-title` passthrough record gives us a session title without shipping prompt content — use it.
3. **The cost block is the cost.** *(Rewritten 2026-08-06; the original "dollars are computed at query time, the fold never writes a price" rule no longer describes the system.)* The OTel true-up worker writes a cost block per session — and its `touched_sessions` query has a dedicated arm for JSONL-only sessions, so **every session in every mode has one**. The dashboard reads `cost_effective_usd_micros` (promoted to a first-class `session_info` column) and never re-derives dollars from `session_info.models`, which are JSONL tokens and therefore blind to background utility calls.

   Three figures, never collapsed: `cost_usd_micros` (vendor-reported), `cost_est_usd_micros` (priced from tokens via the `common.model_pricing` catalog (`Pricer`)), `cost_effective_usd_micros` (vendor where present, estimate otherwise — the one the UI shows), plus `cost_basis` per request and `cost_complete` for the session. An estimate can never silently overwrite vendor truth.

   The price table survives, and so does re-pricing history — but it is no longer free: a changed `pricing_fingerprint` triggers a refold rather than a recomputation on read. `calibrate()` checks the table against vendor truth on every request that has both, which is the mechanism that makes an estimate defensible; its output is **internal and never rendered**.
4. **Insights are prescriptions with a dollar figure**, not observations. Every insight names the waste, the evidence, the action, and the estimated recoverable spend (CodeBurn sets the free-tier bar here; our provenance decomposition is what lets us beat it — see §6).

---

## 2. Data inventory (what the design builds on)

| Store | Available now |
| :---- | :---- |
| `sessions` | `started_at`, `ended_at`, `last_event_at`, `status`, `agent`, `agent_version`, `parent_session_id`, `user_id`, `producer_id` |
| `session_info` | `models` `{model: {input, output, cache_read, cache_write}}` · `tools` `{name: {count, type, ok, failed, mcp_server?}}` · `files` · `skills` `[{skill_name, count, caller_type, success, tokens, started, ended}]` · `subagents` `{child_id: {tokens, duration_ms, purpose, agent_type, origin, model, tools, files}}` · `turns` `[{index, started, ended, duration_ms, tokens, subagents_spawned, files_changed, long, …}]` · `turn_count`, `long_turn_count` · `info.tokens_total` |
| `signal_blob.events` (deep dive only) | full ordered stream: `session_start` (cwd, git_branch), `user_message` (char_count, slash_command), `assistant_message` (per-turn usage incl. `cache_write_detail` TTL split **and `provider_request_id`** — the OTel join key), `tool_call` (input_chars, bytes_read, exit_status, duration_ms, mcp_server), `file_change` (hunks, lines ±), `sub_agent_spawn`, `api_error` (class, retry_count, retry_after), `compaction` (tokens_before/after/reclaimed, trigger), passthroughs (`ai-title`, `turn_duration`, `mode`, …) |
| **`session_info.otel`** (2026-08-06) | the true-up worker's cost block, **present in all three datasource modes**: `measures` (canonical metric totals), `tokens`, `requests[]` (per-request ledger with `model`, four token fields, `cost_usd_micros`, `cost_est_usd_micros`, `cost_basis`, `query_source`, `duration_ms`), `sources`/`cost_basis`/`otel_gap` counters, `cost_effective_usd_micros`, `cost_complete`, `vendor`, `otel_since`, `calibration` |

Known blob-level signals **not yet rolled up** by the fold: compactions, api_errors, cache TTL detail, cwd/branch, ai-title, per-tool bytes_read totals. These recur below and are consolidated into the §7 additions list.

**Which of these exist depends on the deployment.** `session_info.info`, `models`, `tools`, `turns` and `signal_blob.events` are JSONL products — absent entirely in `otel_only`. `session_info.otel` is present everywhere. The capability consequences are the modes doc §2 matrix; the mapping tables below carry a **Mode** column where they differ.

Not consumed by this design, recorded so nobody re-derives it: the OTel **logs** signal carries tool names, durations, byte sizes, prompt lengths and edit accept/reject decisions that would give `otel_only` much of §3.3 and parts of §5 with no collector. All of it is count-and-dropped by v1's `accept: ["api_request"]` filter, and widening that is the otel design's §9 v2 seam — out of scope here, and **not to be designed against** (modes doc §1.1).

---

## 3. Level 1 — User Overview

Layout, top to bottom. Timeframe selector (7d / 30d / 90d / custom) governs everything.

```
┌─ headline strip ────────────────────────────────────────────────┐
│ Total tokens · Est. cost $ · Sessions (active) · Sub-agents      │
│ Turns · Long turns · Files touched · Repos                       │
├─ consumption ───────────────┬─ where it goes ────────────────────┤
│ Model pie (tokens) +        │ Provenance mix (aggregated §6      │
│ cost-by-model bars          │ origin decomposition, all sessions)│
├─ usage patterns ────────────┴────────────────────────────────────┤
│ Top tools · Top skills · Top MCP servers · Repos worked on       │
├─ areas of interest ─────────────────────────────────────────────┤
│ Flagged sessions list (badges: 💰 costly · ⏱ long-running ·      │
│ 🗜 compaction-heavy · 🌿 fan-out · ⚠ error/retry · ❄ cold-cache) │
├─ insights ──────────────────────────────────────────────────────┤
│ Ranked prescriptions with $ recoverable (§6)                     │
└─────────────────────────────────────────────────────────────────┘
```

### 3.1 Headline strip

Sources resolve through the **configurable precedence chain** (modes doc §3.3) — first available wins, and every figure reports which source produced it:

```yaml
dashboard:
  precedence:
    tokens: [otel_measures, otel_ledger, jsonl_models]
    cost:   [otel_vendor, estimated]
```

| Metric | Source | Mode | Status |
| :---- | :---- | :---- | :---- |
| Total tokens (in/out/cache split on hover) | precedence chain: Σ `otel.tokens` → Σ ledger → Σ `session_info.models` | all | ✅ |
| Cost | Σ `cost_effective_usd_micros` (§1.3), captioned with its basis | all | ✅ |
| Sessions in timeframe (+ currently-active count) | `sessions` rows overlapping window; active = `status`/`last_event_at` recency | all — **needs the OTel stub row** (modes doc §8) | 🛠 |
| Active time | `otel.measures.active_time_s` → Σ turn durations | all (exact only with OTel) | 🛠 measure mapping |
| Sub-agents spawned | Σ `len(subagents)` | jsonl, both | ✅ |
| Turns / long turns | Σ `turn_count`, `long_turn_count` | jsonl, both | ✅ |
| Files touched (created/edited split) | Σ `files` | jsonl, both | ✅ |
| Repos worked on | distinct `repo_id` across sessions | jsonl, both | ✅ `repo_id` is an evidence-backed inference (`src/common/git_ident.py`, `metrics.py`'s `repo_info`) — `pr_link` (a `pr-link` record's `prRepository`) > `remote` (a `git remote -v`/`gh repo view` capture) > `path` (worktree-stripped `cwd`, the fallback floor, never a stable identity). `repo_id_provenance` names which tier resolved it; a `path`-tier entry must not be presented as fact the way a `remote`/`pr_link` one can be — same caution the UI already applies to an `estimated` cost figure. Promoted to a `session_info` column and the standalone `repositories` table (`GET /v1/users/{id}/repos`); `repo_key` remains a display-only label, never the join key |

In `otel_only` the strip renders its first four rows and drops the rest — the zone carries one `SourceNote`, not four `unavailable` tiles.

### 3.2 Consumption

- **Model pie + cost bars** — per-model token splits from the precedence chain (the OTel ledger carries `model` per request, so this works in every mode); dollars from `cost_effective_usd_micros`. One chart, two toggles (tokens / $). Include cache_read shading per slice so "Opus-heavy but 90% cached" reads differently from "Opus-heavy uncached".
- **Spend by `query_source`** — 🆕 2026-08-06, modes `otel`/`both`. The background-utility split: `prompt_suggestion`, `generate_session_title` and their siblings against interactive spend. Roughly 5.7% of session cost on the reconciliation session, and **invisible to JSONL entirely** — so this widget is simultaneously a real answer ("what is title generation costing me?") and the most concrete demonstration of what telemetry adds. Needs the `by_query_source` rollup (§7); `effort` and `speed` ride the same attributes if a second facet is ever wanted.
- **Provenance mix** — the §6 decomposition summed over all sessions in range: one stacked bar answering "of 41M tokens this month: X% file re-reads, Y% MCP schemas, Z% conversation carry…". 🛠 This is the flagship widget; nobody else can render it. Modes `jsonl`/`both`; in `jsonl_only` the K category (§5.1) is structurally absent and the stack says so.

### 3.3 Usage patterns

| Widget | Source | Status |
| :---- | :---- | :---- |
| Most-used tools (count, fail rate) | Σ `tools` by name | ✅ |
| Most-used skills (count, success rate, tokens) | Σ `skills` | ✅ (add per-invocation `args` gist for "for what" — 🛠 small extractor addition) |
| Most-used MCP servers (calls, tokens via §6 attribution, fail rate) | `tools` where `mcp_server` set | ✅ counts / 🛠 tokens |
| Repos worked on (sessions, tokens, files per repo) | `session_info.repo_id` + the `repositories` table | ✅ `GET /v1/users/{id}/repos` — grouped by `repo_id`, not `repo_key`, so two worktrees or two clones of the same remote collapse into one entry instead of inflating the count |

### 3.4 Areas of interest (flagged sessions)

A ranked list of sessions worth a click. Flags are computed **at fold time** into scalar columns so the list is one indexed query — no JSONB scans.

| Badge | Rule (defaults; config `dashboard.flags.*`) | Needs |
| :---- | :---- | :---- |
| 💰 Costly | `cost_effective_usd_micros` > p90 of user's trailing 30d, or > absolute floor (dollars, not tokens — the block makes them comparable across models) | 🛠 promoted column (modes doc §3.2); `tokens_total` also promoted for the tokens sort |
| ⏱ Long-running | `long_turn_count > 0` or wall duration > threshold | ✅ |
| 🗜 Compaction-heavy | `compaction_count ≥ 2` | 🛠 `compaction_count` scalar + `info.compactions` list |
| 🌿 Fan-out | `subagent_count ≥ N` or spawn depth ≥ 2 | 🛠 `subagent_count` scalar (len of existing JSONB) |
| ⚠ Error/retry | `error_count ≥ N` or retry tokens > threshold | 🛠 `error_count` scalar + `info.errors` rollup |
| ❄ Cold cache | session cache-hit ratio < threshold | 🔶 from `models` (cache_read ÷ (input+cache_read)); optional scalar for sorting |

Each row: title (`ai-title` 🛠 extraction), repo, started, duration, tokens, cost, badges. Sort by any flag dimension — all sort keys are scalar columns, which is why cost is promoted out of the `otel` JSONB rather than extracted from it.

Badges are JSONL-derived without exception, so **`otel_only` sessions carry none** and cannot be flagged. They still appear in the list (cost, model, time, vendor) as reduced rows — a costly OTel-only session is still worth seeing, it just has nothing to click into. A lag indicator (`jsonl_lag_s` set, modes doc §7) is not a flag and does not participate in filtering: it describes our pipeline, not the session.

---

## 4. Level 2 — Session Deep Dive

### 4.1 Session graph (the centerpiece)

A horizontal timeline built from `signal_blob.events`, three synchronized bands:

```
tokens   ▁▂▃▅▆▇█  cumulative burn (step line; slope = burn rate)
         ├─ turn 1 ──┤├─ turn 2 ─────────┤├─ t3 ┤├─ turn 4 ───────┤
lanes    ●──tool─●●─●   ●──────●  🗜      ●─●     ⚠●●─●
           └─ subagent A ──┘ └─ subagent B (parallel) ──┘
```

- **Band 1 — burn line:** cumulative `input+output+cache_write` per `assistant_message`, with cache_read as a lighter overlay. Slope inflections are visually where the money went.
- **Band 2 — turn track:** `turns[]` ✅ segments; long turns tinted; per-turn token/subagent/file counts on hover.
- **Band 3 — activity lanes:** tool calls (width = duration_ms, color = tool_type, red = nonzero exit), subagent spans as child lanes (`started/ended` from `subagents` rollup), markers for 🗜 compaction (annotated with tokens_reclaimed), ⚠ api_error, and skill invocations.

Data: one endpoint returning a condensed, ordered event list (§8). All fields exist in the blob today ✅ — this is a pure read-path transformation, no fold change.

**Journey narrative** ("how it started, what was achieved"): title from `ai-title`; then one line per turn: `turn N — {slash_command | "message (312 chars)"} → tools used → files changed ± lines → subagents`. With prompt-content opt-in, the user message text replaces the char-count placeholder. "What was achieved" in the outcome sense (PRs, commits) lands with the git correlator ⏳; until then the honest proxy is the files/lines/turn summary. Full task labels are M-04 ⏳.

### 4.2 Session metrics panel

| Metric | Source | Status |
| :---- | :---- | :---- |
| Tokens by model + cost | `models` (+ pricing) | ✅ / ⏳ |
| Turns, long turns, wall time, active time (Σ turn durations) | `turns` | ✅ |
| Tools: counts, fail rate, time in tools, bytes_read total | `tools` + 🛠 add `bytes_read`/`duration_ms` sums to the tool accumulator |
| Skills: name, args gist, success, tokens | `skills` + 🛠 args gist | ✅/🛠 |
| Subagents: tree with per-child purpose, model, tokens, duration | `subagents` | ✅ |
| Files: touched/modified, lines ±, by extension | `files` | ✅ |
| Compactions: count, tokens reclaimed, trigger, timing | 🛠 `info.compactions` | 🛠 |
| Cache: hit ratio, TTL split (5m vs 1h writes), decay events (§6 cat. J) | `models` ✅ + 🛠 `info.cache` |
| Errors: by class, retries, retry-burned tokens | 🛠 `info.errors` |
| Provenance decomposition (§6) | 🛠 `info.provenance` |

---

## 5. Token provenance decomposition (the flagship computation)

Answers, per session: *"this session cost 2.4M input tokens — where did each come from?"* Then aggregates across sessions for the overview mix. Runs in the fold (post-hoc, idempotent, from the full blob) — never in a hot path.

### 5.1 Origin categories

| Code | Origin | Attribution source |
| :---- | :---- | :---- |
| A | Fixed context: system prompt + built-in tool schemas | first `assistant_message`'s `cache_write` minus (B+C+D estimates) — the turn-1 residual |
| B | Project memory (CLAUDE.md, rules, memory files) | Read/injection records where path matches memory locations; cross-session baseline differencing |
| C | MCP tool schemas, per server | cross-session differencing: turn-1 overhead of sessions with server S attached vs without (needs `info.mcp_servers` attached-set 🛠, from tool names + `mode` passthroughs) |
| D | User messages | `char_count` ÷ 4 (calibrated, §5.3) |
| E | Assistant output re-entering as history | prior turns' `output_tokens` (exact) |
| F1 | File reads — first read | `tool_call.bytes_read` ÷ 4 for first Read of path |
| F2 | File reads — **re-reads** (waste candidate) | same, subsequent Reads of same path |
| F3 | Other tool output (Bash, Search, Web) | `bytes_read` ÷ 4 by tool_type |
| F4 | MCP tool results | `bytes_read` where `mcp_server` set |
| G | Subagent returns | fold-back `tool_result` size of Task/Agent calls |
| H | Skill/command injections | `isMeta` user records + `<command-name>` wrappers, char_count ÷ 4 |
| I | Compaction survivors | `compaction.tokens_after` (exact) — replaces prior E/F/G history at the boundary |
| **K** | **Background utility calls** 🆕 | `otel_api_requests.query_source` (exact) — `prompt_suggestion`, `generate_session_title` and siblings. **Modes `otel`/`both` only** |
| R | Residual (unattributed) | reconciliation remainder — **reported, never hidden** |

**On K (added 2026-08-06).** These calls are ~5.7% of session cost, are issued by the client rather than by a turn, and appear in **no JSONL record at all**. Before this category they had nowhere to go: in a hybrid session their tokens would land in the OTel-derived total with no origin, inflating R and making our own residual metric look like a pipeline defect rather than a coverage gap. K is exact where it exists and structurally absent in `jsonl_only` — a distinction the UI must draw, because "no background spend" and "no visibility into background spend" are different claims.

Interrupted requests are K's mirror: JSONL-only, real input-side tokens, placeholder output, absent from OTel because it fires on completion. They belong in the decomposition and must be flagged as interrupted, or their unmatched output distorts category E.

### 5.2 Algorithm — delta attribution with reconciliation

Context between compactions is append-only. So:

1. **Per turn *i*:** `appended_i = cache_write_i + input_i` (what wasn't already cached). Collect everything that entered the stream between assistant *i−1* and *i* via the `parentUuid` DAG: tool results (F/G, exact bytes), user message (D), previous output (E), injections (H). Estimate each in tokens; distribute `appended_i` proportionally; the gap goes to R.
2. **Turn 1:** `appended_1` ≈ A+B+C+D. B and C from the cross-session baselines; A is the residual.
3. **Compaction boundary:** reset attribution state; category I = `tokens_after` (exact); everything reclaimed reduces the carried categories proportionally.
4. **Reconciliation identity (the honesty check):** `Σ categories + R = Σ (input + cache_write)` per session, exact by construction; **R is a quality metric of our own pipeline** — trend it, alarm if p50 R > 10%.

   *"Exact by construction" is precisely the weakness.* Both sides of that identity come from JSONL, so it can only catch attribution bugs, never a gap in the source itself. Where OTel is present there is an **independent measurement of the same quantity**, and the identity should be evaluated against it — a residual computed against vendor truth is evidence; one computed against ourselves is arithmetic. In `jsonl_only` the self-check is all there is, and any reported residual must say which referee it used.

### 5.3 Contribution vs. carry — the two views

- **Contribution:** tokens each origin *added* to context (above). Answers "what bloated the session".
- **Carry (the actionable one):** each contributed token is re-read on **every subsequent turn** (`cache_read` at best, re-written post-compaction at worst). `carry(x) ≈ contribution(x) × turns_remaining_after(x)`, priced at cache-read rates with cache-write repricing after each compaction/TTL expiry. A 20k-token file read at turn 2 of a 50-turn session costs ~48× its face value in carried reads. **Carry is what makes "trim your MCP servers" a dollar figure instead of advice.**
- **Cache decay (J, cross-cutting):** inter-turn gap > TTL of the `cache_write_detail` bucket ⇒ the next turn re-writes what was already paid for. Exact from timestamps + TTL split. Priced as `re-written tokens × (write − read) price delta`.

Calibration: the ÷4 chars-per-token estimate self-calibrates — fit the ratio per session by regressing estimated increments against actual `appended_i` (which we know exactly). Sessions give us thousands of (chars, tokens) pairs; R absorbs the noise.

**Output shape** (`info.provenance`): `{by_origin: {A: t, B: t, …, K: t, R: t}, carry_by_origin: {…}, files_top: [{path, first_read, re_reads, carry}], mcp_by_server: {server: {schema_est, results}}, calib_ratio, residual_pct, residual_basis: "otel" | "jsonl"}`.

### 5.4 Validation gate

Before any UI work on this widget: run the decomposition offline against `out.json` + the recorded fixtures (`tests/fixtures/normalizer/recorded/`, incl. the 27-task session) and check R. **If p50 residual ≤ 10%, ship; if not, we learn which source is unaccounted while it's cheap.** (This is Fidelity-Advantage §6, now with a concrete home.)

**Revised gate (2026-08-06).** Run it against OTel truth wherever the fixture has both sources — the 2026-08-02 reconciliation session does. That is the version of this gate that can actually fail for the right reason. Report both numbers when both exist (`residual_basis`), because a decomposition that reconciles against itself but not against the vendor has found exactly the coverage gap this doc now calls K.

One caution for whoever runs it: **the ≤10% figure belongs to this gate and nowhere else.** It measures how much session context we can attribute to an origin. It is not a token-accuracy figure, not a cost-accuracy figure, and must never be borrowed as one in user-facing copy — see the measured numbers in modes doc §5.1.

---

## 6. Actionable-insights catalog

Each insight = {evidence, action, $ estimate}. Ranked by estimated recoverable spend. Phase 1 = computable from §7 additions; Phase 2 needs cross-session baselines; Phase 3 blocked on deferred subsystems.

**Mode column (added 2026-08-06).** Which datasource modes an insight can be computed in. An insight is not rendered in a mode it cannot be computed in, and is never shown as "0 found" there — absence of signal is not absence of the phenomenon, and the difference is the whole point of the modes doc §5 state.

| # | Insight | Signal | Action shown to user | $ estimate | Mode | Phase |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| 1 | **File re-read waste** | F2 ÷ (F1+F2) high; `files_top` re_read counts | "These 5 files were re-read 47× across sessions — add a summary to CLAUDE.md / keep sessions shorter" | F2 carry × price | jsonl, both | 1 |
| 2 | **Unused MCP servers** | server attached (C > 0) but zero `mcp_server` tool calls in N sessions | "Server X cost ~$N in schema overhead, 0 calls in 30d — detach or lazy-load" | C carry × price | jsonl, both | 2 (needs C baseline) |
| 3 | **Compaction churn** | `compaction_count ≥ 2`; reclaimed ÷ total high; I re-growing between compactions | "Split this workflow; start fresh sessions per task instead of one mega-session" | re-written context × price | jsonl, both | 1 |
| 4 | **Cache decay** | J events: gap > TTL then re-write | "You paid cache-write twice for the same context — batch your interactions or use the 1h tier" | J repricing delta | jsonl, both | 1 |
| 5 | **Retry burn** | `info.errors`: retry_count × turn tokens on 429/529 | "N% of spend hit rate limits — stagger parallel subagents / check org limits" | retried-turn tokens × price | jsonl, both | 1 |
| 6 | **Verbose tool output** | F3 p95 `bytes_read` per call ≫ median; uncapped Bash | "Bash output averages 180KB/call — cap output, pipe through head/grep" | excess bytes ÷ 4 × carry | jsonl, both | 1 |
| 7 | **Runaway/thrash turns** | long turn + high tokens + `files_changed == 0` + repeated same-path tool calls | "Turn 7 burned 900k tokens with no file progress — interrupt earlier; consider plan mode" | thrash-turn tokens | jsonl, both | 1 |
| 8 | **Sub-agent inefficiency** | child tokens ≫ fold-back G size; duplicate purposes among siblings | "4 subagents re-derived the same context — pass findings explicitly / reduce fan-out" | child tokens − useful G | jsonl, both | 1 |
| 9 | **Session hygiene** | tokens/turn rising with session age; compactions correlate | "Sessions >2h cost 2.3× more per turn — start fresh after each task" | delta vs user's own fresh-session baseline | jsonl, both | 2 |
| 10 | **Oversized project memory** | B baseline ≫ peer/user median | "CLAUDE.md et al. inject ~9k tokens into every turn-1 — trim to the sections agents actually follow" | B carry × sessions | jsonl, both | 2 |
| 11 | **Model overkill** | frontier-model sessions whose tool/task mix matches cheap-model sessions | "These lookup-style sessions ran on Opus; Sonnet handled identical patterns at parity" | price delta × tokens | jsonl, both | 3 (needs M-04 classification) |
| 12 | **Dark waste** | edits in `files` never reaching a commit | "23% of generated code was discarded before commit" | orphaned-edit session share | jsonl, both | 3 (git correlator) |
| 13 | **Plan-mode leverage** | rework rate with vs without plan-mode turns | "Sessions starting in plan mode show 40% less edit churn" | churn delta | jsonl, both | 3 (plan_mode TODO) |
| **26** | **Background-call spend** 🆕 | K (§5.1) share of session cost; `by_query_source` rollup | "Title generation and prompt suggestions account for ~$N a month" — fact-first; the action is a settings pointer, and only if one exists | K cost, exact | **otel, both** | 1 |
| **27** | **Model mix by request kind** 🆕 | ledger `model` × `query_source` × `duration_ms` | "Your interactive turns and your background calls run on the same model" | price delta × background tokens | **otel, both** | 1 |

Insights 26–27 are the first two that are **not** JSONL-derived, and they matter out of proportion to their size: they are what an `otel_only` user gets on day one, computed from the ledger alone. Both are exact rather than estimated, which no JSONL-derived insight can claim.

Insight 12's phase is unchanged. An accept/reject signal does exist in the OTel `tool_decision` log event and would give a dark-waste proxy without the git correlator — but that event is dropped by v1's filter and belongs to the v2 logs seam (§2), so nothing here may depend on it yet.

Insight records: `{insight_id, kind, evidence: {…}, action_text, est_tokens, est_usd, sessions: […], first_seen, status}` — persisted so users can dismiss/track, and so we can later measure whether actions were taken (the beginning of the coaching loop).

**Extension:** insights 14–25 — lapses against the published best-practice canon (Anthropic's Claude Code guidance), plus the per-user practice-adherence correlation engine and the practice scorecard widget — are specified in `best-practice-insights.md`. Insights 3/9/10 above merge with its 21/22/24 at implementation time (same phenomena; cost framing + practice citation on one card).

---

## 7. Consolidated `session_info` additions (the fold work)

Scalar columns (sort/filter keys — model + dataclass edits, no migration yet, same as worker plan §4):

```python
tokens_total:     Mapped[int] = mapped_column(default=0)   # promoted from info JSONB
compaction_count: Mapped[int] = mapped_column(default=0)
subagent_count:   Mapped[int] = mapped_column(default=0)
error_count:      Mapped[int] = mapped_column(default=0)

# added 2026-08-06 — written by the true-up worker, not the JSONL fold (modes doc §3.2)
cost_effective_usd_micros: Mapped[int | None]   # the dashboard's cost; sortable
datasource:                Mapped[str]          # both | jsonl_only | otel_only
jsonl_lag_s:               Mapped[int | None]   # null when not applicable
```

Note the ownership split: the first four are JSONL-fold outputs, the last three are the true-up worker's. That is the same boundary the `otel` column exists to enforce (`session_info.py`) — the JSONL fold replaces `info` wholesale on every arrival, so anything a second writer owns lives outside it.

JSONB shape additions (no column changes):

| Key | Shape | Feeds |
| :---- | :---- | :---- |
| `info.title` | from `ai-title` passthrough | §3.4 list, §4.1 header |
| `info.repo` | `{cwd, git_branch, repo_key, repo_id, repo_id_provenance, repos}` — the flat fields describe the primary project (also promoted to `session_info` columns, the join key); `repos: [{repo_id, repo_id_provenance, repo_key, refs, primary}]` is the ranked, multi-project list (R1.3 — sessions are multi-project, measured 8/46 touching two or three roots), weighted by `tool_call.files[]` references. `pr_link`/`remote` evidence is session-wide and can only ever upgrade the primary entry; every secondary entry is `path`-tier, found via `git_ident.sibling_project_root` on a file path outside the primary `cwd` | repos widgets, R8's `repo-chips.mjs` |
| `info.compactions` | `[{ts, trigger, before, after, reclaimed, duration_ms}]` | 🗜 badge, deep dive, insight 3 |
| `info.errors` | `{by_class: {…}, retry_count, retried_tokens_est}` | ⚠ badge, insight 5 |
| `info.cache` | `{hit_ratio, writes_5m, writes_1h, decay_events: [{turn, gap_s, ttl_s, tokens}]}` | ❄ badge; `decay_events` is the raw signal `info.resume_cost` below tightens into a priced finding |
| `info.carry` | `{cache_read_tokens, carry_usd_micros, peak_context, mean_context, requests}` (R2 — replaces the parked provenance-by-origin work) — exact, not estimated: context size per request is `cache_read_tokens + cache_write_tokens` off each `assistant_message`'s own usage block; `carry_usd_micros` prices `cache_read_tokens` at the model's own cache-read rate (`common.model_pricing.Pricer`), summed. `None` when no `Pricer` was available to the fold that produced this row — the token/request figures stay exact regardless, only the dollar conversion degrades. Attributes to time, not origin (R2.3): no per-source breakdown | insight 28, R7's `cost.by_channel` |
| `info.resume_cost` | `{events: [{turn, gap_s, ttl_s, context_before, reload_tokens, usd_micros}], count, total_usd_micros}` (R3 — resume-after-break cost) — stricter than a bare `info.cache` decay event: only counted when the turn that follows a stale-TTL gap re-caches at least half of what was live before it. Priced at the write-minus-read delta per contributing write (what the expired cache cost extra, over what re-reading it would have). `total_usd_micros` is `None`, not partial, on the same all-or-nothing terms as `info.carry` | insight 4 (subsumes the old catalog's cache-decay card), R7's `findings` |
| `info.segments` | `[{index, t0, t1, turns, files, beats: {kind: count}, carry_usd_micros, cost_usd_micros, label, confidence}]` (R4+R5 — beats find the boundaries, R4.2; each span gets its own Tier 0 type, R5.2) — root-session only, computed at fold time (not read-path as session-storytelling.md §4's original draft specified: segments persist and carry priced cost, which needs a stable once-computed span). Beats are session-storytelling.md §4's eleven kinds, `confidence: "fact"` for ten of them and `"inferred"` for `revision_loop`. Segment boundaries are the *strong* beats only (`context_squeeze`, `wrap_up`, `delegation`, and `pause` past a higher bar than the beat-level one) — every beat-level pause would over-segment. `label` is one of the eight Task-Classification-and-Analysis.md §2 types or `MISC` (never forced, R5.3); `confidence` is a float, not fact/inferred — R8's open question 3 picks the render threshold, not this pass. Cost is priced fresh per segment on the same all-or-nothing terms as `info.carry`/`info.resume_cost` — `None`, not partial, when unpriceable. No narrator line, no acts prose (revised-scope plan §2) | insights 4/11, R7's `segments` block |
| `info.read_then_discard` | `{events: [{compaction_ts, bytes_read, est_tokens}], total_bytes_read, total_est_tokens}` (R6.1) — `bytes_read` (any tool, from its paired result) within a config window before a `compaction` event counts as read-then-discarded. Token estimate only (`bytes_read // 4`, insight 18's own convention) — no `Pricer`, R6 is deliberately lighter-weight than R2/R3's per-token-priced engines | insight 29 |
| `info.interrupted` | `{events: [{ts, tokens}], count, total_tokens}` (R6.3) — "input paid, output discarded": the assistant_message immediately before an interrupt sentinel, billed in full for a response the user then threw away. Token-only, not priced, same posture as `info.read_then_discard` | insight 31 (reported as a fact, `est_usd=0` — not recoverable; interrupting early is usually the cheaper choice) |
| `info.segments` | `[{index, t0, t1, turns, files, beats: {kind: count}, carry_usd_micros, cost_usd_micros, label, confidence}]` (R4+R5 — beats find the boundaries, R4.2; each span gets its own Tier 0 type, R5.2) — root-session only, computed at fold time (not read-path as session-storytelling.md §4's original draft specified: segments persist and carry priced cost, which needs a stable once-computed span). Beats are session-storytelling.md §4's eleven kinds, `confidence: "fact"` for ten of them and `"inferred"` for `revision_loop`. Segment boundaries are the *strong* beats only (`context_squeeze`, `wrap_up`, `delegation`, and `pause` past a higher bar than the beat-level one) — every beat-level pause would over-segment. `label` is one of the eight Task-Classification-and-Analysis.md §2 types or `MISC` (never forced, R5.3); `confidence` is a float, not fact/inferred — R8's open question 3 picks the render threshold, not this pass. Cost is priced fresh per segment on the same all-or-nothing terms as `info.carry`/`info.resume_cost` — `None`, not partial, when unpriceable. No narrator line, no acts prose (revised-scope plan §2) | insights 4/11, R7's `segments` block |
| `info.mcp_servers` | attached-set + per-server call/byte counts | insight 2, §3.3 |
| `tools[name]` | add `bytes_read_total`, `duration_ms_total` | §4.2, insight 6 |
| `skills[i]` | add `args_gist` (first ~80 chars of Skill args) | "skills for what" |
| `info.provenance` | §5 output shape | §3.2 mix, §4.2, insights 1/2/6/10 |
| `info.story` | `{curve: [[t, cum, ctx] × ≤64], shape, anchors}` (storytelling doc §6) | glyphs everywhere, S4 header, anchor chips |
| `info.practices` | adherence vector (best-practice doc §4.1) | scorecard, insights 14–25 |

All are pure functions of the full deduped blob — idempotency is preserved by construction, matching the existing fold contract.

### 7.1 True-up worker additions (2026-08-06)

Not fold work — these belong to `otel_trueup.py` and land in the `otel` block or the promoted columns above.

| Key | Shape | Feeds |
| :---- | :---- | :---- |
| `otel.by_query_source` | `{source: {requests, tokens: {…}, cost_usd_micros, cost_est_usd_micros}}`; `null` key = interactive | §3.2 widget, provenance K, insights 26–27 |
| `measures.active_time_s` | already mapped; surface it | §3.1 active-time tile |
| query_source-split measures | `tokens_input_bg` etc. — the `query_source` label is on `token.usage`/`cost.usage` datapoints | cross-check on `by_query_source`; the only background-spend path in a metrics-only fleet |
| stub `sessions` row | modes doc §8 | makes `otel_only` visible to every endpoint at all |
| `datasource`, `jsonl_lag_s` | modes doc §3.2, §7 | mode-aware UI, lag treatment |

The ledger rollup and the metrics rollup measure the same thing two ways and should be kept side by side rather than reconciled, for the same reason `build_cost_block` already keeps `measures` and `requests` apart: where they disagree, the disagreement is the signal.

---

## 8. API surface (new read-only module: `src/server/api`)

Thin query service over storage; SQL lives in `config/queries/dashboard.yaml` per the existing pattern; entry point `cli_serve_api.py` (naming rule: no bare `cli.py`). No writes, no fold coupling.

| Endpoint | Returns | Backing |
| :---- | :---- | :---- |
| `GET /v1/users/{id}/overview?from&to` | headline strip + model/cost split + provenance mix + top tools/skills/mcp/repos | aggregate over `session_info` (+pricing) |
| `GET /v1/users/{id}/sessions?from&to&sort&flags&limit` | flagged/sortable session list (title, repo, badges, totals) | scalar columns + `info.title/repo` |
| `GET /v1/sessions/{id}` | metrics panel (§4.2) | one `session_info` row (+pricing) |
| `GET /v1/sessions/{id}/timeline` | condensed ordered events for the graph: `[{ts, kind, lane, dur, tokens?, label, ref}]`, subagent lanes nested, **plus `story: {narrator, acts[], beats[]}`** (read-path beat detection, storytelling doc §4–§6) | `signal_blob.events` transform |
| `POST /v1/sessions/{id}/feedback` | "mark as expected" on an inference beat/insight: `{beat_ref, verdict}` | feedback table (storytelling doc §6) |
| `GET /v1/sessions/{id}/provenance` | §5 output, contribution + carry | `info.provenance` |
| `GET /v1/users/{id}/insights?from&to` | ranked insight records | insights store (start: computed-on-read; persist when dismiss/track lands) |
| `GET /v1/pricing/models` | active price table (UI renders $ client-side consistently) | `common.model_pricing` catalog via `Pricer` (#68) |

Timeline condensation rule: cap payload (~2k entries) by collapsing runs of same-tool calls into one entry with `count`; the UI can fetch uncollapsed ranges on zoom (`?from_ts&to_ts`).

### 8.1 Mode contract on every response (2026-08-06)

Three rules, binding on all endpoints above:

1. **Every response carries mode.** Session-scoped responses carry `datasource` and `vendor`; aggregate responses carry `{modes_present: [...], sessions_by_mode: {...}}` for the timeframe. The UI cannot choose between the `unavailable` and cold-start states without this, and choosing wrong means either nagging a user about something they cannot fix or promising data that will never arrive.
2. **Every number carries its source.** Any figure resolved through the precedence chain (§3.1) reports which source won; any dollar reports its `cost_basis` and the session's `cost_complete`. A figure whose provenance the UI cannot name is one it cannot caption honestly.
3. **A capability is absent, not empty.** An endpoint that cannot compute a block in the caller's mode returns `null` for it plus a machine-readable reason (`{unavailable: {block: "provenance", requires: "jsonl"}}`) — never `0`, never `[]`. Zero is a measurement; null-with-a-reason is the truth.

Two endpoint changes fall out:

- `GET /v1/sessions/{id}/timeline` **404s in `otel_only`** — there is no blob to condense. The sessions list already withholds the link (BLUE §3); the endpoint says so honestly for anyone who guesses the URL.
- `GET /v1/users/{id}/overview` gains `by_query_source` and the lag rollup, and its `{id}` remains ambiguous across identity spaces until the uploader is configured with the same user and org identifiers the OTel attributes carry — the operational requirement in modes doc §8, and a real failure mode if it is skipped.

---

## 9. Phasing

1. **P0 — pricing table** (small, unblocks every $ figure): `config/pricing.yaml` + query-time pricing helper (since replaced by the `common.model_pricing` catalog and `Pricer`, #68).
2. **P1 — fold additions** (§7 scalars + title/repo/compactions/errors/cache/mcp/tool-byte rollups) + shape-contract golden updates.
3. **P2 — API module** with overview/sessions/detail/timeline (everything but provenance) — the dashboard is demoable here.
4. **P3 — provenance offline validation** against `out.json` + recorded fixtures (the §5.4 gate), then `info.provenance` in the fold + the two provenance widgets.
5. **P4 — insights** 1, 3–8 (Phase-1 set), computed-on-read.
6. **P5 — UI polish**: journey narrative, dismiss/track on insights, cross-session baselines (insights 2, 9, 10).

Each phase is independently testable against the recorded fixtures through the real normalizer → `EconFold` path, same as worker plan §7.

## 10. Open questions

1. **Where does the UI live?** This doc specifies data + API; the front-end stack (and whether v1 is a static SPA against `src/server/api` or something embedded) is unspecified — needs a decision before P2 ends.
2. **Baseline scope for C (MCP schema estimate):** per-user differencing is cleanest but slow to converge for users with static toolsets; org-level baselines converge faster but leak cross-user info into a personal view. Proposal: per-user first, org fallback when the org dashboard lands.
3. **Insight persistence timing:** computed-on-read is fine until dismiss/track ships; deciding the insights table schema early would avoid rework in P4→P5.
4. **`turn_duration`/`isMeta` promotion:** the provenance extractor reads raw passthroughs (H category) — third consumer of raw-digging after turns and skills. Worker plan §8 said promote "only if a second consumer needs them"; we're now there. Recommend promoting `is_meta` + injection markers to typed fields in the normalizer as part of P1.
5. **Identity across sources (2026-08-06).** v1 requires the JSONL uploader to be configured with the same user/org identifiers the OTel attributes carry (modes doc §8). It is an operational requirement with a silent failure mode — misconfigure it and every developer in a hybrid org splits in two, with no error anywhere. A mapping table is the obvious v1.1 fix; worth deciding whether a startup-time consistency check is cheaper than the support burden.
6. **Does the provenance carry model (§5.3) hold under K?** Carry assumes a token re-read on every subsequent turn. Background utility calls are not turn-scoped and do not persist into context at all, so K should have **zero carry** — its contribution is its whole cost. Confirm against a hybrid fixture before the carry figures ship; getting this wrong overstates K by the session's turn count.
