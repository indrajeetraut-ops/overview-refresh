# OTLP test fixtures

Recorded OTLP/JSON export bodies from a real Claude Code client (v2.1.219), trimmed to one
session and **anonymized**. Rebuilt by `make_fixtures.py` (see its docstring); the builder
aborts if any source identifier survives the rewrite.

Source: a local OTel collector's file dump (`~/.claude/otel-local/data/`), not in the repo.

## Anonymization

Every real identifier is mapped to a fixed synthetic value, and every free-form content
attribute (`prompt`, `response`, `tool_input`, `tool_parameters`) is replaced wholesale with
`<redacted-for-fixture>` — those attributes embed absolute paths and file contents, none of
it maps to a canonical column, and none of it survives the v1 event filter.

| Real | Fixture |
|---|---|
| user.email | `dev1@example.test` |
| user.account_uuid | `11111111-1111-4111-8111-111111111111` |
| user.account_id | `user_00000000000000000001` |
| organization.id | `22222222-2222-4222-8222-222222222222` |
| user.id | `0000…0001` (64 hex) |
| process.owner | `devuser` |

`session.id`, `request_id` and `prompt.id` are opaque client-generated UUIDs carrying no
personal data; they are kept verbatim because the goldens key on them.

## Files

### `metrics_session.jsonl` — 15 export bodies, 12 series, 80 datapoints

Session `8f87e0a5-872f-4617-9d27-d9d0bc8db54d`. All six Claude Code metric names, every one
a **cumulative monotonic sum** (`aggregationTemporality: 2`, `isMonotonic: true`).
Contains a genuine two-epoch series (`claude_code.session.count` across a resume) and both
an interactive (`query_source: main`) and a background (`query_source: auxiliary`) label
group.

Last-value fold vs. the naive sum that inflated the reconciliation 150× — the fold test
asserts the left column and that the right one is *not* produced:

| metric | type | query_source | fold (correct) | naive sum |
|---|---|---|---|---|
| claude_code.token.usage | input | main | 13 | 77 |
| claude_code.token.usage | output | main | 7433 | 42051 |
| claude_code.token.usage | cacheRead | main | 466409 | 2665840 |
| claude_code.token.usage | cacheCreation | main | 56799 | 387026 |
| claude_code.token.usage | input | auxiliary | 504 | 2520 |
| claude_code.token.usage | output | auxiliary | 13 | 65 |
| claude_code.token.usage | cacheRead | auxiliary | 78841 | 394205 |
| claude_code.token.usage | cacheCreation | auxiliary | 2254 | 11270 |
| claude_code.cost.usage | — | main | 0.987084(5) | 6.254840 |
| claude_code.cost.usage | — | auxiliary | 0.0648055 | 0.324027 |
| claude_code.active_time.total | cli | — | 148.502 | 742.510 |
| claude_code.session.count | — | — | 2 (two epochs) | 15 |

### `logs_session.jsonl` — 13 export bodies, 37 log records

The same session. The full event-name zoo, so the receiver's count-and-drop numbers are
testable against a known distribution:

| event.name | count | v1 disposition |
|---|---|---|
| api_request | 8 | **kept** → `otel_api_requests` |
| tool_decision | 6 | dropped |
| tool_result | 6 | dropped |
| hook_execution_start | 6 | dropped |
| hook_execution_complete | 6 | dropped |
| assistant_response | 2 | dropped |
| plugin_loaded | 2 | dropped |
| user_prompt | 1 | dropped |

The 8 `api_request` records include background calls (`query_source` `prompt_suggestion`
and `sdk`) — the ~5.7% of session spend the JSONL path cannot see.

Scope names differ per signal: metrics arrive under `com.anthropic.claude_code`, logs under
`com.anthropic.claude_code.events`. Vendor detection must therefore prefix-match.

### `metrics_counter_reset.jsonl` — 4 export bodies, 1 series, 2 epochs

Synthesized (the recorded dump has no reset with enough points on either side to
discriminate). One `claude_code.token.usage` / `type=output` series:

| epoch | exports | values |
|---|---|---|
| A | 2 | 100, 250 |
| B (restart) | 2 | 40, 90 |

The four candidate readings are deliberately all distinct — correct **340**, naive sum 480,
overall last value 90, overall max 250 — so no incorrect fold passes by coincidence.
