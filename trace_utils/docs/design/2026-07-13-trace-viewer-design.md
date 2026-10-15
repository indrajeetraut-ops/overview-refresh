# Design: `trace_utils` — Agent Trace Viewer (v1 CLI)

**Status:** Approved design, ready for implementation plan · **Date:** 2026-07-13
**Parent folder:** `trace_utils/`
**Follows:** root `CLAUDE.md` governance (structured JSON logging, pytest, CLI naming, secrets via env).

---

## 1. Purpose

Publicly available Claude Code / Codex agent traces on Hugging Face have no working data
viewer. This tool lets a developer point at one or more HF trace datasets and:

1. **Discover the shape** of a dataset directly from the data — the field paths that actually
   exist and the values they take — *before* searching.
2. **Filter** sessions with structured predicates (`message.model=claude-opus-4 && permissionMode=default`).
3. **Full-text search** message content, with the search scope chosen by the user (whole
   content, just the prompt, or a specific field).
4. **Show** a single session's transcript.
5. **Extract** selected sessions/files to local disk or S3.

Runs identically on a laptop and on an AWS instance. Streams from HF by default (no download);
download and extract are explicit, user-driven operations.

## 2. Scope

**In scope (v1, CLI):** the five capabilities above, driven by a YAML dataset list, over the
three dataset packagings observed (see §4), dataset-native (no forced cross-dataset schema).

**Out of scope (named, not built now):**
- **Web frontend** — v2. The core is built as a library (`src/trace_utils/`) with the CLI as a
  thin layer, so a web/API layer wraps the same functions later without rework.
- **Semantic / embedding search** — v2. Structured filters + full-text only for v1.
- **Cross-dataset unified schema** — rejected for v1 (decided): discovery/filter/search operate
  on each dataset's own discovered field paths. A thin best-effort common view
  (`session_id`, `vendor`, `timestamp`, `text`) sits on top for listing only.

## 3. Key decisions

| # | Decision | Rationale |
|---|---|---|
| K1 | **DuckDB** is the scan/query engine | Reads parquet + jsonl; globs local or `hf://` paths; streams remote with predicate pushdown (no download); FTS extension for full-text; one `pip install`; fast over GBs; same code path local/AWS. |
| K2 | **Dataset-native**, not normalized | Matches "show me what's actually in THIS data"; ships faster; no per-dataset mapping code hiding the raw shape. |
| K3 | **Field profiler** produces the "discovered shape" | Raw JSONL has no flat `model` column — it is nested at `message.model`, tokens at `message.usage.output_tokens`, and each event type has different keys. The profiler flattens nested JSON to dotted field paths with coverage% and sample values, which is the concrete answer to "how would I know what to search for." |
| K4 | **Shared selectors** across commands | `--session`, `--glob`, `--where` scope `explore`/`search`/`show`/`extract` consistently, so "one jsonl vs all" is one flag, not four code paths. |
| K5 | **Single `cli_trace.py`** with subcommands | A viewer wants cohesive subcommands (`explore/search/show/extract/datasets`). Honors the `CLAUDE.md` mandate's spirit ("no bare `cli.py`; name describes the tool") with one shell wrapper `utils/trace.sh`. |
| K6 | **Stream default, download + extract explicit** | User preference: browse without downloading first; make materialization explicit. Two distinct write ops (§8). |

## 4. Data findings (from live HF inspection, 2026-07-13)

Three example datasets, three packagings — the tool must handle all three:

| Repo | Packaging | Notes |
|---|---|---|
| `nlile/misc-merged-claude-code-traces-v1` | 11 sharded **parquet** files (~1.2 GB) under `data/` | pure columnar |
| `trace-commons/agent-traces` | 1 **parquet** (`data/train-00000-of-00001.parquet`, 70 MB) **and** raw `sessions/<vendor>/*.jsonl` trees (`claude_code`, `codex`, `cursor`, `opencode`, `pi`) | mixed; `subpath` selects a vendor tree |
| `ultralazr/claude-code-traces` | one **JSONL file per session** (UUID-named) under `data/` + `manifest.jsonl` | pure jsonl, file == session |

**Underlying unit is identical everywhere:** a *session* = an ordered stream of *events*.
The JSONL is the raw Claude Code transcript format — representative event keys:
`sessionId`, `uuid`, `parentUuid`, `type` (`user`/`assistant`/`tool_use`/`tool_result`/
`file-history-snapshot`/…), `message{role, content, model?, usage?}`, `timestamp`, `cwd`,
`gitBranch`, `version`, `permissionMode`, `userType`. Parquet repos carry the same content
columnarized (some fields as columns, some still nested).

**Implication for discovery:** field paths must be discovered per dataset and may be nested;
`model` and token usage live *inside* `message`, and event types have heterogeneous keys.

## 5. Architecture & components

Each is a focused module under `src/trace_utils/`; the CLI is a thin layer over them.

```
YAML config ──▶ config ──▶ sources (resolve to path globs)
                                   │
                                   ▼
                              engine (DuckDB: httpfs + json + fts)
                                   │
              ┌────────────────────┼───────────────────────┬───────────────┐
              ▼                    ▼                        ▼               ▼
          profiler             sessions                  search          extract
       (field paths)        (session list)         (filter + FTS)   (write to disk/S3)
              │                    │                        │               │
              └──────────── render / stdout ────────────────┘         out files
```

| Module | Responsibility | Key interface (illustrative) |
|---|---|---|
| `config.py` | Load & validate YAML; env interpolation; return typed config. No other module parses YAML. | `load_config(path) -> Config`; `Config.datasets[]`, `.source.mode`, `.source.cache_dir` |
| `sources.py` | Resolve a dataset entry (+ optional `subpath`, selectors) to a concrete set of readable path globs. Detect format (parquet/jsonl) from the HF file tree. `stream` → `hf://datasets/…/**/*.{parquet,jsonl}`; `download` → local dir or `s3://…`. | `resolve(dataset, selector) -> ResolvedSource(globs, fmt)` |
| `engine.py` | DuckDB connection wrapper; installs/loads `httpfs`, `json`, `fts`; runs scans/queries; returns dict rows. Central place for `read_parquet`/`read_json_auto` and error handling. | `Engine.scan(globs, fmt)`, `Engine.query(sql, params)` |
| `profiler.py` | Sample rows via engine (default sample; `--full` = all), flatten nested JSON to **dotted field paths**, compute type / coverage% / top-or-range values → discovered-shape table. | `profile(source, sample=…) -> list[FieldStat]` |
| `sessions.py` | Group events by session id; summarize (event count, time span, models seen, first user prompt) → session list; the thin common view. | `list_sessions(source, selector) -> list[SessionSummary]` |
| `search.py` | Compile `--where "a=x && b=y"` predicates + `--in <field> "<term>"` full-text into DuckDB SQL / FTS over discovered field paths; return matching session summaries. | `search(source, where, fts) -> list[SessionSummary]` |
| `render.py` | Human-readable output: shape table, session list table, role-tagged transcript for `show` (tool calls collapsible), `--raw` passthrough. Uses `rich`. | `render_shape`, `render_sessions`, `render_transcript` |
| `logging.py` | Structured JSON logging (`structlog`), one logger per module; correlation fields (`dataset`, `session_id`); **never logs content**. | `get_logger(module)` |
| `cli_trace.py` | Subcommands wiring the above; run as `python -m trace_utils.cli_trace …`. | `datasets / explore / search / show / extract` |

**Profiler uniformity note:** both parquet and jsonl are profiled through one path — the engine
returns sampled rows as JSON (`to_json(…)` / native), and the profiler flattens uniformly. No
separate parquet-vs-jsonl profiling code.

## 6. Configuration (YAML)

One file, developer-facing, restart-to-reload, secrets only via env (`${VAR}` interpolation).

```yaml
datasets:
  - name: ultralazr-cc
    url: https://huggingface.co/datasets/ultralazr/claude-code-traces
  - name: trace-commons
    url: https://huggingface.co/datasets/trace-commons/agent-traces
    subpath: sessions/codex          # optional: scope to one tree within the repo
  - name: nlile-merged
    url: hf://datasets/nlile/misc-merged-claude-code-traces-v1   # hf:// form also accepted

source:
  mode: stream                        # stream (default, no download) | download
  cache_dir: ~/.talium/trace_cache    # local dir or s3://bucket/prefix
  hf_token_env: HF_TOKEN              # optional; name of env var holding the HF token
```

- `url` accepts the web URL or the `hf://datasets/org/name` form; `sources.py` normalizes both.
- Secrets (HF token, AWS creds) come from the environment, never the YAML.

## 7. Commands & selectors

```
trace_utils datasets                                   # list configured datasets + file inventory
trace_utils explore <ds> [selector] [--full] [--fields | --sessions]
trace_utils search  <ds> [selector] --where "<preds>" [--in <field> "<term>"]
trace_utils show    <ds> <session_id> [--raw]
trace_utils extract <ds> [selector] --out <dir|s3://…> [--format jsonl|json|parquet]
```

**`explore`** default output = the discovered-shape table (`--fields`); `--sessions` lists
sessions instead. Illustrative shape output:

```
Dataset: ultralazr-cc  ·  1 file · ~3.8k events · 1 session
FIELD PATH                     TYPE   COVERAGE  SAMPLE / TOP VALUES
type                           str    100%      assistant(61%) user(31%) file-history-snapshot(8%)
message.role                   str     92%      assistant, user
message.model                  str     59%      claude-opus-4, claude-sonnet-4
message.usage.output_tokens    int     59%      12 … 4096
permissionMode                 str     31%      default, plan
gitBranch                      str     31%      HEAD, main
```

**Shared selectors** (scope any of `explore`/`search`/`show`/`extract`):

| Selector | Means | Applies to |
|---|---|---|
| *(none)* | whole dataset | all |
| `--session <id>` | one session | explore/search/show/extract |
| `--glob "sessions/codex/*.jsonl"` | a file subset / a single jsonl | explore/search/extract |
| `--where "<preds>"` | filter-matched sessions | search/extract |

- **Search one jsonl:** `search <ds> --glob ".../07b57159….jsonl" --in message.content "sound"`
- **Search all:** drop `--glob`.
- **Where predicates:** `field=value` joined by `&&` (boolean AND for v1); `field` is a
  discovered dotted path; also `!=`, and `field~regex` for regex. (OR/`||` is a noted v1.1 extension.)
- **Full-text `--in <field> "<term>"`:** user chooses the field to search (`message.content`,
  or a narrower path); term is substring/regex via DuckDB, FTS index used when built (§8).

## 8. Stream / download / extract semantics

Three distinct disk interactions, deliberately separated:

| Operation | Trigger | What it does | Where |
|---|---|---|---|
| **Stream** (default) | `mode: stream` | DuckDB reads from `hf://` on demand; nothing persisted except the optional index below | — |
| **Local session-index** | built on first `explore`/`search` in stream mode (or `--reindex`) | One compact summary row per session (id, vendor, first prompt, models, counts, time span) in `cache_dir/<ds>.duckdb`, so repeat discovery/search is instant without re-downloading full content; full message content fetched on demand only for `show`. | `cache_dir` (local or s3) |
| **Download** | `mode: download` or `--download` | Bulk-materialize the whole dataset for offline/fast repeat querying | `cache_dir` (local dir or `s3://…`) |
| **Extract** | `extract` command | Pull a *selected subset* (a session, a glob, or `--where` matches) out to files you keep/share | `--out <dir\|s3://…>` |

**Download vs extract** differ by intent: download = whole dataset for the tool's own repeat
use; extract = targeted results written out in a chosen format. Both can target `s3://` (via
DuckDB `httpfs`/`COPY`); `boto3` is an optional dependency only if S3 listing beyond DuckDB is needed.

## 9. Cross-cutting mandates (root `CLAUDE.md`)

- **Structured JSON logging** (`structlog`): one logger per module; fields `module`, `event`,
  `dataset`, `session_id` where applicable; state changes at info, retries/fallbacks at warning,
  data-loss/format errors at error. **Content (prompts/responses/tool IO) never appears in logs.**
- **pytest**, tests mirror the source tree; a touched module's tests run.
- **CLI naming:** `cli_trace.py`, run as `python -m trace_utils.cli_trace`; shell wrapper
  `utils/trace.sh` calls it.
- **Dependencies** installed explicitly and recorded in `trace_utils/requirements.txt`.
- **Libraries over hand-rolled** (rule 5): `duckdb` (engine/FTS), `pyyaml` (config),
  `huggingface_hub` (repo tree listing / resolve URLs / downloads), `structlog` (logging),
  `typer` + `rich` (CLI + rendering). No bespoke utilities for solved problems.
- **TODO markers:** anything deferred during implementation gets an in-source `TODO:` with a
  description, removed when done.
- No "co-authored by Claude" or Claude references anywhere in code or commits.

## 10. Testing & debuggability

- **Fixtures (committed, tiny):** `tests/fixtures/` — a 3–5 line jsonl session (representative
  event types: `user`, `assistant` with `message.model`+`usage`, `tool_use`,
  `file-history-snapshot`) and a small parquet built from equivalent rows.
- **Golden outputs:** profiler shape table, session list, and a `--where` + `--in` search
  result set, asserted against goldens.
- **Isolation:** every module tested against local fixtures with no network; HF/S3 access is
  behind `sources.py`/`engine.py` seams that fixtures bypass by pointing globs at local files.
- **Debuggability:** `--debug` raises log verbosity; `explore --full` and `show --raw` are the
  "show its work" surfaces for a given dataset/session id.

## 11. Folder layout

```
trace_utils/
├── docs/design/2026-07-13-trace-viewer-design.md   # this doc
├── config/
│   └── datasets.example.yaml
├── src/trace_utils/
│   ├── __init__.py
│   ├── config.py
│   ├── sources.py
│   ├── engine.py
│   ├── profiler.py
│   ├── sessions.py
│   ├── search.py
│   ├── render.py
│   ├── logging.py
│   └── cli_trace.py
├── utils/
│   └── trace.sh
├── tests/
│   ├── fixtures/
│   └── test_*.py
├── pyproject.toml            # package metadata; entry point python -m trace_utils.cli_trace
└── requirements.txt
```

## 12. Open items / v2

- **Web frontend** (v2) — thin API + UI over the same `src/trace_utils/` functions.
- **Semantic search** (v2) — embeddings + vector store; the `--in` scope model extends to it.
- **`||` / OR predicates and grouped expressions** in `--where` — v1.1 if needed.
- **Gemini/context-cache-style provider specifics** — not relevant to this viewer.
- **Manifest/redaction awareness** (e.g. ultralazr's `manifest.jsonl` redaction keys) — surfaced
  as metadata only; no dedup/verification logic in v1.
```
