# Worker Session Analytics — Implementation Plan

**Goal:** Build the real fold/extraction worker (`src/server/worker`) that turns a session's
normalized event stream into `session_info` intelligence: token, subagent, tool, and skill
reports, plus turn extraction with long-turn classification. Replaces `StubFold`.

**References:** `docs/modules/ingest.md` §7–§8 (fold seam, subagent rollup),
`docs/modules/storage.md` §3.4 (`session_info` metric registry), `docs/modules/normalizer.md`
§6.11 (turn-identification gotcha).

**Status:** Planned. No database exists yet, so schema changes are edits to the SQLAlchemy
model + the fold dataclass only — **no Alembic migration in this phase.**

---

## 1. Confirmed decisions

1. **Turns get a dedicated `turns` JSONB column + scalar `turn_count` / `long_turn_count`
   columns.** (No migration now — model edits only; migration lands when the DB does.)
2. **Long-turn threshold = 30 min**, config-driven (`fold.long_turn_threshold_s: 1800`).
3. **Do not touch the normalizer.** All extraction reads `payload.raw`, which already carries
   every needed field (`turn_duration`, `isMeta`, `isSidechain`, Skill `caller`/`commandName`/
   `allowedTools`, tool `exit_status`). Promotion to typed normalizer fields is a deferred,
   optional follow-up.
4. **Turn boundaries must reject injected/agentic messages** (see §3).

---

## 2. Architecture

- **Extraction lives in the fold, reading `raw`** — iterate on metrics without re-collecting.
- **Vendor-aware extractor seam** — a `{vendor → FoldExtractor}` registry mirroring the
  normalizer's `registry`. CC's raw-shape knowledge (turn_duration, isMeta, Skill result) is
  isolated in `extract_claude_code.py`; Codex/Cursor add their own later. The rest of the fold
  is vendor-neutral.
- **Two computation sites** (existing `SessionFold` contract, `fold_api.py`):
  - `fold(session, events)` → **own-session** metrics: tokens, tools, skills, turns. Recomputed
    from the full deduped blob each pass (idempotent by construction).
  - `rollup_subagents(parent, children)` → **tree** metrics: the subagent report, from children's
    `session_info` + the parent's `sub_agent_spawn` events. Replace-by-`child_session_id`.
- **Turns are root-session only** — subagent sessions have no human turns (their user messages
  are injected task prompts, `isSidechain:true`). Turn logic runs when `parent_session_id is None`.

---

## 3. Turn-boundary rule (empirically pinned)

`turn_duration` system records are Claude Code's native "agent finished, waiting for human"
marker: they sit right before the next human message (only passive meta records in between) and
carry the turn's own `durationMs` + `messageCount`. But `turn_duration` is **absent** for the
first turn and for human interrupts, so it can't be the sole signal.

**A new human turn boundary =**
`type==user` ∧ `isMeta` falsy ∧ `isSidechain` falsy ∧ text content (not `tool_result`)
∧ not a `<command-name>` / `<local-command-stdout>` wrapper
∧ not a synthetic marker (`[Request interrupted by user]`).

**Turn duration =** the `turn_duration.durationMs` closing the turn; fall back to
`last − first event_ts` in the window when absent (first/interrupted turns).

Rejected as non-human (do **not** open a turn):

| Case | Marker |
|---|---|
| Skill bootstrap injection (`"Base directory for this skill: …"`) | `isMeta == true` |
| Subagent stream records | `isSidechain == true` |
| Tool / subagent fold-back results | `content` is a `tool_result` block |
| Slash-command expansion / local-command output | `<command-name>` / `<local-command-stdout>` wrapper |
| Interrupt sentinel | text `== "[Request interrupted by user]"` |

`turn_duration` reaches the fold today as an `unknown` event whose `payload.raw.subtype ==
"turn_duration"` (the normalizer passes system records through untouched).

---

## 4. `session_info` schema changes

JSONB shapes evolve in the worker with no migration (storage.md §3.4). Model edits only:

**`src/server/storage/schema/session_info.py`** — add:
```python
turns:           Mapped[jsl]                                  # per-turn records (see below)
turn_count:      Mapped[int] = mapped_column(default=0)       # real column: dashboard filter/sort
long_turn_count: Mapped[int] = mapped_column(default=0)       # real column: "sessions with long turns"
```
**`src/server/worker/fold_api.py`** — mirror the same three onto the `SessionInfo` dataclass.

Everything else is a JSONB **shape** change (no column change):

| Item | Column | Shape |
|---|---|---|
| 1. Tokens | `models` + `info.tokens_total` | `models = {model: {input, output, cache_read, cache_write}}` |
| 3. Tools | `tools` | `{name: {count, type, ok, failed, mcp_server?}}` |
| 4. Skills | `skills` | `[{skill_name, count, caller_type, success, allowed_tools, tokens, started, ended}]` |
| 2. Subagents | `subagents` | `{child_id: {tokens, duration_ms, purpose, agent_type, origin, depth, model, tools, files}}` |
| 5,6. Turns | `turns` | `[{index, started, ended, duration_ms, message_count, source, tokens, subagents_spawned, subagent_session_ids, files_changed, long}]` |

`cost_usd` stays null until a pricing engine runs — item 1 delivers token **counts**, not dollars.

---

## 5. Per-item data flow

| # | Report | Source | Site |
|---|---|---|---|
| 1 | Tokens | `assistant_message` usage fields | `fold` |
| 3 | Tools | `tool_call`: name→type, `exit_status`→ok/failed, `mcp_server/tool` | `fold` |
| 4 | Skills | `tool_call` where `tool_name=="Skill"` + result `{commandName, allowedTools, success}`, `caller.type` from raw | `fold` (extractor) |
| 5 | Turn duration | `turn_duration.durationMs`; fallback last−first ts | `fold` |
| 7 | Turn boundaries | the §3 composite rule | `fold` (extractor) |
| 6 | Turn count + long turns (>30m) | per-turn window → tokens, `sub_agent_spawn` count, `file_change` count; `long = duration_ms > threshold` | `fold` (+ `rollup` fills child token sums) |
| 2 | Subagent report | child `session_info` (tokens, duration) + parent `sub_agent_spawn` (purpose=`agent_type`/`meta.description`, origin, depth) | `rollup_subagents` |

Cross-session dependencies:
- The child's `fold` must stash its own `duration_ms` + token totals into `session_info.info`
  so `rollup` can read them (children arrive as `SessionInfo`, which has no timestamps).
- Extend the `session_info_of` query (`config/queries/fold.yaml`) and `_load_children_info`
  (`fold_session.py:123`) to select `info` (and `turns`) so the rollup sees child duration/tokens.
- **Origin** (`subagents[child].origin`): `human` when the spawn sits in a root-session human
  turn; `subagent` when the spawning session is itself a sidechain.

---

## 6. Module layout, wiring, config

```
src/server/worker/fold/
  __init__.py             # EconFold(SessionFold) + {vendor → FoldExtractor} registry
  driver.py               # fold()/rollup_subagents() orchestration (vendor-neutral)
  turns.py                # §3 boundary detection + per-turn windowing/metrics
  metrics.py              # token/tool/skill accumulators (pure, from typed inputs)
  extract_claude_code.py  # vendor-aware raw readers (turn_duration, isMeta, Skill result, tool type)
```
- Wire `EconFold()` at `cli_work.py:24` (currently `StubFold()`); keep `StubFold` for the
  loop-only fast test path.
- `config/worker.yaml`: `fold.long_turn_threshold_s: 1800`, `fold.vendors: [claude_code]`.
- Idempotency stays free: every accumulator is a pure function of the full deduped blob.
- Structured JSON logs (logger `worker.fold`): per-session counts only, never content.

---

## 7. Phased tasks

Each phase is shippable and tested against the recorded normalizer fixtures
(`tests/fixtures/normalizer/recorded/`) fed through `normalize()` → `EconFold`.

- [x] **P0 — Fold scaffold + vendor seam.** `EconFold` + registry + wiring; parity test vs.
      stub (same `event_count`), idempotent re-fold.
- [x] **P1 — Tokens** (`models` + `info.tokens_total`).
- [x] **P2 — Tools** (`tools`: type + ok/failed from `exit_status`).
- [x] **P3 — Skills** (`skills`: caller/success/tokens; `allowed_tools` deferred, see
      `metrics.py`'s TODO — its source (paired result / follow-on `attachment` event) isn't
      promoted onto the tool_call by the normalizer, and the adjacency correlation needs
      more real data before committing to a heuristic).
- [x] **P4 — Turns** (§3 boundary rule + `turn_duration` capture; added `turns`/`turn_count` to
      model + dataclass; per-turn tokens/subagents/files).
- [x] **P5 — Long turns** (>30m classification; `long_turn_count`; per-turn subagent/token/file metrics).
- [x] **P6 — Subagent report + cross-rollup** (`rollup_subagents` enrichment; child duration/token
      stash; `session_info_of` query + `_load_children_info` extension; long-turn child-token sums).
      `depth` is deferred to a flat `1` (this parent's direct children) pending a root-relative
      need -- see `driver.py`'s TODO.

Turn-heavy recorded fixture: **done** -- `tests/fixtures/normalizer/recorded/session-turns.jsonl`
+ `session-turns/subagents/` (the 27-task `1cc0fed2` run, sanitized via `sanitize.py`), consumed
by `tests/server/worker/fold/test_recorded_turns.py` through the real normalizer, not just
hand-built shapes. It caught a real bug (`metrics.skill_reports` was reading token fields off
`tool_call` events, which never carry them -- fixed via assistant_message adjacency) and a real
privacy gap in `sanitize.py` itself (dict *keys*, not just values, can carry data -- see the
recorded fixtures' README). The optional `session_info` shape-contract golden is still not done.

Add a turn-heavy recorded fixture (trimmed from the 27-task `1cc0fed2` run) for P4–P6. Consider a
`session_info` shape-contract golden (same technique as the normalizer's) to freeze these JSONB
shapes against drift.

---

## 8. Deferred

- **Cost in dollars** (`cost_usd`) — needs the pricing engine; token counts land now.
- **Items produced per long turn** (net lines, PRs) — the git-correlator's job (`pr_list`, arch §8.4).
- **Promoting turn/skill fields to typed normalizer output** — optional; only if a second
  consumer needs them without raw-digging.
