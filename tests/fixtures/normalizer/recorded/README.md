# Recorded normalizer fixtures

Real, **sanitized** Claude Code sessions used by the normalizer's real-data tests
(`tests/collector/normalizer/test_recorded_sessions.py`) and the shape contract
(`test_shape_contract.py`). These are the "recorded CC sessions" `docs/modules/normalizer.md`
§12 calls for — the counterpart to the hand-built `SessionSource`s in `../sessions.py`.

## What's here

| Fixture | Origin session | Exercises |
|---|---|---|
| `session-regular.jsonl` | `05c3d17f-…` (the session `normalizer.md` §6 already documents) | 64 records: user/assistant/tool_call/file_change + the full spread of passthrough record types (mode, attachment, ai-title, file-history-snapshot, …) |
| `session-subagents.jsonl` + `session-subagents/subagents/agent-*.{jsonl,meta.json}` | `0f5e45b9-…` (smallest real spawner) | 409 records, two `Task` subagents → real `sub_agent_spawn` + child-session correlation and `parent_session_id` linkage |
| `session-turns.jsonl` + `session-turns/subagents/agent-*.{jsonl,meta.json}` | `1cc0fed2-…` (a 27-task run) | 637 records, 27 `Task` subagents, real `turn_duration` markers (including one genuine >30min turn) and real `Skill` invocations — the worker plan's turn-heavy fixture (`docs/plan/2026-07-27-worker-session-analytics.md` §7 P4-P6), consumed by `tests/server/worker/fold/`, not the normalizer's own `RECORDED_NAMES`/shape-contract set |

The `subagents/` layout matches Claude Code's real on-disk shape, so `local_loader` auto-discovers
the children exactly as the batch path delivers them.

## Sanitization — why these are safe to commit

Raw sessions carry the developer's actual work, so nothing is committed verbatim. `sanitize.py`
produces a structure-preserving, content-scrubbed copy:

- **Structural ids survive byte-for-byte** (`uuid`, `parentUuid`, `tool_use.id`, `tool_use_id`,
  subagent `toolUseId`) — normalizer correlation and dedup depend on them.
- **Every string leaf is scrubbed**: home paths, e-mails, and common credential shapes
  (`sk-`/`ghp_`/AWS/Slack/bearer/`key=value`) collapse to a placeholder. Over-redaction is safe.
- **Volume is trimmed, not shape**: string leaves are capped at 300 chars and lists at 40
  elements — a real session's giant pasted blobs and 500-line result arrays carry no *shape*
  past their first entries, and trimming them keeps the repo lean.

Because bytes are trimmed and values scrubbed, these fixtures are for **structural/shape**
assertions, not exact-value goldens.

**Dict keys are scrubbed too, not just values.** A record can carry data *as a key*, not
just a value (`file-history-snapshot`'s `trackedFileBackups` is keyed by absolute file
path) — `_scrub_key` runs the same redaction patterns (no length cap, since a truncated key
risks silently colliding two distinct keys). This was a real gap fixed alongside the
`session-turns` fixture: `session-regular.jsonl`/`session-subagents.jsonl` had been
leaking a real developer's home-directory username through exactly this path since they
were first committed — re-run `sanitize.py` and diff if you suspect a similar leak
elsewhere.

## Refreshing

Fixtures are derived from sessions under a local source root (not in the repo). To regenerate:

```bash
python tests/fixtures/normalizer/recorded/sanitize.py --source-root <dir-of-raw-sessions>
```

Then re-freeze the shape contract if the shape legitimately changed and review both diffs:

```bash
NORMALIZER_REGEN_SHAPE=1 pytest tests/collector/normalizer/test_shape_contract.py
git diff tests/fixtures/normalizer/recorded tests/sample-data/normalizer-shape-contract.json
```
