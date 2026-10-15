# M0–M1: Backfill Proof Point — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From a clean checkout, `utils/backfill.sh` pointed at a real Claude Code home directory populates Postgres with sessions, token totals, and list-price costs — idempotently, with zero content bytes on the wire.

**Architecture:** Collector (backfill feeder → normalizer → spool → shipper) ships §6.1 events over HTTPS to a thin ingest service that appends to Postgres; a stream worker claims sessions via SKIP LOCKED and folds events into session metrics and list costs. Contracts live in `common.events`; all SQL lives in `server.storage`. Design reference: `docs/plan/component-design-implementation-plan.md` (components C1–C8), parent `docs/design/coding-econ-design-draft.md` §6.1 (event message), §7.2 (queue), §11.1 (tail loop).

**Tech Stack:** Python 3.11+, pydantic v2, FastAPI + uvicorn, httpx (+respx for tests), SQLAlchemy Core + Alembic + psycopg3, tenacity, structlog, pytest, Postgres 15+ (docker-compose for dev).

**Plan style note (project decision):** tasks specify contracts, test names with their assertions, commands, and expected output — **no implementation bodies**. The implementer writes bodies TDD-style inside each task. A named test in Step 1 must exist verbatim; its assertion description is its spec.

## Global Constraints

- Python 3.11+; environment via `python -m venv venv`; every new dependency is added to `requirements.txt` in the same commit.
- Wire format is exactly parent §6.1: snake_case, literal `{header, payload, ext}` nesting, ISO-8601 UTC timestamps.
- **No content on the wire or in logs**: no prompt text, no code, no command arguments, no tool input/output values. Every serialization point gets a negative test.
- CLI entry points are `cli_{function}.py`, run as `python -m {module}.cli_{function}`; shell wrappers live in `utils/`.
- Structured JSON logging via `common.logging_setup` only; log-event names as specified per task.
- Commit messages: conventional style (`feat:`, `test:`, `chore:`); never reference Claude or AI assistance.
- Idempotence is a tested property, not an intention: every write-path task includes a run-twice test.

---

### Task 1: Repository scaffolding

**Files:**
- Create: `pyproject.toml`, `requirements.txt`, `docker-compose.dev.yml`, `pytest.ini`, `.env.example`
- Create: `src/common/__init__.py`, `src/collector/__init__.py`, `src/server/__init__.py`, `tests/__init__.py`
- Create: `utils/backfill.sh`, `utils/ingest.sh`, `utils/worker.sh` (thin wrappers, may point at not-yet-existing modules)

**Interfaces:**
- Produces: installable package `talium-econ` with extras `[collector]` (watchdog, httpx, tenacity), `[server]` (fastapi, uvicorn, sqlalchemy, alembic, psycopg, httpx), `[dev]` (pytest, respx, everything); `docker-compose.dev.yml` exposing Postgres 15 on `localhost:5433`, credentials in `.env.example` as `DATABASE_URL`.

- [ ] **Step 1:** Write `pyproject.toml` (setuptools, `src/` layout with packages `common`, `collector`, `server`), `requirements.txt` (pinned), `pytest.ini` (testpaths=tests), `docker-compose.dev.yml`, `.env.example`, package `__init__.py` files, and the three `utils/*.sh` wrappers each containing one line of the form `python -m collector.cli_backfill "$@"`.
- [ ] **Step 2:** Verify: `python -m venv venv && venv/bin/pip install -e ".[dev]"` succeeds; `venv/bin/python -c "import common, collector, server"` prints nothing; `venv/bin/pytest` reports "no tests ran"; `docker compose -f docker-compose.dev.yml up -d` then `docker compose ps` shows postgres healthy.
- [ ] **Step 3:** Commit: `chore: scaffold package, extras, dev postgres, cli wrappers`

### Task 2: Structured JSON logging (`common.logging_setup`)

**Files:**
- Create: `src/common/logging_setup.py`
- Test: `tests/common/test_logging_setup.py`

**Interfaces:**
- Produces: `configure(component: str) -> structlog.BoundLogger`. Every log line is one JSON object with keys `ts` (ISO-8601 UTC), `level`, `component`, `event`, plus caller kwargs.

- [ ] **Step 1:** Write failing tests — `test_log_line_is_valid_json` (capture a log call, `json.loads` succeeds), `test_log_line_has_ts_level_component_event` (all four keys present, `component` equals the `configure` argument), `test_extra_kwargs_appear_as_fields` (`logger.info("x", n=3)` → field `n == 3`).
- [ ] **Step 2:** Run `venv/bin/pytest tests/common/test_logging_setup.py -v` → FAIL (module not found).
- [ ] **Step 3:** Implement `configure` with structlog JSON renderer until tests pass.
- [ ] **Step 4:** Run again → 3 passed.
- [ ] **Step 5:** Commit: `feat(common): structured json logging setup`

### Task 3: Layered config loader (`common.config`)

**Files:**
- Create: `src/common/config.py`
- Test: `tests/common/test_config.py`

**Interfaces:**
- Produces: `load(defaults: dict, file_path: Path | None = None, env_prefix: str = "TALIUM_") -> dict` — precedence defaults < TOML file < environment (env key `TALIUM_SPOOL__MAX_BYTES` maps to `spool.max_bytes`, double underscore = nesting).

- [ ] **Step 1:** Write failing tests — `test_defaults_returned_when_nothing_else` , `test_file_overrides_defaults` (tmp TOML), `test_env_overrides_file` (monkeypatch env), `test_env_double_underscore_nests`, `test_missing_file_is_ok`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement until pass. **Step 4:** Run → 5 passed.
- [ ] **Step 5:** Commit: `feat(common): layered config loader`

### Task 4: Event header model (`common.events.header`)

**Files:**
- Create: `src/common/events/__init__.py`, `src/common/events/header.py`
- Test: `tests/common/events/test_header.py`

**Interfaces:**
- Produces: `EventHeader(BaseModel)` with exactly the parent §6.1.1 fields (`event_id: str`, `session_id: str | None`, `seq: int`, `parent_id: str | None`, `schema_version: str`, `org_id: str`, `user_id: str | None`, `device_id: str | None`, `vendor: Vendor`, `agent_version: str | None`, `collector_version: str | None`, `source: Source`, `event_ts: datetime`, `collected_ts: datetime`, `event_type: EventType`); enums `EventType` (10 values incl. `unknown`), `Vendor` (`claude_code|codex|cursor|github|gitlab`), `Source` (`live_tail|backfill|webhook|poll`).

- [ ] **Step 1:** Write failing tests — `test_rejects_missing_event_id`, `test_accepts_null_parent_id_and_session_id`, `test_rejects_unknown_event_type`, `test_event_ts_parses_iso8601_and_serializes_utc_z`, `test_seq_must_be_non_negative`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 5 passed.
- [ ] **Step 5:** Commit: `feat(events): header model and enums per §6.1.1`

### Task 5: Payload models (`common.events.payloads`)

**Files:**
- Create: `src/common/events/payloads.py`
- Test: `tests/common/events/test_payloads.py`

**Interfaces:**
- Produces: one model per §6.1.2 payload — `SessionStart(cwd, repo_id, git_branch)`, `UserMessage(char_count, intent_text?, slash_command, plan_mode)`, `AssistantMessage(model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, stop_reason, duration_ms)`, `ToolCall(tool_name, mcp_server?, mcp_tool?, files: list[FileTouch], command_verb?, bytes_read, input_chars, input_hash, exit_status, duration_ms)` with `FileTouch(path, operation: FileOp, lines_added, lines_removed, hunks: list[Hunk] | None)`, `Hunk(start: int, len: int)`, `FileOp` enum `read|create|edit|delete`; `SubAgentSpawn(agent_type, child_session_id)`, `FileChange(path, extension, operation, lines_added, lines_removed, hunks, derived_from_event_id)`, `SessionEnd(reason)`, `GitEvent(provider, repo, action, branch, pr_number, commit_ids, files_touched, author)`, `PlanUsageSnapshot(plan_type, utilization_pct, window_reset_ts, depleted)`, `Unknown()` (empty).
- Produces: `PAYLOAD_BY_TYPE: dict[EventType, type[BaseModel]]`.

- [ ] **Step 1:** Write failing tests — `test_every_event_type_has_a_payload_class` (iterate `EventType`, assert mapping), `test_tool_call_operation_enum_rejects_bad_value`, `test_unknown_keys_inside_payload_are_ignored` (must-ignore, §6.1.3 rule 2), `test_hunk_requires_start_and_len_ints`, `test_intent_text_absent_by_default` (constructing `UserMessage(char_count=5)` serializes without an `intent_text` key).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 5 passed.
- [ ] **Step 5:** Commit: `feat(events): typed payloads per §6.1.2 with must-ignore`

### Task 6: Envelope + wire codec (`common.events.envelope`)

**Files:**
- Create: `src/common/events/envelope.py`
- Create: `tests/fixtures/golden/tool_call_event.json` — the parent §6.1.4 example, verbatim
- Test: `tests/common/events/test_envelope.py`

**Interfaces:**
- Consumes: `EventHeader`, `PAYLOAD_BY_TYPE` (Tasks 4–5).
- Produces: `Event(BaseModel)` with `header: EventHeader`, `payload` (validated against `PAYLOAD_BY_TYPE[header.event_type]`), `ext: dict[str, Any]`; `to_wire(event) -> bytes`; `from_wire(raw: bytes) -> Event`.

- [ ] **Step 1:** Write failing tests — `test_golden_example_round_trips` (load golden file → `from_wire` → `to_wire` → parsed JSON equal to original), `test_payload_type_mismatch_rejected` (tool_call header with assistant_message payload keys → ValidationError), `test_unknown_ext_keys_preserved_through_round_trip` (§6.1.3 rule 3), `test_missing_header_field_rejected`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 4 passed.
- [ ] **Step 5:** Commit: `feat(events): envelope and wire codec with golden round-trip`

### Task 7: Deterministic ids and input hash (`common.events.ids`)

**Files:**
- Create: `src/common/events/ids.py`
- Test: `tests/common/events/test_ids.py`

**Interfaces:**
- Produces: `deterministic_event_id(vendor_id: str | None, source_identity: str, position: int) -> str` (vendor id passed through when present, else stable hash); `session_salt() -> str` (random, generated once per session by the caller); `input_hash(tool_input: str, salt: str) -> str`.

- [ ] **Step 1:** Write failing tests — `test_vendor_id_passed_through`, `test_same_identity_and_position_same_id`, `test_different_position_different_id`, `test_input_hash_stable_for_same_salt`, `test_input_hash_differs_across_salts`, `test_input_hash_output_contains_no_input_substring` (feed a distinctive marker string, assert not present).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 6 passed.
- [ ] **Step 5:** Commit: `feat(events): deterministic event ids and salted input hash`

### Task 8: Storage schema + migrations (`server.storage`)

**Files:**
- Create: `src/server/storage/__init__.py`, `src/server/storage/tables.py`, `src/server/storage/db.py`, `src/server/storage/alembic/` (alembic init + first revision)
- Test: `tests/server/storage/test_migrations.py`, `tests/server/conftest.py`

**Interfaces:**
- Produces: SQLAlchemy Core tables — `events` (all §6.1.1 header fields as columns; `payload` JSONB; `ext` JSONB; `processed_at TIMESTAMPTZ NULL`; PK `event_id`; partial index on `processed_at IS NULL`; index `(session_id, seq)`), `sessions` (`session_id` PK, org/team/user/device, vendor, status, started_at, last_event_at), `session_metrics` (`session_id` PK, `tokens_by_model` JSONB, `tool_counts` JSONB, `files_touched` JSONB, cache fields, `list_cost NUMERIC NULL`), `orgs`, `users`, `devices`, `device_tokens` (`token` PK → org/user/device), `pricing_models` (`model_id`, `valid_from`, per-token-kind prices; seed rows for current Anthropic models in the migration).
- Produces: `db.engine_from_env() -> Engine` (reads `DATABASE_URL`); pytest fixture `db` in `tests/server/conftest.py` — runs `alembic upgrade head` once per session against the docker-compose Postgres, truncates all tables between tests.

- [ ] **Step 1:** Write failing tests — `test_alembic_upgrade_head_from_empty` (fresh schema, no error), `test_events_table_has_header_columns` (reflect and assert column names match §6.1.1 field list), `test_pricing_seed_rows_present` (at least one row with a positive input price).
- [ ] **Step 2:** Run `venv/bin/pytest tests/server/storage -v` (postgres up) → FAIL. **Step 3:** Implement tables + migration. **Step 4:** Run → 3 passed.
- [ ] **Step 5:** Commit: `feat(storage): core tables, migrations, pricing seed`

### Task 9: Event append repo (`server.storage.repo`)

**Files:**
- Create: `src/server/storage/repo.py`
- Test: `tests/server/storage/test_repo_append.py`

**Interfaces:**
- Consumes: `Event` (Task 6), tables (Task 8).
- Produces: `append_events(conn, events: list[Event]) -> AppendResult(inserted: int, duplicates: int)` using `INSERT ... ON CONFLICT (event_id) DO NOTHING`; `lookup_device_token(conn, token: str) -> DeviceIdentity(org_id, user_id, device_id) | None`.

- [ ] **Step 1:** Write failing tests — `test_append_inserts_header_columns_and_jsonb` (read row back, header column values equal model fields, payload JSONB round-trips), `test_duplicate_append_is_noop` (append same batch twice → second returns `inserted=0, duplicates=n`, row count unchanged), `test_lookup_token_hit_and_miss`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 3 passed.
- [ ] **Step 5:** Commit: `feat(storage): idempotent event append and token lookup`

### Task 10: Ingest service (`server.ingest`)

**Files:**
- Create: `src/server/ingest/__init__.py`, `src/server/ingest/app.py`, `src/server/ingest/cli_ingest.py`
- Test: `tests/server/ingest/test_app.py`

**Interfaces:**
- Consumes: `from_wire`, `append_events`, `lookup_device_token`.
- Produces: FastAPI app factory `create_app(engine) -> FastAPI` with `POST /v1/events` (gzip or plain JSON array of wire events; `Authorization: Bearer <device token>`; response `{"accepted": n, "duplicates": n, "rejected": [{"index": i, "reason": str}]}`) and `GET /healthz`; `cli_ingest.py` runs uvicorn on config port. Log events: `ingest.batch {org, n, dup, rejected, ms}`, `ingest.auth_failed`.

- [ ] **Step 1:** Write failing tests (FastAPI TestClient + `db` fixture) — `test_valid_batch_accepted_and_stored`, `test_replayed_batch_counts_duplicates_and_adds_no_rows`, `test_bad_token_401_nothing_stored`, `test_malformed_event_rejected_rest_accepted` (batch of 3 with one broken header → accepted 2, rejected index reported), `test_healthz_ok`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 5 passed.
- [ ] **Step 5:** Commit: `feat(ingest): authenticated deduplicating event front door`

### Task 11: Adapter base types (`collector.adapters.base`)

**Files:**
- Create: `src/collector/adapters/__init__.py`, `src/collector/adapters/base.py`
- Test: `tests/collector/adapters/test_base.py`

**Interfaces:**
- Produces: `SourceRef(path: Path, identity: str, vendor: Vendor)`; `VendorRecord(vendor: Vendor, source: SourceRef, position: int, raw: dict)`; `Checkpoint(identity: str, offset: int)`; `file_identity(path) -> str` (device+inode+first-256-bytes hash — rotation/truncation detection per parent §11.1); `SourceAdapter` Protocol with `discover() -> list[SourceRef]` and `read_new(ref: SourceRef, ckpt: Checkpoint | None, max_bytes: int) -> tuple[list[VendorRecord], Checkpoint]`.

- [ ] **Step 1:** Write failing tests — `test_file_identity_stable_across_reads`, `test_file_identity_changes_on_truncate_and_rewrite`, `test_checkpoint_dataclass_roundtrip`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 3 passed.
- [ ] **Step 5:** Commit: `feat(collector): adapter protocol, source identity, checkpoint types`

### Task 12: JSONL chunk reader (`collector.adapters.jsonl_reader`)

**Files:**
- Create: `src/collector/adapters/jsonl_reader.py`
- Test: `tests/collector/adapters/test_jsonl_reader.py`

**Interfaces:**
- Produces: `read_records(path, offset: int, max_bytes: int) -> tuple[list[tuple[int, dict]], int]` — parsed `(position, record)` pairs and the new offset; a torn (incomplete) final line is **not** consumed (offset stops before it, parent §11.1).

- [ ] **Step 1:** Write failing tests — `test_reads_complete_lines_and_advances_offset`, `test_torn_last_line_held_back` (write half a line, offset excludes it; append the rest, next read returns it), `test_resume_from_offset_no_duplicates`, `test_max_bytes_bounds_a_single_call` (10 MB fixture, one call returns ≤ max_bytes worth), `test_blank_lines_skipped`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 5 passed.
- [ ] **Step 5:** Commit: `feat(collector): jsonl chunk reader with torn-line holdback`

### Task 13: Claude Code adapter — backfill mode (`collector.adapters.claude_code`)

**Files:**
- Create: `src/collector/adapters/claude_code.py`
- Create: `tests/fixtures/claude_home/projects/-demo-project/aaaa-session.jsonl` (≥ 30 sanitized real-shape records: user, assistant with usage tokens, tool_use Edit with `structuredPatch`, tool_use Bash, summary/meta lines)
- Test: `tests/collector/adapters/test_claude_code.py`

**Interfaces:**
- Consumes: `jsonl_reader`, base types (Tasks 11–12).
- Produces: `ClaudeCodeAdapter(root: Path)` implementing `SourceAdapter`; `discover()` finds `projects/*/*.jsonl`; `read_new` yields `VendorRecord`s whose `raw` is the parsed JSONL object and whose `position` is the byte offset of the line; session id derived from the filename.

- [ ] **Step 1:** Write failing tests — `test_discover_finds_session_files`, `test_read_new_yields_records_in_order_with_positions`, `test_chunked_reads_cover_whole_file_exactly_once` (loop `read_new` with small max_bytes until empty; total record count equals file line count), `test_records_carry_uuid_and_parent_uuid_fields`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 4 passed.
- [ ] **Step 5:** Commit: `feat(collector): claude code adapter, backfill discovery and chunked reads`

### Task 14: Tool-meta allowlist extraction (`collector.normalize.tool_meta`)

**Files:**
- Create: `src/collector/normalize/__init__.py`, `src/collector/normalize/tool_meta.py`
- Test: `tests/collector/normalize/test_tool_meta.py`

**Interfaces:**
- Consumes: payload models (Task 5), `input_hash` (Task 7).
- Produces: `extract(tool_name: str, tool_input: dict, tool_result_meta: dict | None, salt: str) -> ToolCall` implementing the parent §4.2 allowlist: `Edit/Write/Read` → `files[]` with operation and, for Edit results carrying `structuredPatch`, `hunks` copied as numbers; `Bash` → `command_verb` = first whitespace token only; MCP names split into `mcp_server`/`mcp_tool`; always `input_chars` and `input_hash`; everything else dropped.

- [ ] **Step 1:** Write failing tests — `test_edit_yields_file_edit_with_hunks_from_structured_patch` (hunks equal the fixture's oldStart/oldLines numbers), `test_bash_yields_verb_only` (input `"git commit -m 'secret msg'"` → `command_verb == "git"` and serialized output contains neither `commit -m` nor `secret`), `test_write_new_file_operation_create`, `test_read_operation_read`, `test_mcp_tool_name_parsed_into_server_and_tool`, `test_unknown_tool_still_gets_input_chars_and_hash`, `test_no_content_marker_in_serialized_output` (plant marker `XKCD_CANARY_9931` in every input field; serialize result; assert absent).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 7 passed.
- [ ] **Step 5:** Commit: `feat(collector): per-tool allowlist extraction with content canary tests`

### Task 15: Claude Code normalizer (`collector.normalize.claude_code_map`)

**Files:**
- Create: `src/collector/normalize/claude_code_map.py`, `src/collector/normalize/registry.py`
- Create: `tests/fixtures/golden/claude_code_normalized/*.json` (golden events for the Task 13 fixture)
- Test: `tests/collector/normalize/test_claude_code_map.py`

**Interfaces:**
- Consumes: `VendorRecord` (Task 11), `Event`/ids (Tasks 6–7), `tool_meta.extract` (Task 14).
- Produces: `SessionContext(session_id, org_id, user_id, device_id, salt, agent_version, source: Source)`; `normalize(rec: VendorRecord, ctx: SessionContext) -> list[Event]` — user record → `user_message` (char_count only), assistant record → `assistant_message` (model + 4 token kinds from `usage`), tool_use → `tool_call` plus derived `file_change` events for write operations (`derived_from_event_id` set), first record → `session_start` (cwd/branch when present); unmapped record types → `unknown` event with metadata-only ext; `registry.mapper_for(vendor)` dispatch.

- [ ] **Step 1:** Write failing tests — `test_fixture_file_normalizes_to_golden_events` (run every fixture record; compare against golden JSON), `test_event_ids_deterministic_across_two_runs`, `test_parent_id_propagated_from_parent_uuid`, `test_seq_monotonic_within_session`, `test_unknown_record_type_becomes_unknown_event`, `test_no_content_anywhere` (serialize all produced events; assert fixture's known prompt/code substrings absent — list them in the test).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 6 passed.
- [ ] **Step 5:** Commit: `feat(collector): claude code normalizer with golden and canary tests`

### Task 16: Spool (`collector.spool`)

**Files:**
- Create: `src/collector/spool.py`
- Test: `tests/collector/test_spool.py`

**Interfaces:**
- Consumes: `Event`, `to_wire`/`from_wire` (Task 6).
- Produces: `Spool(dir: Path, max_total_bytes: int)` with `append(events: list[Event])` (durable before return), `next_batch(max_bytes: int) -> Batch | None` where `Batch(batch_id: str, events: list[Event])`, `ack(batch_id: str)` (advances read cursor durably), `pending_bytes() -> int`. Log events: `spool.appended`, `spool.evicting` (warning at cap).

- [ ] **Step 1:** Write failing tests — `test_append_then_next_batch_round_trips_events`, `test_unacked_batch_reappears_after_reopen` (new `Spool` on same dir re-serves it), `test_acked_batch_never_reserved_after_reopen`, `test_next_batch_respects_max_bytes`, `test_fifo_order_preserved`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement (append-only segment files + cursor file). **Step 4:** Run → 5 passed.
- [ ] **Step 5:** Commit: `feat(collector): crash-safe disk spool`

### Task 17: Checkpoint store (`collector.checkpoint`)

**Files:**
- Create: `src/collector/checkpoint.py`
- Test: `tests/collector/test_checkpoint.py`

**Interfaces:**
- Consumes: `Checkpoint` (Task 11).
- Produces: `CheckpointStore(path: Path)` (local sqlite3) with `get(source_key: str) -> Checkpoint | None`, `advance(source_key: str, ckpt: Checkpoint)`; source_key = `f"{vendor}:{identity}"`.

- [ ] **Step 1:** Write failing tests — `test_get_missing_returns_none`, `test_advance_then_get_round_trips`, `test_advance_persists_across_reopen`, `test_identity_change_is_a_new_key` (rotation semantics).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 4 passed.
- [ ] **Step 5:** Commit: `feat(collector): durable checkpoint store`

### Task 18: Shipper (`collector.ship`)

**Files:**
- Create: `src/collector/ship.py`
- Test: `tests/collector/test_ship.py`

**Interfaces:**
- Consumes: `Batch` (Task 16), `to_wire`.
- Produces: `Shipper(url: str, token: str)` with `send(batch: Batch) -> ShipResult(accepted: int, duplicates: int, rejected: list)` — gzip POST to `/v1/events`, tenacity exponential backoff on 5xx/network (max configurable), raise `PermanentShipError` on 4xx. Log events: `ship.sent`, `ship.retry`, `ship.permanent_failure`.

- [ ] **Step 1:** Write failing tests (respx mock) — `test_send_success_parses_counts`, `test_retries_on_503_then_succeeds` (assert ≥2 requests), `test_401_raises_permanent_no_retry` (exactly 1 request), `test_payload_is_gzip_of_wire_events` (decode mock request body, `from_wire` each).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 4 passed.
- [ ] **Step 5:** Commit: `feat(collector): batching shipper with backoff`

### Task 19: Backfill runtime + CLI (`collector.runtime`, `collector.cli_backfill`)

**Files:**
- Create: `src/collector/runtime.py`, `src/collector/cli_backfill.py`
- Test: `tests/collector/test_runtime_backfill.py`

**Interfaces:**
- Consumes: everything from Tasks 11–18 plus `common.config`.
- Produces: `run_backfill(root: Path, cfg: dict) -> BackfillReport(files: int, records: int, events_shipped: int)` — loop per parent §3.2/§11.1: discover → for each file: read chunk → normalize → spool → (shipper drains spool) → **checkpoint advances only after ack**; `cli_backfill.py` argparse: `--root`, `--config`, `--org/--user/--device` (M1 identity comes from config, Q2 in the roadmap). Log events: `backfill.file_started`, `backfill.file_done`, `backfill.done`.

- [ ] **Step 1:** Write failing tests — `test_backfill_ships_all_fixture_events` (Shipper faked with in-memory sink; counts match golden), `test_checkpoint_not_advanced_when_ship_fails` (failing fake shipper → checkpoint unchanged, spool retains), `test_rerun_after_success_ships_nothing` (checkpoints make it a no-op), `test_memory_bounded_on_large_file` (200k-line generated fixture; assert peak RSS delta < 100 MB using `resource.getrusage`).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 4 passed.
- [ ] **Step 5:** Commit: `feat(collector): chunked backfill runtime and cli`

### Task 20: Worker claim queue (`server.worker.claim`)

**Files:**
- Create: `src/server/worker/__init__.py`, `src/server/worker/claim.py`
- Test: `tests/server/worker/test_claim.py`

**Interfaces:**
- Consumes: tables (Task 8).
- Produces: `claim_sessions(conn, max_sessions: int) -> list[SessionWork]` — sessions having events with `processed_at IS NULL`, locked with `FOR UPDATE SKIP LOCKED`, each `SessionWork(session_id, events: list[Event])` ordered by `seq`; `complete(conn, work: SessionWork)` — marks those event rows processed in the same transaction the caller commits.

- [ ] **Step 1:** Write failing tests — `test_claim_returns_sessions_with_unprocessed_events_ordered_by_seq`, `test_two_connections_never_claim_same_session` (two engine connections, overlapping claims, assert disjoint), `test_rollback_returns_session_to_pool`, `test_complete_marks_processed_and_session_not_reclaimed`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 4 passed.
- [ ] **Step 5:** Commit: `feat(worker): skip-locked session claim queue`

### Task 21: Session builder (`server.worker.stream.session_builder`)

**Files:**
- Create: `src/server/worker/stream/__init__.py`, `src/server/worker/stream/session_builder.py`
- Modify: `src/server/storage/repo.py` (add `upsert_session`, `upsert_session_metric`)
- Test: `tests/server/worker/stream/test_session_builder.py`

**Interfaces:**
- Consumes: `Event`, `SessionWork` (Task 20), repo (Task 9 — add `upsert_session`, `upsert_session_metric` here).
- Produces: `apply(events: list[Event]) -> SessionDelta` (pure: token totals per model, tool counts, files touched, first/last timestamps) and `persist(conn, session_id, delta)` — recomputed-style upsert keyed on aggregates that are safe under replay (per-model token totals derived only from event rows, so re-processing the same events yields the same stored state).

- [ ] **Step 1:** Write failing tests — `test_token_totals_by_model_summed_correctly` (fixture events, hand-computed expected), `test_files_touched_deduplicated_with_operations`, `test_apply_then_persist_twice_equals_once` (idempotence against db), `test_session_row_created_live_with_timestamps`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 4 passed.
- [ ] **Step 5:** Commit: `feat(worker): incremental session builder`

### Task 22: List-price cost engine (`server.worker.stream.cost_engine`)

**Files:**
- Create: `src/server/worker/stream/cost_engine.py`
- Test: `tests/server/worker/stream/test_cost_engine.py`

**Interfaces:**
- Consumes: `session_metrics` shape (Task 21), `pricing_models` seed (Task 8).
- Produces: `list_price(tokens_by_model: dict, prices: PriceTable) -> Decimal | None` — sum over models × token kinds at the price row valid for the session date; `PriceTable.load(conn, as_of: date)`; unknown model → contributes `None` and logs `cost.unknown_model {model}` (warning), total remains computable for known models with an `incomplete` flag.

- [ ] **Step 1:** Write failing tests — `test_known_models_priced_exactly` (hand-computed decimal expected), `test_unknown_model_flags_incomplete_not_crash`, `test_price_version_selected_by_date` (two seed rows, older session picks older price), `test_zero_tokens_zero_cost`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 4 passed.
- [ ] **Step 5:** Commit: `feat(worker): list-price cost engine over versioned prices`

### Task 23: Stream worker runner + CLI (`server.worker.runner`, `cli_worker`)

**Files:**
- Create: `src/server/worker/runner.py`, `src/server/worker/cli_worker.py`
- Test: `tests/server/worker/test_runner.py`

**Interfaces:**
- Consumes: Tasks 20–22.
- Produces: `run_once(engine) -> int` (sessions processed: claim → apply → cost → persist → complete, one transaction per session) and `main(worker_type: str, loop: bool)`; `cli_worker.py` argparse `--type stream` (`scheduled`/`classifier` rejected with "not implemented until M3/M4"), `--once` flag for tests/cron. Log events: `worker.claimed`, `worker.completed`, `worker.idle`.

- [ ] **Step 1:** Write failing tests — `test_run_once_processes_ingested_fixture_events_end_to_end` (seed events via Task 9 repo; assert session_metrics row with expected totals and list_cost), `test_run_once_twice_changes_nothing`, `test_unknown_worker_type_exits_with_error`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → 3 passed.
- [ ] **Step 5:** Commit: `feat(worker): stream worker runner and cli`

### Task 24: End-to-end backfill integration test + runbook

**Files:**
- Create: `tests/integration/test_backfill_e2e.py`
- Create: `docs/plan/m1-acceptance-runbook.md`

**Interfaces:**
- Consumes: the whole M1 stack.

- [ ] **Step 1:** Write the failing integration test — `test_backfill_e2e`: start ingest app in-process (uvicorn TestServer/ASGI transport), seed org/user/device/token, run `run_backfill` against `tests/fixtures/claude_home`, run `run_once(engine)`; assert: session count, per-model token totals, list_cost, files_touched match golden expectations; then `test_backfill_e2e_rerun_is_noop` (run both again, row snapshots identical); `test_no_content_in_events_table` (SQL scan for the canary strings and known fixture prompt substrings → zero hits).
- [ ] **Step 2:** Run → FAIL (wiring gaps expected). **Step 3:** Fix wiring until green — no new features, only integration fixes.
- [ ] **Step 4:** Run `venv/bin/pytest tests/integration -v` → 3 passed. Then run the whole suite: `venv/bin/pytest` → all green.
- [ ] **Step 5:** Write `docs/plan/m1-acceptance-runbook.md`: the exact command sequence for the live acceptance demo — `docker compose up -d`; `alembic upgrade head`; seed SQL for one org/user/device token; `utils/ingest.sh`; `utils/backfill.sh --root ~/.claude`; `utils/worker.sh --type stream --once`; the three SQL queries that show sessions, token totals, and costs; the rerun-is-noop check.
- [ ] **Step 6:** Commit: `test: end-to-end backfill integration and m1 acceptance runbook`

---

## Self-Review Checklist (run after writing, before execution)

- **Coverage vs. component design:** C1 (Tasks 4–7), C2 (11–13), C3 (14–15), C4 (16–18), C5 (10), C6 (8–9), C7 (20), C8 (21–23) — C8's liveness/rollups and C9–C11 are deliberately M2+ (see roadmap). M1 acceptance criteria (backfill populates sessions + costs, idempotent, content-free) are Tasks 19 + 24. ✔
- **No placeholders:** every task names its tests with their assertions and exact commands. ✔
- **Type consistency:** `SourceRef/VendorRecord/Checkpoint` defined Task 11, consumed 12–13, 17, 19; `Event` defined Task 6, consumed 9–10, 15–16, 20–21; `Batch` defined Task 16, consumed 18–19. ✔
