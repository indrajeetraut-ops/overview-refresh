# Causal Graph CLI

Humans invoke the CLI through `./utils/graph.sh`. The wrapper sets `PYTHONPATH` and dispatches to
`analysis.causal_graph.cli_graph`.

## Build and load

```bash
./utils/graph.sh build normalized-envelope.json -o graphs/
./utils/graph.sh build-all /path/to/normalized-tree -o graphs/
./utils/graph.sh load graphs/<master-session-id>.json
```

The persisted CLI layout is:

```text
graphs/<master-session-id>.json
graphs/<master-session-id>/subagents/<child-session-id>.json
```

`build-all` mirrors the source tree and writes `run_results.json`. Pass a master JSON file when
a directory contains several masters.

## Inline selection and QuerySpec

```bash
./utils/graph.sh query examples/causal_graph/graphs/query_api_sample.json \
  --select Tool \
  --where "Tool.name == 'Bash' AND Tool.outcome == 'failure'" \
  --scope self --return summary --order-by Tool.ts:asc --limit 20

./utils/graph.sh query examples/causal_graph/graphs/query_api_sample.json \
  --query examples/causal_graph/query_specs/tool_inventory.json
```

Repeat `--group-by`, `--aggregate`, and `--order-by` as needed. Aggregate syntax is
`name=FUNCTION[:field]`. The expression language and QuerySpec are explained in the Python
[`README.md`](README.md).

## Named APIs

```bash
GRAPH=examples/causal_graph/graphs/query_api_sample.json

./utils/graph.sh api budget "$GRAPH" --scope self --by model
./utils/graph.sh api budget "$GRAPH" --scope self --by turn \
  --turn-session-id query-api-sample --turn-num 1
./utils/graph.sh api goods "$GRAPH" --scope self --artifact-class code --extension .py
./utils/graph.sh api memory_usage "$GRAPH" --scope tree
./utils/graph.sh api list_tools "$GRAPH" --scope self
./utils/graph.sh api tool_usage "$GRAPH" --scope self \
  --tool-class harness --name Bash --outcome failure \
  --invocation-matches 'git (commit|push)' --limit 20
./utils/graph.sh api turns "$GRAPH" \
  --session-id query-api-sample --turn-num 1 --node-type tool
./utils/graph.sh api subagents "$GRAPH" --scope self
./utils/graph.sh api gitops "$GRAPH" --scope self --operation commit --limit 20
```

Scope is `self`, `tree`, `subagents`, or `session:<session-id>`. `subagents` itself has fixed
`self` scope. Tool class is `harness` (for `Bash`, `Read`, and MCP tools) or `command`
(executables extracted from shell invocations). Outcome is `all`, `success`, or `failure`;
`--invocation-matches` is an RE2 expression over the complete invocation.

Success writes one JSON envelope to stdout. Invalid named filters write a stable JSON error to
stderr and exit nonzero. The authoritative contract is
[`named_apis.yaml`](../../common/contracts/causal_graph/interfaces/named_apis.yaml).

Exact corpus commands, exit status, validation status, and representative outputs are in
[`2026-08-26-causal-graph-consumer-interface-validation.md`](../../../docs/reports/2026-08-26-causal-graph-consumer-interface-validation.md).
