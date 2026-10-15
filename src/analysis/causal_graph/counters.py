"""Anomaly counters for the causal-graph builder (design causal-graph-design.md §3.1, plan
`docs/plan/2026-08-23-turn-identity-join-scope-metrics.md` Step 3).

Every map the builder fills was a plain `dict[key] = value`: `Graph.add`, `results[tool_use_id]`,
`file_changes[...]`, and turn-node identity itself (a bare `promptId` as the id) -- all
last-wins and silent. That silence is what let all three defects this plan fixes go
unnoticed for as long as they did: a counting insert on `Graph.add` alone found the first of
them (a duplicated turn id silently overwriting a whole subtree) across 152 sessions in one run,
after an hour of hand auditing had already turned up nothing.

Each function below is one collision site's report: a `causal_graph.*` series in
`common.metrics` (never `common.msging`'s own names -- the two callers carry different label
vocabularies, see `common/metrics.py`'s module docstring) plus a structlog line at the
condition's severity. The functions only observe and report -- the "keep the first value, do
not replace it" decision itself lives at the call site (`models.Graph.add`,
`builder._segment_turns`, `builder.build_graph`), because only the call site knows what to do
instead with the second one.

Severities:
    ERROR    `node_collision` (`Graph.add`), `child_collision` (a child id already claimed by
             another parent), `prompt_id_noncontiguous` (a promptId reappearing after another
             intervened -- 0 observed in the reference corpus), `event_outside_turn` (an event
             landing outside every turn after the first promptId was seen -- a static invariant
             of `_segment_turns` under Step 1's boundary rule, so this firing means the
             invariant broke, not that this is a known, tolerated gap).
    WARNING  `results_collision`, `file_changes_collision` -- a session-scoped map seeing a
             second value for a key it already holds. Observed cause today: byte-identical
             replayed `tool_result` records (8 of 7,927, one session) -- non-fatal, because the
             first value already answers any query that would read the map.
    counter  `empty_turn` -- a turn with zero assistant records. Legitimate and expected (26 of
             594 turns in the reference corpus: a prompt with no work after it, `status ==
             "empty"`) -- a gauge to watch for a rising rate, not an alarm.
"""
from __future__ import annotations

import structlog

from common import metrics

__all__ = [
    "node_collision",
    "child_collision",
    "results_collision",
    "file_changes_collision",
    "event_outside_turn",
    "prompt_id_noncontiguous",
    "empty_turn",
]

_log = structlog.get_logger(module="analysis.causal_graph.counters")


def node_collision(*, session_id: str, vendor: str, node_id: str, node_type: str) -> None:
    """`Graph.add` refused a second node at an id it already indexes -- first-wins, ERROR.

    The previous "last write wins" behaviour is what let a duplicated turn id silently replace
    an earlier turn's subtree, orphaning everything beneath it -- 1,974,633 tokens on one
    measured session (plan Step 1/3).
    """
    metrics.counter("causal_graph.node_collision", vendor=vendor, node_type=node_type)
    _log.error("causal_graph_node_collision", session_id=session_id, node_id=node_id,
               node_type=node_type)


def child_collision(*, session_id: str, vendor: str, child_id: str, owner_id: str,
                     claimant_id: str) -> None:
    """A child id was already claimed by another parent -- the append is refused, ERROR.

    `owner_id` is the parent that already claims `child_id`; `claimant_id` is the parent whose
    claim was refused (the two are equal for a duplicate append by the SAME parent -- also
    refused, since it would list the child twice under one parent). Without this guard a
    replayed record's node ends up a child of two parents and every walk of `children` (token
    rollup, budget) counts it twice -- the +575,309 that `Graph.add`'s own fix alone did not
    clear (plan Step 3).
    """
    metrics.counter("causal_graph.child_collision", vendor=vendor)
    _log.error("causal_graph_child_collision", session_id=session_id, child_id=child_id,
               owner_id=owner_id, claimant_id=claimant_id)


def results_collision(*, session_id: str, vendor: str, tool_use_id: str) -> None:
    """A second `tool_result` was seen for a `tool_use_id` already in the session-scoped
    results map -- WARNING. Observed cause today: byte-identical replayed records (8 of 7,927,
    one session). First-wins keeps the earlier result; the replay changes nothing a query
    would return differently.
    """
    metrics.counter("causal_graph.results_collision", vendor=vendor)
    _log.warning("causal_graph_results_collision", session_id=session_id,
                 tool_use_id=tool_use_id)


def file_changes_collision(*, session_id: str, vendor: str, derived_from_event_id: str) -> None:
    """A duplicate `file_change` record (same `derived_from_event_id`, same source record id)
    was seen twice -- WARNING. Distinct from the ordinary, legitimate case of several different
    `file_change` events deriving from one tool call (several files edited by one call), which
    is not a collision and is not counted here.
    """
    metrics.counter("causal_graph.file_changes_collision", vendor=vendor)
    _log.warning("causal_graph_file_changes_collision", session_id=session_id,
                 derived_from_event_id=derived_from_event_id)


def event_outside_turn(*, session_id: str, vendor: str, event_type: str) -> None:
    """An event landed in the startup bucket AFTER the first promptId was already seen --
    ERROR.

    The startup bucket (events before any promptId) is meant to hold only `session_start` and
    pre-first-prompt attachments. Under Step 1's boundary rule the open turn is never closed
    except by the next promptId, so this branch is unreachable by construction once a turn has
    opened -- a static invariant, kept as a guard against a future change reintroducing a path
    that resets it, the same spirit as the `_REQUIRED_PAYLOAD_FIELDS` contract guard elsewhere
    in this package.
    """
    metrics.counter("causal_graph.event_outside_turn", vendor=vendor, event_type=event_type)
    _log.error("causal_graph_event_outside_turn", session_id=session_id, event_type=event_type)


def prompt_id_noncontiguous(*, session_id: str, vendor: str, prompt_id: str) -> None:
    """A promptId reappeared after a different one intervened -- ERROR.

    0 observed in the reference corpus (findings-ledger.md §2's "no promptId ever reappeared
    after another intervened" claim, made precise and enforced here rather than merely
    asserted). A fire means the vendor's behaviour changed, not that this is a known gap.
    """
    metrics.counter("causal_graph.prompt_id_noncontiguous", vendor=vendor)
    _log.error("causal_graph_prompt_id_noncontiguous", session_id=session_id,
               prompt_id=prompt_id)


def empty_turn(*, session_id: str, vendor: str, turn_id: str) -> None:
    """A turn with zero assistant records -- a counter, not an alarm.

    Legitimate and expected: a prompt with no work after it (`turn.attrs.status == "empty"`),
    26 of 594 turns in the reference corpus. Worth a gauge to watch for a rising rate, not
    something to log at warning/error severity.
    """
    metrics.counter("causal_graph.empty_turn", vendor=vendor)
