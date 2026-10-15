"""Conformance gates for the authored causal-graph contract bundle."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = ROOT / "src/common/contracts/causal_graph"


def test_manifest_components_exist_and_versions_match() -> None:
    manifest = yaml.safe_load((CONTRACT_ROOT / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["bundle_version"] == "1"
    for component in manifest["components"].values():
        path = CONTRACT_ROOT / component["path"]
        assert path.is_file(), path
        if path.suffix == ".json":
            document = json.loads(path.read_text(encoding="utf-8"))
            version = document.get("schema_version") or document.get("properties", {}).get("version", {}).get("const")
        elif path.suffix == ".yaml":
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            version = document.get("version") if isinstance(document, dict) else component["version"]
        else:
            version = component["version"]
        assert version == component["version"], path

    for contract in manifest["external_contracts"].values():
        assert (CONTRACT_ROOT / contract["path"]).is_file()


def test_json_schema_documents_have_required_draft_shape() -> None:
    for name in ("graph", "query_spec", "query_result"):
        schema = json.loads((CONTRACT_ROOT / f"schemas/{name}.schema.json").read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["type"] == "object"
        assert schema["required"]
        assert schema["properties"]


def test_interfaces_resolve_their_local_contract_references() -> None:
    for path in (CONTRACT_ROOT / "interfaces").glob("*.yaml"):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))

        def visit(value: object) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"contract", "request", "result", "grammar", "semantics", "fields"} and isinstance(item, str) and item.startswith("."):
                        assert (path.parent / item).resolve().is_file(), (path, item)
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(document)


def test_filter_parser_reads_the_authoritative_grammar() -> None:
    source = (ROOT / "src/analysis/causal_graph/filter_parser.py").read_text(encoding="utf-8")
    assert "common/contracts/causal_graph/query_language/filter.lark" in source
    assert "with_name(\"filter.lark\")" not in source


def test_compaction_is_published_across_query_contracts() -> None:
    query_schema = json.loads(
        (CONTRACT_ROOT / "schemas/query_spec.schema.json").read_text(encoding="utf-8")
    )
    assert "Compaction" in query_schema["$defs"]["select"]["properties"]["type"]["enum"]

    fields = yaml.safe_load(
        (CONTRACT_ROOT / "query_language/fields.yaml").read_text(encoding="utf-8")
    )
    assert fields["summary_defaults"]["Compaction"] == [
        "Compaction.trigger", "Compaction.tokens_reclaimed", "Compaction.duration_ms",
    ]
