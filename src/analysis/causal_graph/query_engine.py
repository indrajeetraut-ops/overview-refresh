"""In-memory execution for node-returning QuerySpec requests."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from statistics import median

from .family import graphs_in_scope
from .field_registry import FieldRegistry, MISSING, default_summary_fields
from .filter_parser import evaluate, parse_filter
from .filter_ast import Boolean, Comparison, Field, Function, InComparison, Not, StringComparison
from .models import Family, Graph, Node, NodeType
from .query_config import QueryConfig
from .query_models import GroupResult, NodeIdentity, NodeResult, QueryResult, QuerySpec, QueryWarning


@dataclass(frozen=True)
class Candidate:
    graph: Graph
    node: Node
    order: int


def _scope(spec: QuerySpec) -> str:
    return spec.scope.session_id if spec.scope.kind == "session" else spec.scope.kind


def _matches_type(node: Node, requested: str) -> bool:
    return requested == "Node" or node.type.value.replace("_", "").casefold() == requested.casefold()


class QueryEngine:
    def __init__(self, family: Family, config: QueryConfig) -> None:
        self.family, self.config = family, config
        self.registry = FieldRegistry()
        self._warnings: list[QueryWarning] = []
        self._partial = False

    def execute(self, spec: QuerySpec) -> QueryResult:
        selected = self._select(spec)
        for stage in spec.walk or []:
            selected = self._walk(selected, stage.relation, stage.type, stage.where)
        selected.sort(key=lambda candidate: candidate.order)
        if spec.return_.kind == "nodes":
            selected = self._order_nodes(selected, spec)
            selected, truncated = self._limit(selected, spec.limit)
            return QueryResult(
                version="1", status="partial" if self._partial else "complete", kind="nodes",
                items=[self._shape(candidate, spec) for candidate in selected], truncated=truncated,
                warnings=self._warnings,
            )
        if spec.return_.kind == "aggregate":
            return QueryResult(version="1", status="partial" if self._partial else "complete", kind="aggregate",
                               aggregate=self._aggregates(selected, spec), truncated=False, warnings=self._warnings)
        return self._groups(selected, spec)

    def _order_nodes(self, selected: list[Candidate], spec: QuerySpec) -> list[Candidate]:
        rows = list(selected)
        for order in reversed(spec.order_by or []):
            present = [row for row in rows if (value := self.registry.value(row.node, order.field)) is not MISSING and value is not None]
            missing = [row for row in rows if (value := self.registry.value(row.node, order.field)) is MISSING or value is None]
            present.sort(key=lambda row: self.registry.value(row.node, order.field), reverse=order.direction == "desc")
            rows = present + missing
        return rows

    def execute_nodes(self, spec: QuerySpec) -> QueryResult:
        """Compatibility for the node-only package-4 entry point."""
        return self.execute(spec)

    def _limit(self, rows: list, limit: int | None):
        cap = min(limit or 10_000, 10_000)
        return rows[:cap], len(rows) > cap

    def _aggregates(self, members: list[Candidate], spec: QuerySpec) -> dict:
        result: dict = {}
        for aggregate in spec.aggregates or []:
            if aggregate.function.value == "COUNT": result[aggregate.name] = len(members); continue
            values = [self.registry.value(candidate.node, aggregate.field) for candidate in members]
            values = [value for value in values if value is not MISSING and value is not None]
            if any(not isinstance(value, (int, float)) for value in values):
                raise ValueError(f"{aggregate.function.value} requires numeric field {aggregate.field}")
            if aggregate.function.value == "SUM": result[aggregate.name] = sum(values)
            elif aggregate.function.value == "AVG": result[aggregate.name] = sum(values) / len(values) if values else None
            elif aggregate.function.value == "MIN": result[aggregate.name] = min(values) if values else None
            elif aggregate.function.value == "MAX": result[aggregate.name] = max(values) if values else None
            else: result[aggregate.name] = median(values) if values else None
        return result

    def _groups(self, selected: list[Candidate], spec: QuerySpec) -> QueryResult:
        buckets: dict[tuple, list[Candidate]] = {}
        for candidate in selected:
            keys = [self.registry.value(candidate.node, field) for field in spec.group_by or []]
            variants = [[]]
            for value in keys:
                values = list(dict.fromkeys(value)) if isinstance(value, list) else [None if value is MISSING else value]
                variants = [prefix + [item] for prefix in variants for item in values]
            for key in variants: buckets.setdefault(tuple(key), []).append(candidate)
        rows = []
        for key, members in buckets.items():
            aggregates = self._aggregates(members, spec)
            if spec.having:
                fake = Node(id="having", type=NodeType.TURN, attrs=aggregates)
                if not evaluate(parse_filter(spec.having, self.config), fake, self.registry, self.config): continue
            rows.append((key, members, aggregates))
        if spec.order_by:
            for order in reversed(spec.order_by):
                def order_value(row):
                    key, _, aggregates = row
                    if order.field in aggregates: value = aggregates[order.field]
                    elif order.field in (spec.group_by or []): value = key[(spec.group_by or []).index(order.field)]
                    else: value = None
                    return (value is None, value)
                rows.sort(key=order_value, reverse=order.direction == "desc")
        rows, truncated = self._limit(rows, spec.limit)
        groups = []
        for key, members, aggregates in rows:
            groups.append(GroupResult(key={field: value for field, value in zip(spec.group_by or [], key)}, aggregates=aggregates,
                                      members=[self._shape(member, spec) for member in members] if spec.return_.include_members else None))
        return QueryResult(
            version="1", status="partial" if self._partial else "complete", kind="groups",
            groups=groups, truncated=truncated, warnings=self._warnings,
        )

    def _select(self, spec: QuerySpec) -> list[Candidate]:
        expression = parse_filter(spec.select.where, self.config) if spec.select.where else None
        selected_type = None if spec.select.type == "Node" else NodeType(spec.select.type.replace("UserPrompt", "user_prompt").casefold())
        if expression is not None:
            self._validate_expression_fields(expression, selected_type)
        if spec.return_.kind == "nodes":
            for path in (spec.return_.fields or []):
                self.registry.validate(path, selected_type)
            for order in (spec.order_by or []):
                self.registry.validate(order.field, selected_type)
        candidates: list[Candidate] = []
        order = 0
        scoped_graphs = graphs_in_scope(self.family, _scope(spec))
        if spec.scope.kind in {"tree", "subagents"}:
            loaded = {graph.session_id for graph in self.family.all_graphs()}
            missing = sorted({
                child for graph in scoped_graphs for node in graph.nodes.values()
                if node.type is NodeType.SPAWN
                and isinstance((child := node.refs.get("child_session_id")), str)
                and child not in loaded
            })
            if missing:
                self._partial = True
                self._warnings.extend(QueryWarning(code="child_graph_unloaded", message=f"child session {child!r} is not loaded") for child in missing)
        for graph in scoped_graphs:
            for node in graph.nodes.values():
                if _matches_type(node, spec.select.type) and (expression is None or evaluate(expression, node, self.registry, self.config)):
                    candidates.append(Candidate(graph, node, order))
                order += 1
        return candidates

    def _validate_expression_fields(self, expression, selected_type: NodeType | None) -> None:
        if isinstance(expression, Field):
            self.registry.validate(".".join(expression.path), selected_type)
        elif isinstance(expression, Function):
            self._validate_expression_fields(expression.field, selected_type)
        elif isinstance(expression, (Comparison, InComparison, StringComparison)):
            self._validate_expression_fields(expression.left, selected_type)
        elif isinstance(expression, Not):
            self._validate_expression_fields(expression.expression, selected_type)
        elif isinstance(expression, Boolean):
            for item in expression.expressions:
                self._validate_expression_fields(item, selected_type)

    def _walk(self, source: list[Candidate], relation: str, target: str, where: str | None) -> list[Candidate]:
        if relation == "spawned" and any(candidate.node.type is not NodeType.SPAWN for candidate in source):
            raise ValueError("spawned walk requires Spawn candidates")
        expression = parse_filter(where, self.config) if where else None
        walked: list[Candidate] = []
        seen: set[tuple[str, str]] = set()
        order = 0
        for candidate in source:
            if relation == "within": nodes = self._ancestors(candidate)
            elif relation == "contains": nodes = self._descendants(candidate)
            else: nodes = self._spawned(candidate)
            for graph, node in nodes:
                key = (graph.session_id, node.id)
                if key in seen:
                    continue
                seen.add(key)
                if _matches_type(node, target) and (expression is None or evaluate(expression, node, self.registry, self.config)):
                    walked.append(Candidate(graph, node, order))
                order += 1
        return walked

    def _ancestors(self, candidate: Candidate):
        current = candidate.node.parent_id
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            node = candidate.graph.nodes.get(current)
            if node is None: break
            yield candidate.graph, node
            current = node.parent_id

    def _descendants(self, candidate: Candidate):
        queue = deque(candidate.node.children); seen: set[str] = set()
        while queue:
            node_id = queue.popleft()
            if node_id in seen: continue
            seen.add(node_id)
            node = candidate.graph.nodes.get(node_id)
            if node is None: continue
            yield candidate.graph, node
            queue.extend(node.children)

    def _spawned(self, candidate: Candidate):
        child_id = candidate.node.refs.get("child_session_id")
        child = self.family.resolve(child_id) if isinstance(child_id, str) else None
        if child is None:
            self._partial = True
            self._warnings.append(QueryWarning(code="child_graph_unloaded", message=f"child session {child_id!r} is not loaded"))
            return
        descendant_ids = {child.session_id}
        changed = True
        while changed:
            changed = False
            for graph in self.family.children.values():
                if graph.parent_session_id in descendant_ids and graph.session_id not in descendant_ids:
                    descendant_ids.add(graph.session_id); changed = True
        for graph in self.family.all_graphs():
            if graph.session_id in descendant_ids:
                for node in graph.nodes.values(): yield graph, node

    def _shape(self, candidate: Candidate, spec: QuerySpec) -> NodeResult:
        node = candidate.node
        identity = (NodeIdentity(session_id=candidate.graph.session_id, turn_num=node.attrs["turn_num"])
                    if node.type is NodeType.TURN else NodeIdentity(session_id=candidate.graph.session_id, node_id=node.id))
        fields = None
        if spec.return_.verbosity in {"summary", "full"}:
            paths = list(default_summary_fields(node.type))
            for path in spec.return_.fields or []:
                if path not in paths: paths.append(path)
            fields = {path: self.registry.value(node, path) for path in paths}
            fields = {path: (None if value is MISSING else value) for path, value in fields.items()}
        return NodeResult(identity=identity, type=node.type.value, ts=node.ts, fields=fields,
                          node=node.model_dump(mode="json") if spec.return_.verbosity == "full" else None)
