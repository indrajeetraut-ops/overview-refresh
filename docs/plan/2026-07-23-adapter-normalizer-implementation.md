# Collector (Source Adapter + Normalizer) Implementation Plan

**Status:** Implemented. This doc is a record of what was planned and built, kept current
via the **Amendments** section (§3) below as post-implementation changes landed — it is not
a snapshot frozen at kickoff.
**Specs:** `docs/modules/normalizer.md`, `docs/modules/source-adapter.md`.
**Built on:** `docs/modules/event-messaging.md` (msging client) and `docs/modules/batch-uploader.md`
(the uploader) — both already implemented before this work started.
**Code:** `src/collector/` (engine, source-reader, config, checkpoint, store, dump transport,
CLIs) and `src/collector/normalizer/` (the Claude Code normalizer).

---

## 0. Scope and sequencing

The work was split into two passes, decided explicitly before implementation rather than
built as one undifferentiated effort:

1. **Phase 1 — the normalizer**, standalone, testable without any engine/S3/msging plumbing.
2. **Phase 2 — the source-adapter engine** around it, once the normalizer was solid.

This split was a deliberate call: the normalizer is the higher-value, most self-contained
piece (`docs/modules/normalizer.md` calls it "the crispest part of the doc"), and building it
first meant it could be fully tested and exercised (via a CLI, see §1.4) before the engine's
larger, more architecturally-open surface (storage backends, checkpointing, msging wiring)
was tackled.

---

## 1. Phase 1 — The Normalizer

### 1.1 Decisions made before implementation

- **Target the extended event schema directly** (`api_error`, `compaction`,
  `reasoning_tokens`, `cache_write_detail`, `cache_miss_tokens`, `server_tool_use`) on this
  branch, without merging `design/agent-neutral-schema-fidelity`. Justification: those fields
  only ever existed as *doc* changes to `coding-econ-design-draft.md` on that branch — no code
  — and `EventIn.payload` is an untyped `dict` (confirmed in `src/common/msging/models.py`),
  so there was no code dependency to pull in, only a doc one already satisfied by reading the
  spec.
- **Header = pydantic (msging's `EventIn`, reused directly), payload = open dict.** The
  normalizer defines no event type of its own — see `models.py`'s docstring and
  `docs/modules/normalizer.md` §4.
- **`SessionSource`/`SubagentStream`/`NormalizedSession` are plain dataclasses**, not
  pydantic — they never cross a process boundary, only `EventIn` does.

### 1.2 Module layout built

```
src/collector/normalizer/
    models.py        # SessionSource, SubagentStream, NormalizedSession
    config.py         # NormalizerConfig (usage_field_map, mcp_name_pattern, redaction_patterns)
    redact.py          # config-driven span redaction
    claude_code.py       # ClaudeCodeNormalizer: dispatch table, tool pairing, derivations,
                         #   subagent recursion, vendor id tiering, raw passthrough
    local_loader.py        # dev/manual-testing loader: real ~/.claude/projects file -> SessionSource
    __init__.py               # VendorNormalizer protocol + registry

src/collector/cli_normalize.py   # python -m collector.cli_normalize <session.jsonl> --out result.json

config/normalizer/claude_code.yaml

tests/fixtures/normalizer/sessions.py   # programmatic fixture builders (no committed blobs)
tests/collector/normalizer/*.py          # unit tests, see §1.4
```

### 1.3 Task order (as actually executed)

1. `models.py` — data types.
2. `config.py` + `config/normalizer/claude_code.yaml`.
3. `redact.py`.
4. `claude_code.py` — the `ClaudeCodeNormalizer` core.
5. `normalizer/__init__.py` — `VendorNormalizer` protocol + registry.
6. `tests/fixtures/normalizer/sessions.py` — fixture builders (`simple_session`,
   `tool_pairing_session`, `mixed_model_subagent_session` — the doc-required mixed-model +
   incomplete-subagent fixture — `api_error_and_compaction_session`, `passthrough_session`).
7. Unit tests across mapping, tool pairing, subagents, session lifecycle, losslessness.
8. `local_loader.py` + `cli_normalize.py` — the capability to run the normalizer standalone
   against a real `~/.claude/projects` file, dumping the result to JSON for manual review.
9. `test_local_loader.py` + full-suite verification.

### 1.4 Outcome

32 tests passing at the end of phase 1 (later 41, after amendments — §3.3). Verified against
a real captured session via `cli_normalize.py`, not just synthetic fixtures.

---

## 2. Phase 2 — The Source-Adapter Engine

### 2.1 Decisions confirmed before implementation

Three real, open design questions were resolved with the user before writing code (not
assumed):

1. **Producer identity: one shared producer_id for the whole collector process**, registered
   once, rather than a per-upload synthetic producer_id (RFC §5.2's original design). `org_id`/
   `user_id` — already per-`open_session()`-call params on the existing, unmodified
   `EventClient` — carry the per-developer identity instead. Chosen specifically so **no
   changes to `src/common/msging`** were needed; `open_session()`'s signature has no
   `producer_id` override, and extending it was assessed as unnecessary complexity for this
   pass.
2. **Entry point: `run_once(scope)` only.** No `run_forever` (standing poll/doorbell) — nothing
   currently triggers it, and it's a documented seam (`source-adapter.md` §11/§A2), not
   required for the batch deployment this phase targets.
3. **Checkpoint/ACK attribution: flush per source.** `FlushResult`/`flush()` report aggregate
   counts across the whole spool, not per-source, so batching many sources before one flush
   would make it impossible to know *which* source's events got ACKed if that flush only
   partially succeeded. The engine processes and fully flushes **one source at a time**,
   committing that source's checkpoint only if the flush fully drained
   (`remaining == 0 and parked == 0`) — trading smaller HTTP batches for an unambiguous
   correctness story.

### 2.2 A design bug found and fixed *during* planning, before any engine code was written

Reviewing the plan surfaced that `SubagentStream.records` (built in phase 1) was a fully
materialized `list[dict]` — meaning a session with many sibling subagents would load every
one's full transcript into memory before normalizing any of them, bounded by fan-out width
rather than depth. This violates `source-adapter.md` §7's normative "no unbounded lookahead"
rule. Fixed as a prerequisite, before the source-reader was built against it:

- `models.py`: `SubagentStream.records` became `Callable[[], list[dict]]` — a lazy loader —
  instead of a plain list. Discovering *which* subagents exist (`subagent_streams`, `meta`)
  stayed eager (cheap: filenames + small `meta.json` reads); only the potentially-large JSONL
  content is deferred.
- `claude_code.py`: the recursion in `_normalize_stream()` calls `stream.records()` at the
  point it actually recurses into that child, not before — so at most one branch's content is
  ever resident at once.
- `local_loader.py` and `tests/fixtures/normalizer/sessions.py` updated to the lazy interface
  (`functools.partial`, and a `_static()` fixture helper respectively).

The same review also closed a second gap: `NormalizedSession.parent_session_id` had no wire
path at all (`EventIn`/`open_session()`/`EnvelopeHeader` all lack a field for it). Fixed by
stamping it into the subagent's own synthesized `session_start` payload — the only place that
linkage now reaches the wire.

### 2.3 Module layout built

```
src/collector/
    store.py                          # SourceStore protocol + LocalFsSourceStore + S3SourceStore
    checkpoint.py                       # CheckpointStore protocol + FileCheckpointStore
    config.py                            # CollectorConfig (source_store/checkpoint_store/msging)
    engine.py                             # run_once(): the tail-with-checkpoint loop, batch-scoped
    cli_collect.py                          # python -m collector.cli_collect --config ... [--dump-to ...]
    claude_code/
        source_reader.py                       # manifest -> grouped sessions -> SessionSource

config/collector.yaml

tests/collector/
    test_source_store.py                 # (named to avoid a basename collision with
                                          #  tests/common/msging/test_store.py — pytest requires
                                          #  unique basenames for these __init__.py-less modules)
    test_checkpoint.py
    claude_code/test_source_reader.py
    test_engine.py                        # the centerpiece: uploader -> engine -> mock ingest
```

### 2.4 Task order (as actually executed)

1. Prerequisite fix (§2.2), verified against the full normalizer suite before proceeding.
2. `store.py` — `SourceStore`/`LocalFsSourceStore`/`S3SourceStore`, structurally mirroring
   `src/server/uploader/objectstore.py`'s patterns but built fresh (collector is a reader,
   `server.uploader` a writer — not sharing code across that boundary even though they touch
   the same bucket layout).
3. `checkpoint.py` — mirrors `src/common/msging/store.py`'s `FileProducerStore` exactly (atomic
   tmp+replace JSON, a lock, a `make_*_store(cfg)` factory raising `NotImplementedError` for
   the unbuilt `db` backend).
4. `config.py` + `config/collector.yaml`.
5. `claude_code/source_reader.py` — groups a manifest's files into sessions (main `.jsonl` +
   sibling `subagents/` tree, recursively nested), reading via `SourceStore` with the same
   lazy-subagent-content discipline as §2.2.
6. `engine.py` (`run_once`) + `cli_collect.py`.
7. Unit tests: store, checkpoint, source_reader.
8. `test_engine.py` — chains the *actual* uploader (`server.uploader.service.handle_upload`)
   and the *actual* engine against a mock ingest server via `httpx.MockTransport` (the same
   technique `tests/common/msging/test_integration.py` uses for msging alone) — covering the
   plain run, the unchanged-file skip, a re-upload of a longer file (duplicate `event_id`s,
   correctly left for ingest to dedupe), and crash-resume.

### 2.5 Outcome

All unit + integration tests passing; verified manually end-to-end (real HTTP `POST` to a
locally-run uploader, then `cli_collect.py` reading its staged output) — see
`docs/manual-testing.md`.

---

## 3. Amendments (post-implementation changes, found via real-world use)

Everything below happened *after* §1/§2 were built and reviewed, surfaced by actually running
the tools against real Claude Code data and a real rebase — kept here so this doc doesn't go
stale relative to the code.

### 3.1 Collector config: `msging.server_url` must be a literal, not an `${ENV}` ref

`config/collector.yaml` originally set `server_url: ${TALIUM_INGEST_URL}`. The config
loader's env substitution (`config.py`'s `_ENV_REF`) has no bash-style `:-default` fallback —
an unset env var substitutes to an empty string, which YAML then parses as `null`, failing
`ClientConfig`'s required-string validation before `--dump-to` mode even got a chance to
ignore the value. Fixed by making it a plain literal (`http://localhost:8000`) in the
checked-in config, with a comment explaining why it isn't an env ref.

### 3.2 The uploader staged macOS metadata files as if they were session content

Running the real one-liner (`tar czf - -C ~ .claude/projects | curl ...`) on macOS embeds
AppleDouble sidecar files (`._<name>`, binary resource-fork data) and occasional
`.DS_Store` files alongside real entries. The uploader staged them as ordinary session files;
the collector then crashed trying to `json.loads()` binary junk. Fixed on both ends:

- `src/server/uploader/unpack.py`: `sanitize_member()` now skips `.DS_Store`/`._*` entries at
  unpack time, so future uploads never stage or manifest them. New fixture
  (`build_archive_with_macos_metadata` in `tests/fixtures/uploader/archives.py`) and test
  (`test_macos_metadata_entries_are_skipped`).
- `src/collector/claude_code/source_reader.py`: skips the same pattern defensively when
  grouping, so a manifest staged *before* the uploader fix (already on disk) doesn't need
  re-uploading — confirmed by reproducing the exact tainted-manifest scenario and rerunning
  `cli_collect`. New test `test_group_skips_macos_metadata_entries`.

### 3.3 Defensive-typing audit across `claude_code.py`

A real crash (`AttributeError` on `tool_use_result.get("exitCode")`) traced to
`record.get("toolUseResult") or {}` — real Claude Code data doesn't always shape that field
as a dict (it's a plain string for some tools). `or {}` only substitutes when the value is
*falsy*, not when it's present-but-wrong-typed. Audited the whole file for the same pattern
and found **8 identical occurrences**, plus two related unguarded-shape assumptions
(`cache_creation`/`server_tool_use` treated as dicts without checking;
`api_error.status` compared numerically without checking it's an `int`). Fixed via a shared
`_as_dict()` helper and `isinstance` guards throughout — each one now degrades to "treat as
absent" rather than crashing, per `source-adapter.md` §7's "never crash on new input" rule.
Nine new regression tests in `tests/collector/normalizer/test_resilience.py`, one per fixed
spot, each planting the real kind of malformed value that was found.

### 3.4 `vendor_record_id` renamed to `record_id`, everywhere

A terminology simplification, applied end-to-end: `src/common/msging/models.py`
(`EventIn.record_id`), `ids.py`, `client.py` — the shared msging library — plus
`src/collector/normalizer/claude_code.py` (13 sites), `cli_normalize.py`, all affected tests
in `tests/common/msging` and `tests/collector/normalizer`, and every doc referencing the old
name (`event-messaging.md`, `normalizer.md`, `source-adapter.md`, the upload-batch-ingestion
RFC, the event-messaging implementation plan). Verified with a repo-wide grep afterward —
zero remaining occurrences.

### 3.5 `--dump-to`: exercising the engine without a live Ingest process

Added `src/collector/dump_transport.py` (`make_recording_transport` /
`make_dump_transport`) — wires msging's *real* client-side code (header stamping,
`event_id`, `seq`, gzip, batching, retry) against a real `MsgServer` for register/verify/ack,
but writes each shipped batch's `Envelope`s to a file instead of a network call. Exposed via
`cli_collect.py --dump-to <file>`. `test_engine.py`'s mock-server wiring was refactored to
reuse this same function (`received.extend` as its sink) rather than duplicating the handler,
so the shared code is validated by the existing centerpiece test too. New
`tests/collector/test_dump_transport.py`.

### 3.6 Documentation: a field reference was written, then merged into the design doc

`docs/normalizer-event-reference.md` was created as a standalone, real-data-grounded field
reference (every example pulled from `tests/sample-data/normaliser-result.json`, a real
captured session, rather than synthetic fixtures) — covering the shape of every event type,
field origin/significance, how turns are identified (no `turn_id` exists; reconstructed via
`user_message` boundaries, `parent_id` chains, and CC's own `system`/`turn_duration` records),
and a real gotcha found in the process (a `tool_result` record's `uuid` is consumed when
merged into its `tool_call` and never becomes its own event, producing "dangling" `parent_id`
references — 7 of them in the one real 67-event sample checked).

Per a later request, this was merged into `docs/modules/normalizer.md` as new §6 ("Event
Payload Reference — Fields, Origin, and Significance"), and the standalone file deleted.
Merging required renumbering every subsequent section (old §6–§13 → §7–§14) — done via
per-line verification rather than a blind find-replace, since several existing `§6.x`/`§8`/
`§13` references in the doc point at *other* documents (`system-architecture.md`, the master
doc) and had to be left untouched. Two pre-existing stale cross-references were fixed along
the way (`§11, Q-reasoning`/`§11, Q-cachemiss` should have pointed at Open Items, now
correctly `§14`). A follow-up request then added the fields *outside* `payload`
(`event_type`, `record_id`, `parent_id`, `event_ts`) to that section's §6.1, which the initial
merge had dropped — with an explicit note distinguishing them from the separate, larger wire
`header` msging builds later.

---

## 4. Final state

- **154 tests passing** across the full suite (`pytest tests/ -q`, excluding
  `tests/server/storage` and `tests/common/msging/test_store.py`, which need `sqlalchemy`/
  `alembic`/`psycopg` installed — an unrelated, pre-existing environment gap from the
  storage-layer work merged in via rebase, not something this effort introduced).
  - 41 in `tests/collector/normalizer`.
  - 23 in `tests/collector` (engine, store, checkpoint, source_reader, dump_transport).
- **Manual verification:** `docs/manual-testing.md` — real uploader HTTP round trip, real
  `cli_collect.py --dump-to` run against it, plus the automated centerpiece
  (`test_engine.py`) chaining both in-process.
- **Rebase note:** this branch was rebased onto `main` (which had gained a full storage
  layer, PR #15, in the interim) partway through this work; `pytest.ini`
  (`pythonpath = . src tests`) and `requirements.txt` conflicts were resolved as a union of
  both sides' additions — recorded here since it affected file state at the time but isn't
  itself part of this module's design.

## 5. Deferred / not built (documented seams, not gaps)

- `run_forever` (standing poll/doorbell) — source-adapter.md §11/§A2.
- The `db` backend for `CheckpointStore` (file-only today, matching msging's own
  `ProducerStore` file/db split).
- Live-tail chunking (byte-offset checkpoint granularity) — batch-only today.
- `S3SourceStore`/`S3ObjectStore` have no dedicated unit tests (structurally mirrors the
  uploader's own untested S3 backend; both would need `moto` or a real bucket).
- `reasoning_tokens` derivation from `thinking` blocks — real signal confirmed present in
  captured data (`docs/modules/normalizer.md` §6.4), left as `null` per §14 Q-reasoning; the
  user explicitly deferred implementing this when asked.
- Per-developer synthetic `producer_id` (RFC §5.2's original design) — §2.1 decision 1 above.
