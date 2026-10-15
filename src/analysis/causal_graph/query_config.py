"""Mandatory, versioned configuration for the causal-graph query contract."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

__all__ = [
    "BudgetCacheConfig",
    "CommandExtractorConfig",
    "FilterLimits",
    "MemoryClassifierConfig",
    "QueryConfig",
    "QueryConfigError",
    "load_query_config",
]

_PARSER_ADAPTERS = frozenset({"bash_tree_sitter", "powershell_ast"})
_REPO_QUERY_CONFIG = Path(__file__).resolve().parents[3] / "config/causal_graph/query.yaml"


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate semantic configuration keys."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.YAMLError(f"duplicate mapping key {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class QueryConfigError(ValueError):
    """A structured configuration error safe for CLI and service adapters."""

    def __init__(self, code: str, message: str, *, details: dict | None = None) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "location": None,
                "details": self.details}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FilterLimits(_StrictModel):
    max_expression_chars: int = Field(gt=0)
    max_nesting: int = Field(gt=0)
    max_regex_chars: int = Field(gt=0)


class CommandExtractorConfig(_StrictModel):
    command_field: str = Field(min_length=1)
    parser: str
    basename_style: str
    command_case: str

    @field_validator("command_field")
    @classmethod
    def _validate_command_field(cls, value: str) -> str:
        if any(not part.isidentifier() for part in value.split(".")):
            raise ValueError("must be a dotted attribute path")
        return value
    @field_validator("parser")
    @classmethod
    def _validate_parser(cls, value: str) -> str:
        if value not in _PARSER_ADAPTERS:
            raise ValueError(f"unknown parser {value!r}; expected one of {sorted(_PARSER_ADAPTERS)}")
        return value

    @field_validator("basename_style")
    @classmethod
    def _validate_basename_style(cls, value: str) -> str:
        if value not in {"posix", "windows"}:
            raise ValueError("must be 'posix' or 'windows'")
        return value

    @field_validator("command_case")
    @classmethod
    def _validate_command_case(cls, value: str) -> str:
        if value not in {"preserve", "casefold"}:
            raise ValueError("must be 'preserve' or 'casefold'")
        return value


class MemoryClassifierConfig(_StrictModel):
    path_glob: str = Field(min_length=1)
    kind: Literal["index", "entry", "unknown"]


class BudgetTurnRollupConfig(_StrictModel):
    identity: list[str] = Field(min_length=2, max_length=2)
    usage_source: str = Field(min_length=1)
    reusable_for: list[str] = Field(min_length=1)
    persisted: bool
    hit_bypasses_recomputation: bool


class BudgetCacheConfig(_StrictModel):
    policy: str
    owner: str
    key_dimensions: list[str] = Field(min_length=1)
    turn_rollup: BudgetTurnRollupConfig

    @field_validator("policy")
    @classmethod
    def _validate_policy(cls, value: str) -> str:
        if value != "lazy_memory_only":
            raise ValueError("must be 'lazy_memory_only'")
        return value


class QueryConfig(_StrictModel):
    version: str
    revision: str = Field(min_length=1)
    filter: FilterLimits
    command_extractors: dict[str, CommandExtractorConfig] = Field(min_length=1)
    memory_classifiers: dict[str, list[MemoryClassifierConfig]] = Field(min_length=1)
    budget_cache: BudgetCacheConfig

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        if value != "1":
            raise ValueError("must be supported configuration version '1'")
        return value

    @field_validator("command_extractors")
    @classmethod
    def _validate_tool_names(cls, value: dict[str, CommandExtractorConfig]) -> dict[str, CommandExtractorConfig]:
        if any(not name.strip() for name in value):
            raise ValueError("tool names must be non-empty")
        return value

    @field_validator("memory_classifiers")
    @classmethod
    def _validate_memory_classifiers(
        cls, value: dict[str, list[MemoryClassifierConfig]],
    ) -> dict[str, list[MemoryClassifierConfig]]:
        if any(not vendor.strip() for vendor in value):
            raise ValueError("memory classifier vendor names must be non-empty")
        if any(not rules for rules in value.values()):
            raise ValueError("each memory classifier vendor must have at least one rule")
        return value


def load_query_config(path: str | Path | None = None) -> QueryConfig:
    """Load the mandatory repository configuration without semantic fallback values."""
    config_path = Path(path) if path is not None else _REPO_QUERY_CONFIG
    try:
        raw = yaml.load(config_path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except FileNotFoundError as exc:
        raise QueryConfigError("query_config_missing", f"query configuration not found: {config_path}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise QueryConfigError("query_config_invalid", f"cannot read query configuration: {exc}") from exc
    if not isinstance(raw, dict):
        raise QueryConfigError("query_config_invalid", "query configuration must be a mapping")
    try:
        return QueryConfig.model_validate(raw)
    except ValidationError as exc:
        raise QueryConfigError(
            "query_config_invalid",
            "query configuration failed validation",
            details={"errors": exc.errors(include_url=False)},
        ) from exc
