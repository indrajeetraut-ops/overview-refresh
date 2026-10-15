# trace_utils

A small CLI to **explore, search, and extract** agent traces (Claude Code / Codex) published
as Hugging Face datasets — parquet or JSONL, streamed straight from HF with **no download
required**, or materialized to local disk / S3 when you want it.

Built on DuckDB. Works identically on a laptop and on an AWS instance.

---

## Install

Nothing to do — the wrapper bootstraps its own virtualenv on first run:

```bash
cd trace_utils
./utils/trace.sh --help          # creates trace_util.venv, installs, then runs
```

- The venv is named **`trace_util.venv`** (distinctive, easy to find and clean up).
- `./utils/trace.sh --reset` force-recreates the venv (e.g. after a dependency change).
- Override the base interpreter with `PYTHON=/path/to/python3 ./utils/trace.sh …`.

Prefer to manage it yourself? A plain venv works too:

```bash
python -m venv trace_util.venv && ./trace_util.venv/bin/pip install -e .
./trace_util.venv/bin/python -m trace_utils.cli_trace --help
```

The examples below use `trace_utils` as shorthand for `./utils/trace.sh` (equivalently
`./trace_util.venv/bin/python -m trace_utils.cli_trace`).

---

## Two ways to point it at data

**A. Directly by repo id / URL** — no config needed, great for a quick look:

```bash
trace_utils explore ultralazr/claude-code-traces
```

**B. Config file** — name your datasets once, refer to them by name. Copy the example:

```bash
cp config/datasets.example.yaml config/datasets.yaml
```

```yaml
# config/datasets.yaml
datasets:
  - name: ultralazr-cc
    url: https://huggingface.co/datasets/ultralazr/claude-code-traces
  - name: trace-commons
    url: https://huggingface.co/datasets/trace-commons/agent-traces
    subpath: sessions/claude_code      # optional: scope to one tree inside the repo
  - name: nlile-merged
    url: hf://datasets/nlile/misc-merged-claude-code-traces-v1   # hf:// form also fine

source:
  mode: stream                         # stream (no download) | download
  cache_dir: ~/.talium/trace_cache     # local dir OR s3://bucket/prefix
  hf_token_env: HF_TOKEN               # name of the env var holding your HF token (optional)
```

- `url` accepts the web URL, the `hf://datasets/org/name` form, or bare `org/name`.
- **Secrets never go in YAML.** Reference env vars with `${VAR}`; the HF token is read from the
  env var named by `hf_token_env` (defaults to `HF_TOKEN`).
- After this, use the name anywhere a dataset is expected: `trace_utils explore ultralazr-cc`.

---

## Workflow: discover → search → view → extract

### 1. See what datasets you have

```bash
trace_utils datasets
```
```
name           repo_id                        fmt      files   size
ultralazr-cc   ultralazr/claude-code-traces   jsonl    1       4.1 MB
trace-commons  trace-commons/agent-traces     jsonl    28      127.4 MB
nlile-merged   nlile/misc-merged-…-v1         parquet  11      1158.0 MB
```

### 2. Discover the shape — *what can I search for?*

`explore` reads the data and prints every **field path** (nested paths flattened to dotted
form), how often it's populated, and real sample values. This is how you learn the field names
and values to search on.

```bash
trace_utils explore ultralazr/claude-code-traces
```
```
FIELD PATH                     TYPE   COVERAGE  SAMPLE / TOP VALUES
type                           str    100%      file-history-snapshot, user, assistant
message.role                   str     62%      user, assistant
message.content[].type         str     52%      thinking, tool_use, text
message.model                  str     36%      claude-opus-4-6, claude-sonnet-4-6
message.usage.output_tokens    int     36%      1 … 1116
permissionMode                 str     10%      default, plan
```

Options: `--full` scans more rows (up to 50k) for wider coverage; `--sessions` lists sessions
instead of the shape.

```bash
trace_utils explore ultralazr/claude-code-traces --sessions
```
```
SESSION                                EVENTS  FIRST                LAST                 MODELS
e830fcb5-bb80-4214-8d16-9677a87c713b   876     2026-03-25 08:26:10  2026-03-28 21:48:24  claude-opus-4-6, …
```

### 3. Search for the sessions you care about

**Full-text** — you choose which field to search (whole content, or just a prompt field):

```bash
trace_utils search ultralazr/claude-code-traces --in message.content "sound"
trace_utils search ultralazr/claude-code-traces --in message.content "playwright" --regex
```

**Structured filters** — `field=value`, `!=`, `~regex`, combined with `&&` (AND):

```bash
trace_utils search ultralazr/claude-code-traces --where "message.model=claude-opus-4-6"
trace_utils search ultralazr/claude-code-traces \
  --where "message.model=claude-opus-4-6 && permissionMode=default"
```

Combine both — filter *and* full-text at once:

```bash
trace_utils search ultralazr/claude-code-traces \
  --where "message.model=claude-opus-4-6" --in message.content "refactor"
```

A session matches if **any** of its events match. Field paths come straight from `explore`.

### 4. View one session's transcript

```bash
trace_utils show ultralazr/claude-code-traces e830fcb5-bb80-4214-8d16-9677a87c713b
trace_utils show ultralazr/claude-code-traces e830fcb5-…  --raw   # raw event JSON
```

### 5. Extract sessions/files to disk or S3

```bash
# one session
trace_utils extract ultralazr/claude-code-traces \
  --session e830fcb5-… --out ./out --format jsonl

# everything a filter matched
trace_utils extract ultralazr/claude-code-traces \
  --where "message.model=claude-opus-4-6" --out ./out --format parquet

# a raw file subset (glob), straight to S3
trace_utils extract trace-commons/agent-traces \
  --glob "sessions/codex/*.jsonl" --out s3://my-bucket/codex/
```

`--format` is `jsonl` (default), `json`, or `parquet`.

---

## Scoping any command: one file vs all

`--glob`, `--session`, and `--where` work across `explore` / `search` / `extract`, so
"just this one jsonl" vs "the whole dataset" is a single flag:

```bash
# search inside ONE file
trace_utils search trace-commons/agent-traces \
  --glob "sessions/claude_code/07b57159*.jsonl" --in message.content "test"

# search the WHOLE dataset — drop the --glob
trace_utils search trace-commons/agent-traces --in message.content "test"
```

| Selector | Means |
|---|---|
| *(none)* | whole dataset |
| `--session <id>` | one session (matched to its file, else filtered at query time) |
| `--glob "<pattern>"` | a file subset / a single jsonl (fnmatch on repo-relative path) |
| `--where "<preds>"` | filter-matched sessions (search / extract) |

---

## Stream vs download

- **Stream (default):** DuckDB reads from `hf://` on demand — nothing is stored locally.
- **Download:** add `--download` (or set `source.mode: download`) to materialize the selected
  files into `cache_dir` once, then query them locally — faster for repeated work, and the way
  to run offline. Point `cache_dir` at `s3://…` on AWS or a local path on your laptop.

```bash
trace_utils explore ultralazr/claude-code-traces --download --sessions
```

---

## Private datasets / rate limits

Set a token in the env var named by `hf_token_env` (default `HF_TOKEN`):

```bash
export HF_TOKEN=hf_xxx
trace_utils explore my-org/private-traces
```

---

## Other

- `--debug` on any command raises log verbosity (structured JSON logs go to **stderr**; command
  output goes to **stdout**, so `2>/dev/null` gives you clean tables).
- Run the tests: `./trace_util.venv/bin/python -m pytest -q`.
- Design & scope: `docs/design/2026-07-13-trace-viewer-design.md`. Verification: `docs/TEST_REPORT.md`.
- Not yet: semantic search and a web UI (both v2).
