"""Render causal-graph README tables from the authoritative contract bundle."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = _ROOT / "src/common/contracts/causal_graph"
SCHEMA_FILE = CONTRACT_ROOT / "schemas/graph.schema.json"
README = _ROOT / "src/analysis/causal_graph/README.md"
API_SPEC = CONTRACT_ROOT / "interfaces/named_apis.yaml"


def build_schema() -> dict:
    """Read the authored graph contract; fixtures never define this interface."""
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    required = {"$schema", "properties", "$defs", "attrs_by_node_type"}
    if missing := required - schema.keys():
        raise SystemExit(f"Graph contract is missing required sections: {sorted(missing)}")
    return schema


def render_schema_block() -> str:
    schema = build_schema()
    compact = {key: schema[key] for key in ("schema_version", "node_types", "node", "envelope")}
    lines = ["```json", json.dumps(compact, indent=2), "```", "", "`attrs` by node type:", "", "```"]
    for node_type, block in schema["attrs_by_node_type"].items():
        lines.append(node_type)
        for field, spec in block["attrs"].items():
            lines.append(f"    {field:28} {' | '.join(spec['types'])}")
    lines.extend(["```"])
    return "\n".join(lines)


def render_named_api_block() -> str:
    """Render the public named-API index from its authored YAML contract."""
    spec = yaml.safe_load(API_SPEC.read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or spec.get("version") != "1" or not isinstance(spec.get("apis"), list):
        raise SystemExit(f"Invalid named API specification: {API_SPEC}")
    lines = ["| API | Stable signature | Result kind |", "|---|---|---|"]
    for api in spec["apis"]:
        if not isinstance(api, dict) or not all(isinstance(api.get(key), str) for key in ("name", "signature")):
            raise SystemExit(f"Invalid named API entry in {API_SPEC}")
        result = api.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("kind"), str):
            raise SystemExit(f"Named API entry {api['name']!r} has no result kind")
        lines.append(f"| `{api['name']}` | `{api['signature']}` | `{result['kind']}` |")
    lines.extend([
        "",
        "The complete request fields, response envelopes, and named error codes are authored in "
        "[`named_apis.yaml`](../../common/contracts/causal_graph/interfaces/named_apis.yaml). "
        "QuerySpec construction and caching are internal "
        "implementation details of these wrappers.",
    ])
    return "\n".join(lines)


def _replace_managed_block(text: str, kind: str, body: str) -> str:
    start = f"<!-- gen:graph:{kind} -->"
    end = f"<!-- /gen:graph:{kind} -->"
    if start not in text or end not in text:
        raise SystemExit(f"Missing managed {kind!r} block in {README}")
    before, _, rest = text.partition(start)
    _, _, after = rest.partition(end)
    return f"{before}{start}\n{body}\n{end}{after}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    build_schema()
    readme_text = README.read_text(encoding="utf-8")
    new_readme = _replace_managed_block(readme_text, "schema", render_schema_block())
    new_readme = _replace_managed_block(new_readme, "named-api", render_named_api_block())
    stale = readme_text != new_readme
    if args.check:
        if stale:
            print("causal-graph schema reference is stale; run utils/gen_graph_interface.py")
            return 1
        return 0
    README.write_text(new_readme, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
