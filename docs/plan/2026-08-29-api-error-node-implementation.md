# Plan: `api_error` node in the causal graph (integ)

> **SHELVED 2026-08-29 — do not implement.** A real-corpus measurement (`~/.claude/projects`, 80
> sessions) showed `api_error` is not worth tracking: 6/80 sessions affected, ~126 s total backoff,
> all server-side (429/529/overload) with no user lever, and `retried_tokens_est` is phantom cost
> (failed requests aren't billed). The `api_error` metric is **dropped** — see
> `docs/design/2026-08-28-causal-graph-github-integration-feasibility.md` §4.C. Separately, this plan
> was written against the `system`-form test fixture: **real records use different field names**
> (`error.status`, `retryAttempt`, `maxRetries`, `retryInMs` — not `status`/`retryCount`/`message`),
> so the normalizer's `_make_api_error` currently yields all-null payloads (a normalizer bug, filed
> independently of this decision). Kept as a record; only revisit for an *aggregate fleet
> reliability / backoff-time* surface, which is a different design.

**Status:** Shelved (superseded by the drop decision) · **Date:** 2026-08-29 · **Branch:** `integ`
**Builds on:** `ccdff4e` + `9c086b1` (compaction node, the exact template) · head `a2ce765` (schema 4.3.0)
**Companion:** `docs/design/2026-08-28-causal-graph-github-integration-feasibility.md` (§4.C, §5 — this closes the last graph-model loss)

---

## Nutshell

Promote the normalizer's `api_error` event to a first-class **`api_error` graph node**, mirroring
the compaction landing. Schema `4.3.0 → 4.4.0`. The node is **session-level / out-of-band** (design
§4.1 rule: no `promptId` ⇒ not turn-contained), linked back to the turn whose request failed via
`refs`, exactly as compaction links its turns.

Load-bearing decisions:
- **Out-of-band node, not turn-contained** — consistent with the design rule compaction already
  set; zero risk to turn-descendant consumers (`budget`, `turns`).
- **No named API** — read via `select("ApiError")`, same as compaction.
- **Normalizer already emits the event** for the `system` form; the graph is the only new code.
- **Scope fence:** the github-side `error_rollup` swap is *not* here — this plan only makes
  `api_error` extractable from the graph on `integ`.

Honest limit up front: across 32 recorded fixtures there is **exactly one** real api_error record,
and it is the `assistant`/`isApiErrorMessage` form the normalizer does **not** currently route to
`api_error` (§ Open questions). The typed-payload contract this plan builds on comes from the
`system`/`subtype:api_error` form, which today appears only in a hand-built test fixture.

---

## Terms

- **system form** — `{type:"system", subtype:"api_error", status, retryCount, retryAfterMs, requestId}`. Rich retry/HTTP metadata. Routed to `_make_api_error` by `_handle_system`.
- **synthetic form** — `{type:"assistant", isApiErrorMessage:true, message:{model:"<synthetic>"...}}`. A placeholder assistant turn; `status`/`retryCount` null. Routed to `_handle_assistant` today → becomes a normal assistant node.
- **interrupted turn** — the turn whose in-flight request the api_error belongs to. Resolved from `parent_id` (an api_error carries a real `parentUuid`; `promptId` is null).

---

## What exists already (evidence)

- **Normalizer emits `api_error`** (`_make_api_error`, `claude_code.py:1288`), payload:
  ```
  error_class          from _classify_error(status)   # server|overloaded|other; "other" when status not int
  http_status          status                          # int | null
  retryable            bool(record.retryable)
  retry_count          record.retryCount               # int | null
  retry_after_ms       record.retryAfterMs             # int | null
  provider_request_id  record.requestId                # str | null
  message              record.message                  # str (system) OR dict (synthetic)
  raw                  record
  ```
  `record_id = uuid`, `parent_id = parentUuid`, `event_ts = timestamp`.
- **Dispatch:** `_make_api_error` is called **only** from `_handle_system` (`claude_code.py:807`).
- **Turn segmentation drops it:** `promptId:null` ⇒ `_segment_turns` absorbs it into the open turn;
  the per-turn dispatch loop (`builder.py:724`) nodes only `tool_call`/`sub_agent_spawn`. Same
  situation compaction solved with a post-hoc `_attach_compactions` pass (`builder.py:565`).
- **Prevalence:** 1 api_error record in 32 recorded fixtures (`session-subagents.jsonl`), form =
  synthetic, `promptId:null`. The system form exists only in `tests/fixtures/normalizer/sessions.py::api_error_and_compaction_session`.

---

## Decision: how api_error attaches

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Out-of-band node + `refs.interrupted_turn_id`** (mirror compaction) | consistent with design §4.1 (no promptId ⇒ out-of-band); zero change to turn-descendant walks; exact `_attach_*` template | extractor reads a ref, not containment | **chosen** |
| Turn-contained child (`parent_id = turn`, in `turn.children`) | slightly more faithful; retry-token estimate is a plain descendant walk | mutates containment semantics — every turn-descendant consumer (`budget` `_descendants`, `turns` tool_count) must be re-audited | switch if a consumer needs api_error inside `contains`/`within` walks |
| Attribute on the turn (`had_api_error`, count) — no node | no schema node | multiple errors per turn lose identity; `select` can't enumerate; breaks one-node-per-event | never |

**Node shape** (typed attrs only; `message`/`raw` stay off the node, as compaction kept only typed attrs):
```
api_error
    error_class          str
    http_status          int | null
    retryable            bool
    retry_count          int | null
    retry_after_ms       int | null
    provider_request_id  str | null
refs
    interrupted_turn_id  str | null      # local turn id, or null when unresolvable
```
Turn side (mirror compaction's `is_after_compact` / `resumed_from_compaction_ids`):
```
turn.attrs.had_api_error = True
turn.refs.api_error_ids  = [<api_error node ids>]   # plural, via _append_ref
```

---

## Steps

### Step 1 — Amend the contract & design docs (FIRST; code cites the doc)

- `docs/design/causal-graph-design.md` — add `api_error` to the node-type catalog; state the
  out-of-band placement rule and the `interrupted_turn_id` link. **Fenced out:** NOT the query
  interface, NOT graph management — additive node only.
- `src/common/contracts/causal_graph/schemas/graph.schema.json` — add `"api_error"` to the node
  `type` enum and `node_types`; add the `api_error` attrs block; **bump `schema_version` 4.3.0 →
  4.4.0**.
- `src/common/contracts/causal_graph/query_language/fields.yaml` — add the `ApiError` public-field
  list (`ApiError.error_class`, `.http_status`, `.retryable`, `.retry_count`, `.retry_after_ms`,
  `.provider_request_id`).
- `src/common/contracts/causal_graph/manifest.yaml` — schema version to 4.4.0.
- `src/analysis/causal_graph/descriptions.py` — node + attr descriptions (feeds doc generators).
- `src/analysis/causal_graph/README.md` — node table + `attrs` reference block.

before → after (`graph.schema.json`):
```
before  node_types: [turn, user_prompt, assistant, tool, spawn, compaction]   schema_version 4.3.0
after   node_types: [turn, user_prompt, assistant, tool, spawn, compaction, api_error]   schema_version 4.4.0
```
Evolution: additive-only node type; consumers must ignore unknown node types; a new node type is a
minor bump (matches the compaction 4.1→4.2 precedent).

### Step 2 — `models.py`

- Add `NodeType.API_ERROR = "api_error"`.
- `SCHEMA_VERSION = "4.4.0"`.

### Step 3 — `builder.py`

- Add `_REQUIRED_PAYLOAD_FIELDS["api_error"] = frozenset({"error_class","http_status","retryable","retry_count","retry_after_ms","provider_request_id","raw"})` (the contract guard; `test_normalized_contract.py` asserts each is published).
- `_api_error_node(event) -> Node` — typed attrs above, `refs={"interrupted_turn_id": None}`, `id = event["record_id"]`.
- `_attach_api_errors(graph, turns, events)` — copy `_attach_compactions` structure: build
  `turn_id_by_record` from turn members; for each `api_error` event resolve `interrupted_turn_id =
  turn_id_by_record.get(event.parent_id)`; when resolved, set `turn.attrs.had_api_error=True`,
  `_append_ref(turn,"api_error_ids",node.id)`, and `node.refs["interrupted_turn_id"]=turn_id`;
  scan the **full event list** (not only turn members) so a pre-first-turn error still yields a
  node with a null ref. `graph.add(node)`; do **not** append to `graph.roots`.
- Wire the call immediately after `_attach_compactions(graph, turns, events)` (`builder.py:940`).

### Step 4 — query + registry

- `query_models.py` — add `"api_error"` to the selectable type set and the `"api_error":"ApiError"` name map.
- `field_registry.py` — `_TYPE_NAMES["apierror"] = NodeType.API_ERROR`; `_DEFAULTS[NodeType.API_ERROR] = ("ApiError.error_class","ApiError.http_status","ApiError.retry_count")`.

### Step 5 — rebuild the example corpus at 4.4.0

- Re-run the corpus/example builder (the `42b8dbb` step's mechanism) so committed example graphs and
  `examples/causal_graph/in_memory_consumer.py` carry `schema_version 4.4.0`. Real corpus sessions
  produce **zero** `api_error` nodes today (prevalence above) — so the corpus proves the schema bump
  and the no-op path, not the populated path.

### Step 6 — tests

- `test_builder.py` — using `sessions.py::api_error_and_compaction_session` (system form): one
  `api_error` node with the typed attrs; `refs.interrupted_turn_id` == the failing turn;
  `turn.attrs.had_api_error is True`; the node is **not** in `roots`. Add an orphan case
  (api_error before the first `promptId`) ⇒ node exists, `interrupted_turn_id is None`.
- `test_models.py` — `NodeType.API_ERROR` round-trips; `SCHEMA_VERSION == "4.4.0"`.
- `test_select.py` — `select(family,"ApiError", scope="tree", verbosity="full")` returns the node with the public fields.
- `test_contract_bundle.py` — schema/fields/manifest agree at 4.4.0; `ApiError` fields resolvable.
- `test_doc_generators.py` — regenerated node table includes `api_error`.

---

## Order & dependencies

```
Step 1 (contract)      independent; do first — Steps 2–4 cite it
Step 2 (NodeType)      blocks 3, 4
Step 3 (builder)       needs 2; blocks 5, 6
Step 4 (query/registry) needs 2
Step 5 (corpus)        needs 3
Step 6 (tests)         needs 2–4; corpus assertions need 5
```

---

## Files

```
docs/design/causal-graph-design.md                                   node-type catalog + placement rule
src/common/contracts/causal_graph/schemas/graph.schema.json          enum + attrs + schema_version 4.4.0
src/common/contracts/causal_graph/query_language/fields.yaml         ApiError public fields
src/common/contracts/causal_graph/manifest.yaml                      schema_version
src/analysis/causal_graph/descriptions.py                            node/attr descriptions
src/analysis/causal_graph/README.md                                  node table + attrs block
src/analysis/causal_graph/models.py                                  NodeType.API_ERROR; SCHEMA_VERSION
src/analysis/causal_graph/builder.py                                 required fields; _api_error_node; _attach_api_errors; wire
src/analysis/causal_graph/query_models.py                            selectable type + name map
src/analysis/causal_graph/field_registry.py                          type map + defaults
examples/causal_graph/in_memory_consumer.py                          rebuilt at 4.4.0
examples/causal_graph/graphs/**                                      rebuilt corpus graphs
tests/analysis/causal_graph/test_builder.py                          node + attach + orphan
tests/analysis/causal_graph/test_models.py                           enum + version
tests/analysis/causal_graph/test_select.py                           select ApiError
tests/analysis/causal_graph/test_contract_bundle.py                  contract agreement
tests/utils/test_doc_generators.py                                   regenerated tables
```

---

## Verification

Per step:
```
1  graph.schema.json validates; schema_version == 4.4.0; ApiError fields present in fields.yaml
2  NodeType.API_ERROR exists; models round-trip; SCHEMA_VERSION == "4.4.0"
3  api_error_and_compaction_session builds ONE api_error node; refs.interrupted_turn_id == failing turn;
   turn.had_api_error True; node absent from roots; orphan case → interrupted_turn_id None
4  select("ApiError") returns the node with public fields; unknown-field query still rejected
5  every committed example graph reports schema_version 4.4.0; real corpus yields 0 api_error nodes (documented no-op)
6  new tests green
```
Whole-suite bar: `PYTHONPATH=src venv/bin/python -m pytest tests/analysis/causal_graph tests/utils` green; the
compaction contract still holds (compaction nodes unchanged; `usage_known:False` untouched). An
assertion that would fail if reverted: `select("ApiError")` on the system-form fixture returns ≥1 node.

---

## Open questions

| Question | Working assumption used here |
|---|---|
| Which api_error form(s) appear in the **production** corpus, and at what rate? Recorded fixtures show 1 record, and it is the **synthetic** form the normalizer does not route to `api_error`. | The structured **system form** is canonical (52-transcript inventory, feasibility handoff §3/§4, VERIFIED); this plan builds on it. Settle by grepping `isApiErrorMessage`/`subtype:api_error` by `type` over a real corpus before shipping. If the synthetic form dominates, add a normalizer step (below) — otherwise the graph node is empty on real data. |
| Should the **synthetic form** (`type:assistant, isApiErrorMessage:true`) also become an api_error event? Today it lands as an assistant node. | Out of scope for the graph plan; it is a **normalizer** change in `_handle_assistant` (guard `isApiErrorMessage` → `_make_api_error` before assistant collapse). Flagged, not done. Do it iff the corpus grep says the synthetic form is common. Its `status`/`retryCount` are null, so `error_class` degrades to `"other"` and retry fields are null — the node is sparse for that form. |
| Multiple api_error records per failed request (retry storm) — one node each, or coalesce? | One node per event (one-node-per-event invariant). A per-turn extractor sums `retry_count` across the turn's `api_error_ids`, matching fold's `error_rollup`. |
| `retried_tokens_est` (fold field) — computed where? | Not a node attr. It is a github-side extractor derivation (`retry_count × interrupted turn's assistant tokens`), and lands with the integration cutover, not this plan. |
```
