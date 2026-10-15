"""Causal graph: per-session span tree built from a NormalizedSession.

The versioned query interface is introduced separately from the graph construction and
persistence surface. See docs/design/causal-graph-design.md and the module README.

Public surface, by layer:
  models   Node, NodeType, Graph, Family, SCHEMA_VERSION
  config   ReasoningConfig, default_config (reasoning-token estimate constants, §4.1a)
  build    build_graph (one session), build_family (an envelope), scope helpers
  lifecycle GraphContext plus process-local reports and metrics
"""
from analysis.causal_graph.models import Family, Graph, Node, NodeType, SCHEMA_VERSION
from analysis.causal_graph.config import ReasoningConfig, default_config
from analysis.causal_graph.builder import GraphBuildError, build_graph
from analysis.causal_graph.context import GraphContext, GraphContextError, get_graph_context
from analysis.causal_graph.observability import get_load_report, get_metric
from analysis.causal_graph.query import query, select
from analysis.causal_graph.family import build_family, graphs_in_scope, nodes_in_scope
from analysis.causal_graph.named_api import NamedApiError, budget, gitops, goods, list_tools, memory_usage, subagents, tool_usage, turns

__all__ = [
    "Node", "NodeType", "Graph", "Family", "SCHEMA_VERSION",
    "ReasoningConfig", "default_config",
    "GraphBuildError", "build_graph", "build_family", "graphs_in_scope", "nodes_in_scope",
    "GraphContext", "GraphContextError", "get_graph_context",
    "get_load_report", "get_metric",
    "query", "select",
    "NamedApiError", "budget", "goods", "memory_usage", "list_tools", "tool_usage", "turns", "subagents", "gitops",
]
