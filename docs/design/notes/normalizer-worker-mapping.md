### 3.4 Field → Metric Provenance (normative)

Workers, not the normalizer, compute metrics — but the normalizer produces their **only raw
material**, and it has the most context to know what each field is *for* (it is the stage
reading the vendor records). So every metric-bearing field is tagged here with the downstream
metric it feeds and the worker aggregation over a session's events (metric registry:
architecture §9). This is a contract in two directions: the normalizer **must not drop** a
field some metric depends on, and any field left `null` that a metric consumes is a tracked
**gap** (§13), never a silent omission.

| Metric family | Source event field(s) | Worker aggregation (over a session's events) | Normalizer status |
|---|---|---|---|
| **Token spend & cost** | `assistant_message`: input/output/cache_read/cache_write tokens, `cache_write_detail`, `server_tool_use`, `model` | sum per session × model; cost engine prices (TTL-aware cache writes, cache-read ~0.1×, server tools as line items) | **populated** |
| **Reasoning spend** | `assistant_message.reasoning_tokens` (a split of `output_tokens`) | sum per session → reasoning share of output | **GAP** — null now; derive here from thinking-block size (this stage holds the raw blocks; §13 Q-reasoning) |
| **Cache efficiency / waste** | `cache_read_tokens`, `cache_write_tokens`, `cache_miss_tokens` | cache-hit ratio; `cache_miss_tokens` = recoverable waste (M-05) | read/write **populated**; `cache_miss_tokens` **GAP** (not in CC) |
| **Model mix** | `assistant_message.model`, incl. each subagent session's own model | per-model token/cost split | **populated** — subagents carry their own model (§6) |
| **Tool usage & success** | `tool_call`: `tool_name`/`mcp_*`, `exit_status` | per-tool counts, top-K, success rate | **populated** (`exit_status` paired from result, §3.3) |
| **Files & churn** | `file_change` / `tool_call.files[]`: path, extension, operation, lines_added/removed, hunks | files touched, lines changed (churn), language dimension | **populated** (lines/hunks copied only, §3.2) |
| **Sub-agent economics** | `sub_agent_spawn.{agent_type, child_session_id}` + the child session's token fields | subagent cost rolled up to parent via `child_session_id`; fan-out | **populated** (§6) |
| **Reliability (M-03)** | `api_error.{error_class, http_status, retryable, retry_count}` | `error_counts`, `retry_total`; anomaly alerting | **populated** when CC emits an api_error record |
| **Context waste** | `compaction.{tokens_reclaimed, tokens_before/after, trigger}` | `compaction_count`, `context_reclaimed_tokens` | **populated** when CC emits a compact_boundary |
| **Behavior / rework (classifier)** | `tool_call.input` (identical-call), `exit_status`; `file_change` lines/hunks; `sub_agent_spawn`; header `parent_id`/`seq` | no-progress loop, thrash, rework, anomaly (behavior graph) | **populated** — `input` carries content |
| **Velocity & dimensions** | header `event_ts`, `duration_ms`, `model` / org / user / producer | token velocity, session length; rollup windows & dimensions | **populated** |
| **Plan depletion / burst** | `plan_usage_snapshot.{utilization_pct, depleted, window_reset_ts}` | burst pricing (billed cost), depletion timeline | **out of scope here** — emitted by the plan-usage poller, not the file normalizer |

**Reasoning tokens — the worked example.** CC's `usage` carries no reasoning subtotal, so a
naive mapping leaves `reasoning_tokens` null and the session-level "reasoning spend" metric
aggregates to nothing. But the normalizer sees the assistant record's **thinking blocks** — the
raw reasoning content — so *this* stage, not the worker, is where the per-event value is best
derived (a token count, or a character proxy, over the thinking spans). The worker then only
sums. This is the general pattern for every gap: **fill the per-event field where the context
exists — here, the normalizer — and let the worker aggregate.** Leaving it to "the worker will
compute it later" fails, because by then the raw thinking blocks are gone (content lives in the
event, but the per-turn split is only reconstructable while the vendor record is in hand).

---