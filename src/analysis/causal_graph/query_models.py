"""Strict public QuerySpec and QueryResult contracts for causal-graph queries."""
from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "AggregateFunction",
    "AggregateSpec",
    "NodeIdentity",
    "NodeResult",
    "OrderBy",
    "QueryError",
    "QueryResult",
    "QuerySpec",
    "QueryWarning",
    "ReturnSpec",
    "ScopeSpec",
    "SelectSpec",
    "WalkSpec",
]

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _normalized(value: str, allowed: set[str], field_name: str) -> str:
    normalized = value.casefold()
    if normalized not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{field_name} must be one of {choices}")
    return normalized


class ScopeSpec(_StrictModel):
    kind: str
    session_id: str | None = None

    @model_validator(mode="after")
    def _validate_scope(self) -> "ScopeSpec":
        self.kind = _normalized(self.kind, {"self", "tree", "subagents", "session"}, "scope.kind")
        has_session_id = "session_id" in self.model_fields_set
        if self.kind == "session":
            if not self.session_id:
                raise ValueError("scope.session_id is required when scope.kind is 'session'")
        elif has_session_id:
            raise ValueError("scope.session_id is only valid when scope.kind is 'session'")
        return self


class SelectSpec(_StrictModel):
    type: str
    where: str | None = None

    @model_validator(mode="after")
    def _validate_type(self) -> "SelectSpec":
        names = {"node", "turn", "userprompt", "assistant", "tool", "spawn", "compaction"}
        value = _normalized(self.type.replace("_", "").replace(" ", ""), names, "select.type")
        self.type = {
            "node": "Node", "turn": "Turn", "userprompt": "UserPrompt",
            "assistant": "Assistant", "tool": "Tool", "spawn": "Spawn",
            "compaction": "Compaction",
        }[value]
        return self


class WalkSpec(_StrictModel):
    relation: str
    type: str
    where: str | None = None

    @model_validator(mode="after")
    def _validate_walk(self) -> "WalkSpec":
        self.relation = _normalized(self.relation, {"within", "contains", "spawned"}, "walk.relation")
        names = {"turn", "userprompt", "assistant", "tool", "spawn"}
        value = _normalized(self.type.replace("_", "").replace(" ", ""), names, "walk.type")
        self.type = {
            "turn": "Turn", "userprompt": "UserPrompt", "assistant": "Assistant",
            "tool": "Tool", "spawn": "Spawn",
        }[value]
        return self


class AggregateFunction(str, Enum):
    COUNT = "COUNT"
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"
    MEDIAN = "MEDIAN"


class AggregateSpec(_StrictModel):
    name: str
    function: AggregateFunction
    field: str | None = None

    @field_validator("function", mode="before")
    @classmethod
    def _normalize_function(cls, value: Any) -> AggregateFunction:
        if isinstance(value, AggregateFunction):
            return value
        if not isinstance(value, str):
            raise ValueError("aggregate function must be a string")
        try:
            return AggregateFunction(value.upper())
        except ValueError as exc:
            choices = ", ".join(function.value for function in AggregateFunction)
            raise ValueError(f"aggregate function must be one of {choices}") from exc

    @model_validator(mode="after")
    def _validate_aggregate(self) -> "AggregateSpec":
        if not _IDENTIFIER.match(self.name):
            raise ValueError("aggregate name must be an identifier")
        if self.function is AggregateFunction.COUNT and "field" in self.model_fields_set:
            raise ValueError("COUNT must not declare a field")
        if self.function is not AggregateFunction.COUNT and not self.field:
            raise ValueError(f"{self.function.value} requires a field")
        return self


class OrderBy(_StrictModel):
    field: str = Field(min_length=1)
    direction: str

    @model_validator(mode="after")
    def _validate_direction(self) -> "OrderBy":
        self.direction = _normalized(self.direction, {"asc", "desc"}, "order_by.direction")
        return self


class ReturnSpec(_StrictModel):
    kind: str
    verbosity: str | None = None
    fields: list[str] | None = None
    include_members: bool | None = None

    @model_validator(mode="after")
    def _validate_return(self) -> "ReturnSpec":
        self.kind = _normalized(self.kind, {"nodes", "groups", "aggregate"}, "return.kind")
        has_verbosity = "verbosity" in self.model_fields_set
        if self.kind == "aggregate":
            if has_verbosity:
                raise ValueError("return.verbosity is invalid for aggregate results")
            if "fields" in self.model_fields_set:
                raise ValueError("return.fields is invalid for aggregate results")
        else:
            if not self.verbosity:
                raise ValueError("return.verbosity is required for nodes and groups")
            self.verbosity = _normalized(self.verbosity, {"ref", "summary", "full"}, "return.verbosity")
        if self.kind != "groups" and "include_members" in self.model_fields_set:
            raise ValueError("return.include_members is only valid for groups")
        if self.fields is not None and any(not item for item in self.fields):
            raise ValueError("return.fields cannot contain empty paths")
        return self


class QuerySpec(_StrictModel):
    version: str
    scope: ScopeSpec
    select: SelectSpec
    walk: list[WalkSpec] | None = None
    group_by: list[str] | None = Field(default=None, min_length=1, max_length=3)
    aggregates: list[AggregateSpec] | None = None
    having: str | None = None
    order_by: list[OrderBy] | None = None
    limit: int | None = Field(default=None, ge=1, le=10_000)
    return_: ReturnSpec = Field(alias="return")

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> "QuerySpec":
        if self.version != "1":
            raise ValueError("version must be supported query-contract version '1'")
        if self.group_by is not None:
            if len(set(self.group_by)) != len(self.group_by):
                raise ValueError("group_by fields must be unique")
            if any(not path for path in self.group_by):
                raise ValueError("group_by cannot contain empty paths")
        if self.aggregates is not None:
            aliases = [aggregate.name for aggregate in self.aggregates]
            if len(set(aliases)) != len(aliases):
                raise ValueError("aggregate names must be unique")
        has_groups = self.group_by is not None
        if self.return_.kind == "nodes":
            if has_groups or self.aggregates is not None:
                raise ValueError("nodes results cannot use group_by or aggregates")
        elif self.return_.kind == "groups":
            if not has_groups:
                raise ValueError("groups results require group_by")
        else:
            if has_groups or not self.aggregates:
                raise ValueError("aggregate results require aggregates and no group_by")
        if self.having is not None and not has_groups:
            raise ValueError("having is only valid with group_by")
        return self


class NodeIdentity(_StrictModel):
    session_id: str = Field(min_length=1)
    turn_num: int | None = Field(default=None, ge=1)
    node_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _validate_identity(self) -> "NodeIdentity":
        if (self.turn_num is None) == (self.node_id is None):
            raise ValueError("identity must contain exactly one of turn_num or node_id")
        return self


class NodeResult(_StrictModel):
    identity: NodeIdentity
    type: str
    ts: str | None
    fields: dict[str, Any] | None = None
    node: dict[str, Any] | None = None


class QueryWarning(_StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class QueryError(_StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    location: int | str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class GroupResult(_StrictModel):
    key: dict[str, Any]
    aggregates: dict[str, Any]
    members: list[NodeResult] | None = None


class QueryResult(_StrictModel):
    version: str
    status: str
    kind: str
    items: list[NodeResult] | None = None
    groups: list[GroupResult] | None = None
    aggregate: dict[str, Any] | None = None
    truncated: bool
    warnings: list[QueryWarning]

    @model_validator(mode="after")
    def _validate_result_shape(self) -> "QueryResult":
        if self.version != "1":
            raise ValueError("version must be supported query-contract version '1'")
        self.status = _normalized(self.status, {"complete", "partial"}, "status")
        self.kind = _normalized(self.kind, {"nodes", "groups", "aggregate"}, "kind")
        expected = {"nodes": "items", "groups": "groups", "aggregate": "aggregate"}[self.kind]
        present = {name for name in ("items", "groups", "aggregate") if name in self.model_fields_set}
        if present != {expected}:
            raise ValueError(f"{self.kind} results must expose only {expected}")
        return self
