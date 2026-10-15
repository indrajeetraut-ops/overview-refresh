# Causal Graph Python API

This package consumes storage-neutral causal-graph JSON dictionaries. Your code retrieves
those dictionaries from a file, database, object store, message, or memory; the package builds
and analyzes them without knowing their origin.

The authoritative machine contract starts at
[`manifest.yaml`](../../common/contracts/causal_graph/manifest.yaml). This README explains that
contract for Python developers. Human CLI usage belongs in
[`README-graphcli.md`](README-graphcli.md).

## Public surface

```python
from analysis.causal_graph import (
    GraphContext, GraphContextError,
    build_graph, get_graph_context, get_load_report, get_metric,
    budget, goods, memory_usage, list_tools, tool_usage, turns, subagents, gitops,
    select, query,
)
```

Named APIs and `query()` remain free functions receiving a `Family`. `GraphContext` owns only
loading, lookup, and unloading; there are deliberately no `context.budget()` or
`context.query()` duplicates.

## Build a graph dictionary

`build_graph()` accepts exactly one normalized envelope `sessions[]` member and returns a plain
JSON-compatible dictionary. It does no source or destination I/O.

```python
import json
from analysis.causal_graph import build_graph

# Retrieval is application code: this could instead be a DB row, S3 object, or message.
normalized_session = json.loads(open("normalized-session.json").read())
graph_dict = build_graph(normalized_session)
assert isinstance(graph_dict, dict)

# Storage is also application code.
open("graph.json", "w").write(json.dumps(graph_dict))
```

The precise dictionary-in/dictionary-out contract is
[`build.yaml`](../../common/contracts/causal_graph/interfaces/build.yaml); the result conforms
to [`graph.schema.json`](../../common/contracts/causal_graph/schemas/graph.schema.json).

```bash
PYTHONPATH=src venv/bin/python examples/causal_graph/build_graph.py \
  tests/fixtures/causal_graph/tiny_session.json /tmp/talium-graph.json
```

`build_family()` remains available for code already holding a complete normalized envelope.
It uses the internal Graph-producing builder directly, without serializing and reparsing each
session.

## Load self, discover subagents, and load the tree

Start with the master graph dictionary. `load_self()` never retrieves children.

```python
context = GraphContext()
context.load_self(master_graph_dict)

# This inspects spawn nodes in the master; it does not load child graphs.
discovered = subagents(context.family)
child_ids = [row["session_id"] for row in discovered["subagents"]]
```

Supply one source-specific batch resolver to load descendants:

```python
def resolve_graphs(session_ids: list[str]) -> dict[str, dict]:
    rows = database.select_graphs(session_ids)
    return {row["session_id"]: row["graph_json"] for row in rows}

context.load_tree(resolve_graphs)
family = context.family
```

Loading is eager and breadth-first. Every unresolved ID at one depth is passed in one
`list[str]` batch. Missing keys produce a partial family; exceptions are failures. A returned
batch is validated before any member is registered, and already loaded IDs are not requested
or materialized again.

The loader accepts the current graph schema and older versions in the same major version. It
rejects malformed versions, older majors, and future versions, and preserves the source
envelope's version on the loaded `Graph`.

Every graph keeps its universal `session_id`:

```python
child_graph = context.resolve(child_ids[0])
assert get_graph_context(child_ids[0]) is context
```

A process may hold several contexts for different masters. One active session ID may belong to
only one context:

```python
context_a = GraphContext(); context_a.load_self(master_a)
context_b = GraphContext(); context_b.load_self(master_b)
assert get_graph_context(master_a["session_id"]) is context_a
assert get_graph_context(master_b["session_id"]) is context_b
```

The lifecycle and resolver contract is
[`context.yaml`](../../common/contracts/causal_graph/interfaces/context.yaml).

## Named analysis APIs

Use these for questions with stable domain results. Their complete inputs, return envelopes,
field descriptions, filters, and error codes are authoritative in
[`named_apis.yaml`](../../common/contracts/causal_graph/interfaces/named_apis.yaml).

```python
usage = budget(family, scope="tree", by="model")
artifacts = goods(family, scope="tree", artifact_class="code", extension=".py")
memory = memory_usage(family, scope="tree")
inventory = list_tools(family, scope="tree")
failures = tool_usage(
    family, scope="tree", tool_class="harness", name="Bash",
    outcome="failure", invocation_matches=r"pytest|ruff", limit=20,
)
turn = turns(family, turn_id={"session_id": family.master.session_id, "turn_num": 1})
delegations = subagents(family)
commits = gitops(family, scope="tree", operation="commit", limit=20)
```

`memory_usage()` reports memory Tool calls and distinct files, separate read/write activity,
and full usage for each associated assistant response. A response containing another Tool or
Spawn is classified as `mixed`; its usage is not guessed or prorated. The `files` list keeps
each classified path and contributing change, while Edit hunks provide a net line delta only
when their coordinates are usable. Memory contents and inferred final sizes are never exposed.
`goods()` excludes those memory-classified file records, including when the same Tool also
reports an ordinary artifact.

<!-- gen:graph:named-api -->
| API | Stable signature | Result kind |
|---|---|---|
| `budget` | `budget(family, *, scope='tree', by='model', model=None, permission_mode=None, turn=None)` | `budget_report` |
| `goods` | `goods(family, *, scope='tree', artifact_class=None, extension=None)` | `goods_report` |
| `memory_usage` | `memory_usage(family, *, scope='tree')` | `memory_usage_report` |
| `list_tools` | `list_tools(family, *, scope='tree')` | `tool_list_report` |
| `tool_usage` | `tool_usage(family, *, scope='tree', tool_class=None, name=None, outcome='all', invocation_matches=None, limit=None)` | `tool_usage_report` |
| `turns` | `turns(family, *, turn_id, node_type=None)` | `turn_report` |
| `subagents` | `subagents(family, *, scope='self')` | `subagents_report` |
| `gitops` | `gitops(family, *, scope='tree', operation=None, limit=None)` | `gitops_report` |

The complete request fields, response envelopes, and named error codes are authored in [`named_apis.yaml`](../../common/contracts/causal_graph/interfaces/named_apis.yaml). QuerySpec construction and caching are internal implementation details of these wrappers.
<!-- /gen:graph:named-api -->

## Handwritten selection

Use `select()` for exploratory node retrieval. It creates and validates QuerySpec internally
and returns the same `QueryResult` object as `query()`.

```python
result = select(
    family,
    "Tool",
    where="Tool.name == 'Bash' AND Tool.outcome == 'failure'",
    scope="tree",
    fields=["Tool.cmd", "Tool.input.command"],
    order_by=[{"field": "Tool.ts", "direction": "asc"}],
    limit=20,
    verbosity="summary",
)
payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
```

Valid node types are `Node`, `Turn`, `UserPrompt`, `Assistant`, `Tool`, `Spawn`, and
`Compaction`. Scope is
`self`, `tree`, `subagents`, a session ID string, or the explicit QuerySpec scope object.
Verbosity is `ref`, `summary`, or `full`. Explicit ordering is stable and nulls sort last in
both directions.

### Filter expressions

Expressions are strings passed through `where=`. The exact grammar is
[`filter.lark`](../../common/contracts/causal_graph/query_language/filter.lark); semantics are
authoritative in
[`semantics.yaml`](../../common/contracts/causal_graph/query_language/semantics.yaml).

| Form | Meaning | Example |
|---|---|---|
| `==`, `!=` | equality/inequality | `Tool.name == 'Read'` |
| `>`, `>=`, `<`, `<=` | numeric or timestamp ordering | `Tool.duration_ms >= 1000` |
| `IN [...]` | equality against any literal | `Tool.name IN ['Read', 'Edit']` |
| `STARTS_WITH` | case-sensitive prefix | `Tool.name STARTS_WITH 'web'` |
| `ENDS_WITH` | case-sensitive suffix | `Tool.input.command ENDS_WITH '.py'` |
| `CONTAINS` | case-sensitive substring | `Tool.input.command CONTAINS 'pytest'` |
| `MATCHES` | RE2 regular expression | `Tool.input.command MATCHES 'pytest.*causal'` |
| `AND`, `OR`, `NOT` | boolean composition | `A AND NOT B` |
| `CASEFOLD(field)` | Unicode case-folded string | `CASEFOLD(Tool.name) == 'bash'` |
| `LENGTH(field)` | string/list/object length | `LENGTH(Tool.cmd) > 1` |

`NOT` binds before `AND`, which binds before `OR`; parentheses override precedence. String
literals may use single or double quotes and comparisons are case-sensitive unless `CASEFOLD`
is explicit. Literals also include numbers, `true`, `false`, and `null`. Missing fields evaluate
as null. Equality on a list-valued left operand means membership, so `Tool.cmd == 'git'` finds
tools whose extracted command list contains `git`. Unknown fields, functions, operators, and
malformed expressions are errors.

Public fields and types are in
[`fields.yaml`](../../common/contracts/causal_graph/query_language/fields.yaml) and the per-node
`attrs` catalog in `graph.schema.json`. Common examples are `Tool.name`, `Tool.cmd`,
`Tool.outcome`, `Spawn.child_session_id`, `UserPrompt.preview`, `model`, `model_id`, and
`harness`. Configured memory activity is directly selectable with
`Tool.memory_access == true`; per-file `is_memory` and `memory_kind` values are available in
full Tool-node results.

## Advanced QuerySpec

Use `query()` for walks, grouping, aggregation, and `having`. The authoritative serialized
request is [`query_spec.schema.json`](../../common/contracts/causal_graph/schemas/query_spec.schema.json),
not Python source. Results conform to
[`query_result.schema.json`](../../common/contracts/causal_graph/schemas/query_result.schema.json).

This request runs against the committed `query_api_sample.json` corpus graph:

```python
spec = {
    "version": "1",
    "scope": {"kind": "self"},
    "select": {"type": "Tool", "where": "Tool.name == 'Bash'"},
    "group_by": ["Tool.outcome"],
    "aggregates": [{"name": "count", "function": "COUNT"}],
    "order_by": [{"field": "count", "direction": "desc"}],
    "return": {"kind": "groups", "verbosity": "ref"},
}
result = query(family, spec)
```

Aggregate functions are `COUNT`, `SUM`, `AVG`, `MIN`, `MAX`, and `MEDIAN`; walk relations are
`within`, `contains`, and `spawned`. `select()` intentionally omits those advanced features.

## Result and error shapes

`QueryResult` contains `version`, `status`, `kind`, exactly one of `items`, `groups`, or
`aggregate`, plus `truncated` and `warnings`. Node identities are session-scoped:
`{session_id, turn_num}` for turns and `{session_id, node_id}` for all other nodes.

Named APIs return stable dictionaries documented in `named_apis.yaml`; `NamedApiError.to_dict()`
returns their error envelope. `GraphContextError.to_dict()` returns `version`, stable `code`,
message, UTC timestamp, master/session identities, and details. Lifecycle codes are listed in
`context.yaml`.

## Reports, metrics, unloading, and reload

Every lifecycle method returns the same retained report snapshot as
`get_load_report(master_session_id)`. Omitting the argument returns all process reports. Reports
contain current loaded/unresolved state and every timestamped load/unload operation. Returned
values are deep copies.

```python
one_report = get_load_report(family.master.session_id)
all_reports = get_load_report()
metrics = get_metric(family.master.session_id)  # one master is required
```

Metrics are process-memory JSON series with name, type, labels, value, and update timestamp.
They cover lifecycle, resolver, query, named API, cache, gauge, duration, and failure events.
There is no flush, exporter, persistence, server, or no-argument metric getter.

`unload(child_session_id)` releases that non-master and all descendants; a master ID is always
rejected. `unload_tree()` releases the complete family and registry entries while retaining
reports and metrics. The same master may then load into a new context and continue its retained
history.

```python
context.unload(child_session_id)
context.unload_tree()
replacement = GraphContext(); replacement.load_self(master_graph_dict)
```

Lazy loading is deliberately deferred; current loading is explicit and eager.

## Persisted graph reference

<!-- gen:graph:schema -->
```json
{
  "schema_version": "4.3.0",
  "node_types": [
    "turn",
    "user_prompt",
    "assistant",
    "tool",
    "spawn",
    "compaction"
  ],
  "node": {
    "id": "str",
    "type": "str  (one of node_types)",
    "ts": "str | null",
    "attrs": "dict",
    "meta": "dict",
    "children": "list[str]",
    "parent_id": "str | null  (null on a turn or session-level out-of-band node; roots is the ordered turn index)",
    "refs": "dict  (non-containment node/session references; local node ids or explicit session ids)"
  },
  "envelope": {
    "schema_version": "str",
    "session_id": "str",
    "parent_session_id": "str | null",
    "vendor": "str  (claude_code | codex)",
    "source": "str  (jsonl | otel)",
    "built": "str  (complete | partial)",
    "meta": "dict  (title, objective, budget)",
    "nodes": "list[node]",
    "roots": "list[str]  (turn ids, in order)"
  }
}
```

`attrs` by node type:

```
assistant
    attribution_agent            null | str
    attribution_plugin           null | str
    attribution_skill            null | str
    cache_read_tokens            int
    cache_write_tokens           int
    effort                       null | str
    estimated_reasoning_tokens   int
    estimated_visible_tokens     int
    has_thinking                 bool
    input_tokens                 int
    message_id                   str
    model                        str
    output_tokens                int
    reasoning_ratio              float
    reasoning_source             str
    tool_call_count              int
spawn
    agent_type                   str
    description                  str
    duration_ms                  int | null
    prompt_char_count            int
    requested_model              null | str
    resolved_model               null | str
    result                       dict
    run_in_background            bool | null
    status                       null | str
tool
    blocked_on_user_ms           null
    cmd                          list
    duration_ms                  int | null
    exit_status                  int
    file_change                  bool
    memory_access                bool
    files                        list
    input                        dict
    is_error                     bool
    result                       dict
    tool_denial_kind             null | str
    tool_name                    str
turn
    attribution                  dict
    duration_ms                  int
    permission_mode              null | str
    plan_mode                    bool | null
    prompt_id                    str
    status                       str
    tokens                       dict
    turn_num                     int
    is_after_compact             bool
    triggered_compaction         bool
compaction
    trigger                      null | str
    tokens_before                null | int
    tokens_after                 null | int
    tokens_reclaimed             null | int
    duration_ms                  null | int
    usage_known                  bool
user_prompt
    is_injected                  bool
    model                        null
    resources                    list
    text                         str
```
<!-- /gen:graph:schema -->
