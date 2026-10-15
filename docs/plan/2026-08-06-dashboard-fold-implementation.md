# Phase P1 — Fold Additions: Implementation Plan

**Goal:** compute everything the dashboard reads out of `session_info` — the sort/filter scalars, the event rollups, the story curve, and the practice-adherence vector — inside `EconFold`, idempotently, from the full deduped blob.

**Position:** P1 is what PM and P2 both stand on. `info.story.curve` feeds every glyph and the personal band; `info.practices` feeds the scorecard; the scalars feed every sort in the sessions list. It is also the phase with the most empirical unknowns, and F7 keeps those from blocking the rest.

**Spec set:**
- The additions table and output shapes: `docs/design/user-dashboard-design.md` (**DASH**) §7
- Story rollup shape, shapes taxonomy: `docs/design/session-storytelling.md` (**STORY**) §5, §6
- Practice vector and its fingerprints: `docs/design/best-practice-insights.md` (**PRACT**) §2, §4.1, §5
- Existing fold contract and conventions: `docs/plan/2026-07-27-worker-session-analytics.md`, `src/server/worker/fold_api.py`
- Ground rules: `docs/plan/2026-08-01-user-dashboard-implementation.md` (**PLAN**) §0

**Status:** planned 2026-08-06. Schema changes are SQLAlchemy-model + fold-dataclass edits; no migration yet.

---

## 0. Ground rules specific to the fold

PLAN §0 applies — §0.1 (idempotency) and §0.2 (no content) are the load-bearing two here. Four more:

1. **Recompute, never accumulate.** `_recompute` rebuilds every field from the full event list on each pass (`driver.py` docstring). A running `+=` would make a replayed fold wrong rather than merely slow. Every addition below is a pure function of `events`.
2. **Vendor-aware raw reads live only in `extract_claude_code.py`.** `driver.py`, `metrics.py` and `turns.py` must never touch `payload.raw`. Several additions here (`ai-title`, plan-mode markers, `<command-name>` wrappers) are raw-shaped, so they arrive as new extractor methods, not as inline dict-digging.
3. **Anything `_recompute` produces must also survive `rollup_subagents`** — see F0.2. This is the rule the current code breaks.
4. **Config over constants.** Every threshold named in the specs (`dashboard.verifiers`, `dashboard.review_agent_types`, `worker.story.*`, `fold.long_turn_threshold_s`) lives in `config/worker.yaml` through `FoldConfig`.

---

## F0 — Fix two existing defects first

Both were found while planning this phase. Neither is visible today; both become live the moment P1 lands, and F0.2 would corrupt the very fields F1 adds.

### F0.1 — `session_info.files` is never computed

`_recompute` (`driver.py:148`) returns models, tools, skills, turns, counts and info. **It never computes `files`.** `file_change` events are read in exactly one place — `turns.py:130`, which increments a per-turn `files_changed` counter — and `SessionInfo.files` keeps its `{}` default forever.

DASH marks "Files touched" ✅ in §3.1 and §4.2. It is not. Everything resting on it is resting on an empty dict: the Z1 tile, the §4.2 Files row, STORY §3.6's `$/file changed` per-unit line, and insight #12's evidence. The subagent rollup even stores `child.files` (`driver.py:120`) — always `{}`.

- [ ] `metrics.file_totals(events) -> dict` — `{path: {changes, lines_added, lines_removed, first_seen, last_seen, ext}}` from `file_change` payloads (hunks, lines ±).
- [ ] Wire into `_recompute`'s return and into `fold()`'s `SessionInfo`.
- [ ] Created-vs-edited split, which Z1 asks for: derive from whether the path's first `file_change` is a creation, per the normalizer's payload. Confirm the field exists before promising the split; if it does not, ship the count and leave a `TODO(dashboard)` rather than guessing.
- [ ] Test against a recorded fixture with known file activity.

### F0.2 — `rollup_subagents` silently resets fields it does not pass

`persist_session_info` (`persistence.py:58-69`) assigns every column field-by-field from the `SessionInfo` it is handed. `rollup_subagents` (`driver.py:135`) constructs its `SessionInfo` with **models, tools, skills, subagents, turns, turn_count, long_turn_count, info** — and nothing else. `files`, `pr_list` and `cost_usd` take dataclass defaults and overwrite whatever the fold wrote.

The comment at `persistence.py:49-52` asserts this is safe because "rollup recomputes the owning session's own fields from its blob". That holds only for the fields `_recompute` actually returns. It is harmless today because `files` is always empty anyway (F0.1) — and it stops being harmless the moment F0.1 and F1 land.

**Specifically: F1's four new scalar columns will be zeroed by any rollup pass.** Rollup runs for sessions with subagents — which are exactly the 🌿 fan-out sessions the dashboard flags as interesting. The bug would present as "my most complex sessions all report 0 tokens", intermittently, only after a rollup.

- [ ] Make `rollup_subagents` pass through **every** field `_recompute` produces. The durable fix is to stop hand-listing: build the `SessionInfo` from `own` the way `fold()` does (`SessionInfo(session_id=..., **own)`) and then override only what rollup genuinely adds — `subagents`, the enriched `turns`, and `info`'s `inclusive_tokens`.
- [ ] Correct the `persistence.py` comment to say what is actually true.
- [ ] **Regression test:** fold a session with subagents, assert a field set; run `rollup_subagents`; assert it survived. Parameterize over every `session_info` field so a future addition cannot reintroduce this.

**Done when:** `files` is populated for a fixture session and survives a rollup pass.

## F1 — Scalar columns

**Edit:** `src/server/storage/schema/session_info.py`, `fold_api.py`, `driver.py`

- [ ] Add `tokens_total`, `compaction_count`, `subagent_count`, `error_count` (DASH §7), plus their `SessionInfo` dataclass mirrors.
- [ ] `tokens_total` is `info.tokens_total` promoted. **Keep one source of truth** — `metrics.tokens_total(models)` — and set both from it. Two independently computed copies of the same number will disagree eventually, and the JSONB one is already load-bearing for the subagent rollup (`child.info["tokens_total"]`).
- [ ] `subagent_count` = `len(subagents)`. Note this is only correct after rollup for a parent; on a plain fold it is the spawn count from events. Pick one definition, write it in the column comment, and make the two paths agree.
- [ ] These are PM's neighbours in the same table but a different writer — do **not** touch PM's `cost_effective_usd_micros` / `datasource` / `jsonl_lag_s`, and confirm `persist_session_info` still omits them (PM ground rule 3).
- [ ] Indexes on the sort keys.

**Done when:** all four populate, survive a rollup (F0.2), and match their JSONB equivalents.

## F2 — Event rollups

**Edit:** `metrics.py`, `extract_claude_code.py`, `driver.py`

All from DASH §7's table. The typed events carry what is needed already — verified against the normalizer.

- [ ] **`info.compactions`** — `[{ts, trigger, before, after, reclaimed, duration_ms}]`. Straight from the `compaction` event payload (`claude_code.py:527`), which already carries `trigger`, `tokens_before`, `tokens_after`, `tokens_reclaimed`, `duration_ms`. Feeds the 🗜 badge, insight 3 and PRACT's 22 (`trigger` distinguishes auto from manual).
- [ ] **`info.errors`** — `{by_class: {...}, retry_count, retried_tokens_est}`. From `api_error` payloads (`claude_code.py:507`): `error_class`, `http_status`, `retry_count`, `retry_after_ms`.
- [ ] **`info.cache`** — `{hit_ratio, writes_5m, writes_1h, decay_events: [{turn, gap_s, ttl_s, tokens}]}`. Hit ratio from `models`; the TTL split from `assistant_message.cache_write_detail`; decay events from inter-turn gap > the bucket's TTL.
- [ ] **`info.repo`** — `{cwd, git_branch, repo_key}` from `session_start` (`claude_code.py:346`, which has `cwd` and `git_branch`). `repo_key` = terminal path segment until the normalizer's `repo_id` TODO lands. The normalizer's own comment already notes cwd/gitBranch are not a stable cross-machine fingerprint — carry that caveat into the column comment so nobody treats `repo_key` as an identity.
- [ ] **`info.title`** — from the `ai-title` passthrough. It arrives as an `unknown` event with only `raw` (`claude_code.py:547`), so this is a **new extractor method**, not a driver read (ground rule 2).
- [ ] **`info.mcp_servers`** — attached set plus per-server call and byte counts, from `tools[].mcp_server`.
- [ ] **`tools[name].bytes_read_total` / `duration_ms_total`** — extend the existing accumulator in `metrics.tool_totals`.
- [ ] **`skills[i].args_gist`** — first ~80 chars of Skill args. **The one place in this phase that touches content.** It is a deliberate, bounded exception (DASH §7) so "skills for what" is answerable; keep it to the cap, and never extend the same reasoning to Bash commands or user messages.

**Done when:** each rollup is populated from a recorded fixture and each has an idempotency assertion.

## F3 — Story rollup

**New:** `src/server/worker/fold/story.py`

- [ ] `info.story = {curve: [[t_rel_s, cum_tokens, ctx_tokens] × ≤64], shape, anchors: {superlatives: []}}` (STORY §6).
- [ ] **Curve:** cumulative consumption and live context size against elapsed seconds, downsampled to ≤64 points. Downsampling must be **deterministic** — the same blob must give byte-identical points, or every glyph flickers between folds and the idempotency assertion fails. Pick a fixed-stride or largest-triangle rule and state it in the docstring.
- [ ] **Shape** classification per STORY §5's six archetypes (short spark, steady climb, staircase, sawtooth, cliff, long tail), from curve geometry, thresholds under `worker.story.*`. Names are geometry, never quality — a shape must not read as a grade.
- [ ] **`anchors.median_ratio` is NOT stored** (STORY §6). It is computed at query time from the user's history, because the median moves as the user accumulates sessions and a stored ratio would silently go stale. Only self-contained superlatives persist.
- [ ] Context size (`ctx_tokens`) resets at each compaction boundary — that is what produces the sawtooth, so it must come from the compaction events rather than from a monotonic sum.

**Done when:** the recorded fixtures classify into distinct shapes, and re-folding produces byte-identical curves.

## F4 — Practice-adherence vector

**New:** `src/server/worker/fold/practices.py`

`info.practices` per PRACT §4.1 — booleans and small ints, all metadata-derived.

- [ ] Ships now (fingerprints ✅ in PRACT §2): `verifier_runs`, `verifier_pass_rate`, `exitplan_count`, `first_prompt_chars`, `at_refs`, `delegation_ratio`, `subagent_research`, `review_subagent`, `interrupts`, `manual_compactions`, `auto_compactions`, `fresh_session`.
- [ ] **Verifier detection is the content-sensitive one.** Bash `input.command` is matched at fold time against the `dashboard.verifiers` regex list and **only the boolean and the counts persist** (PLAN §0.2, PRACT §2 note 2). The command string must not reach `session_info` — not in a sample, not in an "unmatched commands" debug key.
- [ ] `interrupts` reuses the sentinel already pinned in `turns.py`. Do not re-derive it.
- [ ] `review_subagent`: spawn `agent_type` ∈ `dashboard.review_agent_types` occurring **after** the first `file_change`. The ordering is the signal — a reviewer that ran before any code existed reviewed nothing.
- [ ] Config: `dashboard.verifiers`, `dashboard.review_agent_types` in `config/worker.yaml` via `FoldConfig`.
- [ ] **Applicability:** this vector is Claude Code only (PRACT's applicability gate). Compute it only for `claude_code` sessions; leave it absent, not empty, for other vendors — absent and zero mean different things to the scorecard.

**Done when:** the vector populates for the recorded fixtures and no command text appears anywhere in the output.

## F5 — Flags stay at query time (a non-task, recorded so nobody "fixes" it)

- [ ] **Do not store badge booleans.** Flags are derived by the API from scalars + `dashboard.flags.*` thresholds (DASH §3.4, PLAN P1.5). Storing them would mean a refold every time a threshold is tuned — and thresholds are exactly the thing that gets tuned.

## F6 — Deferred: the 🔬 empirical pinning tasks

Each needs a recorded fixture before its marker can be trusted (PRACT §5). **Ship P1 without them** — the alternative is blocking the whole phase on data collection.

- [ ] Leave `TODO(dashboard)` markers and **null fields, not `false`**, for: plan-mode spans (`mode` / `permission-mode` raw shapes), `/clear` and `/compact` `<command-name>` wrapper shapes, rewind/branch DAG signature.
- [ ] Null vs false matters: `plan_mode: false` asserts the user did not plan; `plan_mode: null` says we cannot tell. Insights 16, 17, 20 and 13 hang off this distinction, and a false negative here is the kind of wrong that reaches a user as a suggestion they did something they did not.
- [ ] Each lands later with its fixture and unlocks its insights. Remove the TODO when it does (CLAUDE.md workflow rule 4).

## F7 — Tests and the shape contract

- [ ] Per-rollup tests against `tests/fixtures/normalizer/recorded/` (including the 27-task `session-turns` set) through the **real normalizer → EconFold** path, per the worker plan.
- [ ] **Idempotency assertion on every new rollup** (PLAN §0.1): fold twice, assert byte-identical `session_info`. Add it to each test rather than as one shared test — the additions take different code paths.
- [ ] **The F0.2 rollup-survival test, parameterized over every field.** This is the one that stops the defect coming back.
- [ ] Extend the `session_info` shape-contract golden, or create it if absent (still open from worker plan §7). Update it deliberately, never regenerate it blindly.
- [ ] Assert no content leaks: no Bash command text, no user-message text, `args_gist` within its cap.

**Done when:** `pytest tests/server/worker` green and a folded `session-turns` fixture shows populated `files`, `story`, `practices` and all four scalars.

---

## Sequencing

```
F0 ──► F1 ──┬──► F2 ──► F4
            └──► F3
                        F7 alongside; F5 is a rule; F6 is TODOs
```

- **F0 first, always.** F0.2 would corrupt F1's scalars, and finding that after four more rollups exist is much worse than fixing it now.
- F2, F3 and F4 are independent of each other once F1's scalars exist.
- Suggested PRs: F0, F1, F2, (F3+F4), with F7's tests inside whichever PR introduces the behaviour.

## Out of scope, recorded

- **Provenance** (`info.provenance`) is P4, behind its own validation gate. F2's `info.cache` and `info.mcp_servers` are inputs it will read; nothing here computes attribution.
- **`is_meta` / injection-marker promotion to typed normalizer fields.** DASH §10.4 recommends it as part of this phase on the grounds that provenance would be the third raw-digging consumer. Provenance is P4, so the second consumer does not exist yet — revisit when it does rather than promoting speculatively.
- **PM's columns.** Different writer, different phase. The only thing P1 owes them is not touching them.
- **Git correlator fields** (`pr_list`, rework) — P6.
