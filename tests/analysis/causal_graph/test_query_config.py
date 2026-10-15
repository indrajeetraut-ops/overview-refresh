from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from analysis.causal_graph.query_config import QueryConfigError, load_query_config


def _repository_config() -> Path:
    return Path(__file__).resolve().parents[3] / "config/causal_graph/query.yaml"


def _write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "query.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_repository_config_is_the_complete_semantic_source_of_truth():
    config = load_query_config()
    assert config.revision == "2026-08-27"
    assert config.filter.max_expression_chars == 4096
    assert config.command_extractors["Bash"].parser == "bash_tree_sitter"
    assert config.command_extractors["PowerShell"].command_case == "casefold"
    assert config.memory_classifiers["claude_code"][0].kind == "index"
    assert config.memory_classifiers["claude_code"][1].kind == "entry"


@pytest.mark.parametrize(
    "key", ["version", "revision", "filter", "command_extractors", "memory_classifiers", "budget_cache"],
)
def test_missing_top_level_key_fails(tmp_path: Path, key: str):
    data = yaml.safe_load(_repository_config().read_text(encoding="utf-8"))
    data.pop(key)
    with pytest.raises(QueryConfigError, match="failed validation"):
        load_query_config(_write_config(tmp_path, data))


@pytest.mark.parametrize("key", ["max_expression_chars", "max_nesting", "max_regex_chars"])
def test_missing_filter_key_has_no_python_default(tmp_path: Path, key: str):
    data = yaml.safe_load(_repository_config().read_text(encoding="utf-8"))
    data["filter"].pop(key)
    with pytest.raises(QueryConfigError, match="failed validation"):
        load_query_config(_write_config(tmp_path, data))


def test_invalid_mapping_or_parser_fails(tmp_path: Path):
    data = yaml.safe_load(_repository_config().read_text(encoding="utf-8"))
    data["command_extractors"]["Bash"]["parser"] = "split_on_whitespace"
    with pytest.raises(QueryConfigError, match="failed validation"):
        load_query_config(_write_config(tmp_path, data))


@pytest.mark.parametrize(
    "rules",
    [[], [{"path_glob": "", "kind": "entry"}], [{"path_glob": "**/memory/**", "kind": "blob"}]],
)
def test_invalid_memory_classifier_fails(tmp_path: Path, rules: list[dict]):
    data = yaml.safe_load(_repository_config().read_text(encoding="utf-8"))
    data["memory_classifiers"]["claude_code"] = rules
    with pytest.raises(QueryConfigError, match="failed validation"):
        load_query_config(_write_config(tmp_path, data))


def test_duplicate_mapping_fails(tmp_path: Path):
    path = tmp_path / "query.yaml"
    path.write_text(
        """version: \"1\"
revision: \"a\"
filter:
  max_expression_chars: 1
  max_nesting: 1
  max_regex_chars: 1
command_extractors:
  Bash: {command_field: input.command, parser: bash_tree_sitter, basename_style: posix, command_case: preserve}
  Bash: {command_field: input.command, parser: bash_tree_sitter, basename_style: posix, command_case: preserve}
""",
        encoding="utf-8",
    )
    with pytest.raises(QueryConfigError, match="cannot read"):
        load_query_config(path)


def test_missing_file_fails():
    with pytest.raises(QueryConfigError, match="not found"):
        load_query_config("does-not-exist.yaml")
