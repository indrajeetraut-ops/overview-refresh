"""Build one normalized ``sessions[]`` member into the causal-graph contract.

The session is the normalizer's output: ``{vendor, session_id, source, parent_session_id,
events[]}`` where each event is ``{event_type, record_id, parent_id, event_ts, payload}``
and ``payload`` follows ``src/common/events/normalized_schema.json`` (schema 2.0.0). This
step is JSONL-only and complete: no OTEL, no family assembly. Cross-session refs are left
unresolved for Step 3 (a spawn carries ``child_session_id`` in ``refs`` and nothing walks it
here).

``_REQUIRED_PAYLOAD_FIELDS`` is the contract guard: every payload key a transform below reads
via ``.get(...)``, which returns ``None`` silently on a missing key -- no exception, no
failing test, just null attrs and short budgets. ``tests/analysis/causal_graph/
test_normalized_contract.py`` asserts each one is still published in the schema, so an
upstream rename or removal fails loud here instead of surfacing as quiet data loss.

Five transforms, all structural (no heuristics on text):

* **Collapse assistant** -- the 2-15 records that share one ``payload.message_id`` become one
  ``assistant`` node. Usage is replicated across those records, so tokens are taken from the
  single record whose ``payload.first_block_of_message`` is true -- never summed (design §3.1;
  summing double-counts ~2x, plan Step 1). Reasoning is the one exception to "never summed":
  ``estimated_reasoning_tokens`` sits alongside the fb=true usage fields, but the two
  ingredients that produce it when nothing was reported (``estimated_visible_tokens``,
  ``tool_call_count``) are genuinely per-record and are summed across the whole group --
  see :mod:`analysis.causal_graph.reasoning`.
* **Merge tool** -- a ``tool_call`` and its ``tool_result`` (paired by
  ``tool_result.payload.tool_use_id == tool_call.record_id``) become one ``tool`` node carrying
  name/input/status/duration/rejection, plus a facet-grouped ``attrs.result`` extracted from the
  result's ``raw.toolUseResult`` by :func:`analysis.causal_graph.results.extract_result`. One
  node = one tool operation with its outcome.
* **Attach files** -- the goods a tool produced, from the tool_call's ``files[]`` and any
  ``file_change`` events deriving from it, land on the tool node's ``attrs.files`` with a
  ``attrs.file_change`` marker (design §6.5, §6.6).
* **Spawn** -- a ``sub_agent_spawn`` becomes a ``spawn`` node; its ``child_session_id`` lives in
  ``node.refs`` (late-bound, resolved on load in Step 3), not in ``attrs`` (design §4.1/§4.2).
  Its ``tool_result`` joins the same results map a ``tool_call`` does and merges the same
  ``attrs.result`` block.
* **Segment turns** -- a ``promptId`` run (the turn key, carried in ``payload.raw.promptId`` of
  ``user_message`` / ``tool_result`` records). The boundary is a ``promptId`` different from the
  one currently open, and nothing else -- a ``turn_duration`` record (an ``unknown`` event with
  ``payload.raw.subtype == "turn_duration"`` and ``durationMs``) does **not** close a turn; it is
  an event *inside* the open run and keeps two other jobs: it supplies the turn's
  ``duration_ms``, and it is the completed/interrupted signal (design §3.1, plan Step 1). A turn
  node's local id is ``"turn:{turn_num}"`` and ``attrs.turn_num`` carries the same 1-based
  ordinal. The canonical identity is ``(session_id, turn_num)``; ``promptId`` remains only in
  ``attrs.prompt_id`` as vendor provenance. A local ordinal is safe inside one session, while
  the session id prevents sibling agents' first turns from colliding in a family-wide query.

Thinking blocks are dropped; only ``has_thinking`` survives on the assistant node.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
from fnmatch import fnmatchcase

import structlog

from . import counters
from .config import ReasoningConfig, default_config
from .command_extractors import CommandExtractorError, extract_commands
from .models import Graph, Node, NodeType
from .query_config import QueryConfig, load_query_config
from .reasoning import estimate_reasoning
from .results import extract_result

__all__ = ["GraphBuildError", "build_graph"]

_log = structlog.get_logger(module="analysis.causal_graph.builder")

# event_type -> the payload keys a transform below reads via .get(...). The contract guard
# (test_normalized_contract.py) asserts each one is still published in
# src/common/events/normalized_schema.json -- see the module docstring.
_REQUIRED_PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "assistant_message": frozenset({
        "message_id", "first_block_of_message", "model", "has_thinking",
        "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
        "estimated_visible_tokens", "tool_call_count", "reasoning_tokens", "content_kind",
        "attribution_skill", "attribution_plugin", "attribution_agent", "effort",
    }),
    "tool_call": frozenset({
        "tool_name", "input", "exit_status", "duration_ms", "files", "message_id",
        "mcp_server", "mcp_tool",
    }),
    "tool_result": frozenset({
        "tool_use_id", "is_error", "tool_denial_kind", "raw",
    }),
    "sub_agent_spawn": frozenset({
        "agent_type", "status", "resolved_model", "child_session_id", "message_id",
    }),
    "user_message": frozenset({
        "text", "is_injected", "raw", "permission_mode", "plan_mode",
    }),
    "file_change": frozenset({
        "derived_from_event_id", "path", "extension", "artifact_class",
        "operation", "hunks",
    }),
    "session_start": frozenset({"ai_title"}),
    "unknown": frozenset({"raw"}),
    "compaction": frozenset({
        "trigger", "tokens_before", "tokens_after", "tokens_reclaimed", "duration_ms", "raw",
    }),
}


# --- small event accessors -------------------------------------------------

def _payload(event: dict) -> dict:
    return event.get("payload") or {}


def _raw(event: dict) -> dict:
    raw = _payload(event).get("raw")
    return raw if isinstance(raw, dict) else {}


def _prompt_id(event: dict) -> str | None:
    """The turn key for user_message / tool_result records; None for everything else.

    promptId is refreshed onto every user-side record through a turn, so it appears on both
    the initiating user_message and the tool_result records that follow within the same turn.
    """
    if event.get("event_type") in ("user_message", "tool_result"):
        pid = _raw(event).get("promptId")
        return pid if isinstance(pid, str) else None
    return None


def _is_turn_duration(event: dict) -> bool:
    return event.get("event_type") == "unknown" and _raw(event).get("subtype") == "turn_duration"


def _ts_to_ms(ts: str | None) -> float | None:
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000.0
    except ValueError:
        return None


# --- turn segmentation -----------------------------------------------------

class _Turn:
    """A promptId run being accumulated: its events, in order, plus its close marker(s)."""

    __slots__ = ("prompt_id", "run_ordinal", "start_ts", "events", "turn_duration_ms")

    def __init__(self, prompt_id: str, run_ordinal: int, start_ts: str | None) -> None:
        self.prompt_id = prompt_id
        self.run_ordinal = run_ordinal
        self.start_ts = start_ts
        self.events: list[dict] = []
        self.turn_duration_ms: int | None = None


def _segment_turns(
    events: list[dict], *, session_id: str, vendor: str,
) -> tuple[list[_Turn], list[dict]]:
    """Split an ordered event list into promptId runs (design §3.1, plan Step 1).

    **A turn boundary is a promptId different from the one currently open, and nothing else.**
    A ``turn_duration`` record does NOT close a run -- it is consumed *inside* the open turn and
    keeps two other jobs: it supplies ``duration_ms`` (see ``_turn_duration_ms``), and its mere
    presence is the completed/interrupted signal (``_turn_status``). A run can carry zero, one,
    or several ``turn_duration`` markers (measured: 182 runs / 409 / 3 in the reference corpus);
    with several, the LAST one wins -- each sighting simply overwrites ``turn_duration_ms``,
    since the assignment below runs unconditionally on every marker and nothing ends the run in
    between. Last was chosen over summing because the markers observed are re-emissions of the
    same closing event, not additive durations of distinct sub-runs -- summing them would
    inflate a turn's reported duration for no compensating benefit; "last" is also the simpler
    invariant (one overwrite, order-independent code) and is documented once, here, rather than
    reasoned about at every call site.

    The OLD rule -- close on `turn_duration` too -- is the defect this plan fixes: bash mode (a
    `!command`) emits its own `turn_duration` without rotating `promptId`, so the old rule
    closed the turn early and the same `promptId` then reopened a second turn whose id already
    existed, silently overwriting the first turn's subtree (596 turns measured instead of 594).

    ``run_ordinal`` increments at every boundary, from 1 -- the session's turn sequence, and
    the persisted ``turn_num`` (`"turn:{turn_num}"`, assigned by the caller once the turn is
    otherwise complete; see ``build_graph``).

    Events seen before the first promptId (session_start, startup attachments) are returned
    separately as ``startup`` -- they belong to no turn. A promptId reappearing after a
    different one intervened (non-contiguous) is reported via
    ``counters.prompt_id_noncontiguous`` rather than silently starting a fresh run under the
    old id (0 observed in the reference corpus; see the findings ledger).
    """
    turns: list[_Turn] = []
    startup: list[dict] = []
    current: _Turn | None = None
    seen_prompt_ids: set[str] = set()
    run_ordinal = 0
    turn_opened = False

    for event in events:
        pid = _prompt_id(event)
        if pid is not None and (current is None or current.prompt_id != pid):
            if pid in seen_prompt_ids:
                counters.prompt_id_noncontiguous(
                    session_id=session_id, vendor=vendor, prompt_id=pid,
                )
            seen_prompt_ids.add(pid)
            run_ordinal += 1
            current = _Turn(pid, run_ordinal, event.get("event_ts"))
            turns.append(current)
            turn_opened = True

        if current is not None:
            current.events.append(event)
        elif turn_opened:
            # Structurally unreachable once a turn has opened -- `current` is never reset to
            # None except by a new promptId (that branch above), so this is a static invariant
            # guard against a future change reintroducing a path that resets it, not a
            # condition observed in the corpus. See counters.event_outside_turn.
            counters.event_outside_turn(
                session_id=session_id, vendor=vendor, event_type=event.get("event_type"),
            )
            startup.append(event)
        else:
            startup.append(event)

        if _is_turn_duration(event) and current is not None:
            duration = _raw(event).get("durationMs")
            if isinstance(duration, int):
                current.turn_duration_ms = duration
            # turn_duration no longer closes the run (plan Step 1) -- `current` stays open for
            # the next event, which is either more of this same promptId's run, or the next
            # promptId, handled by the boundary check above.

    return turns, startup


# --- node construction -----------------------------------------------------

def _collapse_assistants(
    events: list[dict], *, vendor: str, config: ReasoningConfig
) -> list[Node]:
    """One assistant node per distinct ``payload.message_id``; tokens from the fb=true record.

    Groups preserve first-seen order. Within a group the fb=true record is authoritative for
    usage (input/output/cache tokens, model); ``has_thinking`` is true if any record in the
    group carried thinking. The reasoning-token estimate (or reported count) is added by
    :func:`analysis.causal_graph.reasoning.estimate_reasoning`, which needs the whole group --
    see the module docstring's "Collapse assistant" note. The negative-floor rate is counted
    inside ``estimate_reasoning`` itself (``causal_graph.reasoning.*`` in ``common.metrics``,
    plan Step 3) -- no stats object is threaded through here.
    """
    groups: dict[str, list[dict]] = {}
    for event in events:
        if event.get("event_type") != "assistant_message":
            continue
        mid = _payload(event).get("message_id")
        if not isinstance(mid, str):
            continue
        groups.setdefault(mid, []).append(event)

    nodes: list[Node] = []
    for mid, group in groups.items():
        fb = next((e for e in group if _payload(e).get("first_block_of_message")), group[0])
        p = _payload(fb)
        group_payloads = [_payload(e) for e in group]
        reasoning = estimate_reasoning(
            group_payloads, p, model=p.get("model"), vendor=vendor, config=config
        )
        nodes.append(
            Node(
                id=mid,
                type=NodeType.ASSISTANT,
                ts=fb.get("event_ts"),
                attrs={
                    "message_id": mid,
                    "model": p.get("model"),
                    "has_thinking": any(_payload(e).get("has_thinking") for e in group),
                    "input_tokens": p.get("input_tokens"),
                    "output_tokens": p.get("output_tokens"),
                    "cache_read_tokens": p.get("cache_read_tokens"),
                    "cache_write_tokens": p.get("cache_write_tokens"),
                    **reasoning,
                    "attribution_skill": p.get("attribution_skill"),
                    "attribution_plugin": p.get("attribution_plugin"),
                    "attribution_agent": p.get("attribution_agent"),
                    "effort": p.get("effort"),
                },
            )
        )
    return nodes


# The usage counters summed onto a turn. Same list `budget.TOKEN_FIELDS` rolls up, kept here
# rather than imported to avoid builder -> budget -> family -> builder.
_ROLLUP_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "estimated_reasoning_tokens",
)

# Attribution rolled onto a turn: (turn key, assistant attr). Always a LIST -- see
# roll_up_turn. `effort` has no `attribution_` prefix upstream and keeps the vendor's name.
_ATTRIBUTION_FIELDS = (
    ("skill", "attribution_skill"),
    ("plugin", "attribution_plugin"),
    ("agent", "attribution_agent"),
    ("effort", "effort"),
)


def _link_parents(graph: Graph) -> None:
    """Set `parent_id` on every node from its parent's `children`.

    One derived pass rather than assignment at each construction site, so the two directions
    cannot disagree: `children` is written in several places during assembly and a missed one
    would leave a node silently unparented.
    """
    for node in graph.nodes.values():
        for child_id in node.children:
            child = graph.nodes.get(child_id)
            if child is not None:
                child.parent_id = node.id


def roll_up_turn(graph: Graph, turn_id: str) -> dict[str, int] | None:
    """Sum the assistant usage beneath one turn onto `turn.attrs["tokens"]`.

    **Nested, not flat.** A turn carrying `output_tokens` beside an assistant's identically
    named field invites summing both and double counting the whole session -- the same trap as
    `estimated_reasoning_tokens` living inside `output_tokens`. A nested object reads as a
    rollup at a glance.

    **Containment only.** Tokens spent by subagents this turn spawned are NOT included: they
    live in a separate graph, and a build-time number must be well-defined without a loaded
    family. `budget(by="turn")` is the one that walks the spawn edge and includes them; these
    are the turn's own cost.

    Safe to store because a turn is only ever added complete -- `update()` rejects partial
    interaction spans (design §8) -- so nothing is appended beneath a turn after it is built.
    Exposed as a function, not inlined, so an incremental path can recompute one turn.

    Also writes `turn.attrs["attribution"]` in the same pass: the distinct non-null
    `attribution_skill` / `_plugin` / `_agent` / `effort` of the assistants beneath the turn.

    **Always a list**, `[]` when nothing was attributed -- never a scalar, never null. Only
    one distinct value per field occurs in the measured corpus (0 of 584 turns carry two), so
    a scalar-or-list rule would leave the list branch first executing in production untested,
    and would publish a `str | list | null` type every consumer must check on every read. One
    type, exercised on every turn, is the cheaper contract. Order is first-seen, which is why
    the walk below is a FIFO queue rather than the stack it used to be -- a stack pops siblings
    in reverse and would make the order an artefact of the traversal. Summing is
    order-independent, so the token totals are unaffected by the change.

    Containment only, exactly like the tokens: attribution inside a subagent this turn spawned
    lives in the child graph and is not pulled across the spawn edge.
    """
    turn = graph.nodes.get(turn_id)
    if turn is None:
        return None
    totals = dict.fromkeys(_ROLLUP_FIELDS, 0)
    attribution: dict[str, list] = {key: [] for key, _ in _ATTRIBUTION_FIELDS}
    queue = deque(turn.children)
    while queue:
        node = graph.nodes.get(queue.popleft())
        if node is None:
            continue
        if node.type is NodeType.ASSISTANT:
            for field in _ROLLUP_FIELDS:
                value = node.attrs.get(field)
                if isinstance(value, (int, float)):
                    totals[field] += int(value)
            for key, attr in _ATTRIBUTION_FIELDS:
                value = node.attrs.get(attr)
                if value is not None and value not in attribution[key]:
                    attribution[key].append(value)
        queue.extend(node.children)
    turn.attrs["tokens"] = totals
    turn.attrs["attribution"] = attribution
    return totals


def _command_text(attrs: dict, path: str) -> str | None:
    value: object = attrs
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value if isinstance(value, str) else None


def _merge_tool(call: dict, result: dict | None, query_config: QueryConfig) -> tuple[Node, list[dict]]:
    """A ``tool_call`` merged with its ``tool_result`` (or None) into one tool node."""
    p = _payload(call)
    rp = _payload(result) if result is not None else {}
    node = Node(
        id=call["record_id"],
        type=NodeType.TOOL,
        ts=call.get("event_ts"),
        attrs={
            "tool_name": p.get("tool_name"),
            "input": p.get("input", {}),
            "exit_status": p.get("exit_status"),
            "is_error": bool(rp.get("is_error", False)),
            "duration_ms": p.get("duration_ms"),
            "blocked_on_user_ms": None,  # OTEL-only; null when built from JSONL (design §4.1)
            "tool_denial_kind": rp.get("tool_denial_kind"),
            # bytes of tool-result content the normalizer already measured, and the MCP server/tool
            # for an mcp__ call -- carried onto the node so consumers (tool byte totals, mcp rollup)
            # don't have to re-derive them from raw. Null on tools that produce none.
            "bytes_read": p.get("bytes_read"),
            "mcp_server": p.get("mcp_server"),
            "mcp_tool": p.get("mcp_tool"),
        },
    )
    warnings: list[dict] = []
    extractor = query_config.command_extractors.get(p.get("tool_name"))
    node.attrs["cmd"] = []
    if extractor is not None:
        try:
            extraction = extract_commands(
                extractor.parser,
                _command_text(node.attrs, extractor.command_field),
                basename_style=extractor.basename_style,
                command_case=extractor.command_case,
            )
            node.attrs["cmd"] = extraction.commands
            warnings.extend(extraction.warnings)
        except CommandExtractorError:
            raise
    if result is not None:
        result_attrs = extract_result(
            p.get("tool_name"), rp, mcp_server=p.get("mcp_server"), mcp_tool=p.get("mcp_tool")
        )
        if result_attrs is not None:
            node.attrs["result"] = result_attrs
    return node, warnings


_FILE_FIELDS = (
    "path", "extension", "artifact_class", "operation",
    "hunks", "lines_added", "lines_removed", "mime_type", "remote_id",
)


def _tool_files(call: dict, file_changes: list[dict]) -> list[dict]:
    """The files a tool touched -- the "goods" (design §6.5, §6.6).

    Two sources, both already in the event log: the ``tool_call``'s own ``files[]`` (local
    paths and remote artifacts), and any ``file_change`` events that derive from this call
    (``file_change.derived_from_event_id == tool_call.record_id``). Deduped by ``path`` (or
    ``remote_id`` for a remote artifact); the tool_call's entry wins on a tie.
    """
    out: list[dict] = []
    seen: set = set()
    for src in [*(_payload(call).get("files") or []), *file_changes]:
        key = src.get("path") or src.get("remote_id")
        if key is None or key in seen:
            continue
        seen.add(key)
        out.append({f: src.get(f) for f in _FILE_FIELDS})
    return out


def _refine_write_operations(files: list[dict], tool: Node) -> None:
    """Use the structured Write result to distinguish create from update when available."""
    if tool.attrs.get("tool_name") != "Write":
        return
    result = tool.attrs.get("result")
    file_result = result.get("file") if isinstance(result, dict) else None
    kind = file_result.get("kind") if isinstance(file_result, dict) else None
    operation = {"create": "create", "update": "edit"}.get(kind)
    if operation is not None:
        for file in files:
            file["operation"] = operation


def _classify_memory_files(
    files: list[dict], *, vendor: str, query_config: QueryConfig,
) -> bool:
    """Mark files matching the vendor's ordered memory rules; first matching rule wins."""
    rules = query_config.memory_classifiers.get(vendor, [])
    matched = False
    for file in files:
        path = file.get("path")
        if not isinstance(path, str) or not path:
            continue
        normalized_path = path.replace("\\", "/")
        for rule in rules:
            if fnmatchcase(normalized_path, rule.path_glob):
                file["is_memory"] = True
                file["memory_kind"] = rule.kind
                matched = True
                break
    return matched


def _spawn_node(event: dict, result: dict | None) -> Node:
    """A ``sub_agent_spawn`` -> spawn node; child_session_id rides in refs (late-bound).

    Merges the same ``tool_result`` block a ``tool_call`` would (plan Step 2): a spawn's
    result joins by the identical ``tool_use_id == record_id`` convention, but the results
    map keyed off that join was, until this step, only ever consulted from the ``tool_call``
    branch below -- so 75 of 75 spawn results silently found no home. "Task" is passed as the
    facet lookup key regardless of whether the vendor originally named this block ``Agent`` or
    ``Task``: both collapse to one `sub_agent_spawn` event upstream (normalizer's
    `_SPAWN_TOOL_NAMES`) with an identical result shape, and `results.py` registers `Agent` as
    an alias of the `Task` table for exactly this call site.
    """
    p = _payload(event)
    node = Node(
        id=event["record_id"],
        type=NodeType.SPAWN,
        ts=event.get("event_ts"),
        attrs={
            "agent_type": p.get("agent_type"),
            "description": p.get("description"),
            "prompt_char_count": p.get("prompt_char_count"),
            "requested_model": p.get("requested_model"),
            "status": p.get("status"),
            "resolved_model": p.get("resolved_model"),
            "run_in_background": p.get("run_in_background"),
            "duration_ms": p.get("duration_ms"),
        },
        refs={"child_session_id": p.get("child_session_id")},
    )
    if result is not None:
        result_attrs = extract_result("Task", _payload(result))
        if result_attrs is not None:
            node.attrs["result"] = result_attrs
    return node


def _compaction_node(event: dict) -> Node:
    """A ``compaction`` event -> a session-level out-of-band node.

    Compaction is a harness action, not part of any turn (design §4.1): the harness pauses the
    conversation, summarizes it with a dedicated API call, and resumes. The node has no
    containment parent, but is **not** appended to ``graph.roots`` -- that field is specifically
    the ordered turn index. It is reached by type or through the non-containment ``refs`` links
    it and the turns exchange (set in ``_attach_compactions``).

    ``usage_known`` is false on purpose: the summarization request's own token usage is absent
    from the JSONL, so the ``tokens_*`` deltas describe the context sizes it reset, never the
    cost of the call (see issue #40). The node's id is the event's ``record_id`` (== the boundary
    record's uuid), unique per compaction.
    """
    p = _payload(event)
    return Node(
        id=event["record_id"],
        type=NodeType.COMPACTION,
        ts=event.get("event_ts"),
        attrs={
            "trigger": p.get("trigger"),
            "tokens_before": p.get("tokens_before"),
            "tokens_after": p.get("tokens_after"),
            "tokens_reclaimed": p.get("tokens_reclaimed"),
            "duration_ms": p.get("duration_ms"),
            "usage_known": False,
        },
        refs={"triggering_turn_id": None, "resumed_turn_id": None},
    )


def _append_ref(node: Node, key: str, value: str) -> None:
    """Append one unique local node id to a plural non-containment reference."""
    refs = node.refs.setdefault(key, [])
    if value not in refs:
        refs.append(value)


def _attach_compactions(graph: Graph, turns: list[_Turn], events: list[dict]) -> None:
    """Add one session-level node per compaction event and link the turns it relates.

    A compaction has no ``promptId``, so ``_segment_turns`` absorbs it into whichever turn was
    open and the per-turn dispatch loop drops it. This pass, run after the turn nodes exist,
    recovers it:

    * **resumed turn** -- the turn holding the ``isCompactSummary`` message, i.e. the event whose
      ``parent_id`` is this compaction's ``record_id`` (verified structural link). That turn gets
      ``attrs.is_after_compact = True`` and a plural ``refs.resumed_from_compaction_ids`` link;
      the node records ``resumed_turn_id``.
      The flag is named vendor-neutrally, not copied from the vendor's ``isCompactSummary`` -- a
      different vendor may signal a resume differently.
    * **causing turn** -- only for an ``auto`` compaction. A user-initiated (``manual``) compact is
      unrelated to any turn, so it is left unlinked. The triggering turn is resolved from
      ``raw.logicalParentUuid`` (the preserved pre-compaction tail) to the turn that owns that
      record; array position is unusable here because the preserved-segment re-emission leaves the
      event stream out of chronological order. That turn gets ``attrs.triggered_compaction = True``
      and a plural ``refs.triggered_compaction_ids`` link; the node records
      ``triggering_turn_id``. Best-effort: an unresolvable pointer leaves the mark off rather
      than guessing.

    Compactions are scanned from the complete event list, not only from turn members. This keeps
    a compaction that precedes the first promptId visible even when neither related turn resolves.
    """
    turn_id_by_record: dict[str, str] = {}
    resumed_turn_by_compaction: dict[str, str] = {}
    for turn in turns:
        tid = f"turn:{turn.run_ordinal}"
        for event in turn.events:
            rid = event.get("record_id")
            if isinstance(rid, str):
                turn_id_by_record.setdefault(rid, tid)
            parent = event.get("parent_id")
            if (
                event.get("event_type") == "user_message"
                and _raw(event).get("isCompactSummary") is True
                and isinstance(parent, str)
            ):
                resumed_turn_by_compaction.setdefault(parent, tid)

    for event in events:
        if event.get("event_type") != "compaction":
            continue
        node = _compaction_node(event)

        resumed_turn_id = resumed_turn_by_compaction.get(node.id)
        if resumed_turn_id is not None:
            resumed = graph.nodes.get(resumed_turn_id)
            if resumed is not None:
                resumed.attrs["is_after_compact"] = True
                _append_ref(resumed, "resumed_from_compaction_ids", node.id)
            node.refs["resumed_turn_id"] = resumed_turn_id

        if node.attrs.get("trigger") == "auto":
            lpu = _raw(event).get("logicalParentUuid")
            triggering_turn_id = turn_id_by_record.get(lpu) if isinstance(lpu, str) else None
            if triggering_turn_id is not None:
                triggering = graph.nodes.get(triggering_turn_id)
                if triggering is not None:
                    triggering.attrs["triggered_compaction"] = True
                    _append_ref(triggering, "triggered_compaction_ids", node.id)
                node.refs["triggering_turn_id"] = triggering_turn_id

        graph.add(node)


def _initiating_user_message(turn: _Turn) -> dict | None:
    """The turn's initiating ``user_message`` event, or None if the turn has none.

    First non-injected ``user_message``, falling back to the first user_message. Shared by
    :func:`_user_prompt_node` (the prompt node) and :func:`_turn_mode` (permission_mode /
    plan_mode, design §4.1b) so the two never disagree about which record opened the turn.
    tool_result records also carry the promptId but are outcomes, not prompts, and are ignored
    here.
    """
    user_msgs = [e for e in turn.events if e.get("event_type") == "user_message"]
    if not user_msgs:
        return None
    return next((e for e in user_msgs if not _payload(e).get("is_injected")), user_msgs[0])


def _user_prompt_node(turn: _Turn, attachment_ids: list[str]) -> Node | None:
    """The turn's initiating user prompt, with attachment ids attached as resources."""
    initiating = _initiating_user_message(turn)
    if initiating is None:
        return None
    p = _payload(initiating)
    return Node(
        id=initiating["record_id"],
        type=NodeType.USER_PROMPT,
        ts=initiating.get("event_ts"),
        attrs={
            "text": p.get("text"),
            "model": None,
            "is_injected": bool(p.get("is_injected", False)),
            "resources": attachment_ids,
        },
    )


def _turn_mode(turn: _Turn) -> tuple[str | None, bool | None]:
    """``(permission_mode, plan_mode)`` from the turn's initiating user_message (design §4.1b).

    Both null when the turn has no user_message at all, or when the vendor recorded no mode
    on the record it does have -- null, not false, for plan_mode: unknown and not-plan-mode
    are different claims. Not read from tool_result: 0 of 7,927 carry a permissionMode: there
    is no mid-turn mode change to capture.
    """
    initiating = _initiating_user_message(turn)
    if initiating is None:
        return None, None
    p = _payload(initiating)
    mode = p.get("permission_mode")
    permission_mode = mode if isinstance(mode, str) else None
    plan = p.get("plan_mode")
    plan_mode = plan if isinstance(plan, bool) else None
    return permission_mode, plan_mode


def _turn_status(turn: _Turn, has_assistant: bool) -> str:
    """completed (close marker) | interrupted (work, no marker) | empty (no assistant)."""
    if not has_assistant:
        return "empty"
    if turn.turn_duration_ms is not None:
        return "completed"
    return "interrupted"


def _turn_duration_ms(turn: _Turn) -> int | None:
    """turn_duration.durationMs, else last_event_ts - start_ts (design §3.1: never OTEL here)."""
    if turn.turn_duration_ms is not None:
        return turn.turn_duration_ms
    start = _ts_to_ms(turn.start_ts)
    ends = [_ts_to_ms(e.get("event_ts")) for e in turn.events]
    ends = [e for e in ends if e is not None]
    if start is None or not ends:
        return None
    return int(round(max(ends) - start))


# --- session-scoped lookups (plan Step 2) -----------------------------------

def _collect_results_and_file_changes(
    events: list[dict], *, session_id: str, vendor: str,
) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """The ``tool_result`` and ``file_change`` lookups, built ONCE across the whole session
    (plan Step 2) rather than rebuilt inside the per-turn loop.

    ``tool_use_id`` is a session-scoped identity -- a turn is a presentation grouping, not an
    identity scope -- so a result event that lands in a different turn than its call (6 of
    7,927 in the reference corpus, written after the turn key had already rotated) still
    resolves against its call. This attaches the result to the CALL's node; the node itself
    stays in the call's turn (the per-turn loop in :func:`build_graph` is unchanged) -- only the
    lookup widens.

    First-wins on both maps, counted (plan Step 3): a second ``tool_result`` for a
    ``tool_use_id`` already seen, or a duplicate ``file_change`` record for the same source
    call, is a byte-identical replay (8 observed, one session) rather than new information, so
    the first value is kept and the second is reported through :mod:`counters`, never silently
    absorbed. An ordinary tool call touching several files legitimately yields several distinct
    ``file_change`` events under the same ``derived_from_event_id`` -- that is not a collision
    and is not counted; only a duplicate of the same source record id is.
    """
    results: dict[str, dict] = {}
    file_changes: dict[str, list[dict]] = {}
    seen_file_change_records: set[tuple[str, str]] = set()

    for event in events:
        etype = event.get("event_type")
        if etype == "tool_result":
            tuid = _payload(event).get("tool_use_id")
            if isinstance(tuid, str):
                if tuid in results:
                    counters.results_collision(
                        session_id=session_id, vendor=vendor, tool_use_id=tuid,
                    )
                else:
                    results[tuid] = event
        elif etype == "file_change":
            src = _payload(event).get("derived_from_event_id")
            if isinstance(src, str):
                record_id = event.get("record_id")
                dedup_key = (src, record_id)
                if record_id is not None and dedup_key in seen_file_change_records:
                    counters.file_changes_collision(
                        session_id=session_id, vendor=vendor, derived_from_event_id=src,
                    )
                    continue
                if record_id is not None:
                    seen_file_change_records.add(dedup_key)
                file_changes.setdefault(src, []).append(_payload(event))

    return results, file_changes


def _claim_children(
    claimed: dict[str, str], owner_id: str, child_ids: list[str], *, session_id: str,
    vendor: str,
) -> list[str]:
    """Filter ``child_ids`` to those not already claimed by another owner, registering the
    accepted ones (plan Step 3).

    ``Graph.add``'s first-wins keeps the ORIGINAL node object on an id collision, but by itself
    does not stop a later turn's (or a later assistant's) ``children`` list from ALSO naming
    that id -- the same node would then be reachable from two parents, and any walk of
    ``children`` (the token rollup, ``budget``) counts it twice. This is the guard that clears
    that remainder: it is session-scoped, not reset per turn, because the replay it guards
    against can and does cross a turn boundary (design §6 "First-wins inserts over last-wins";
    measured: this guard alone clears the +575,309 double-count that ``Graph.add``'s fix does
    not, on session ``10b1950e``). Re-claiming by the SAME owner (an id listed twice for one
    parent) is refused too, for the identical reason -- the walk would still visit it twice.
    """
    accepted: list[str] = []
    for child_id in child_ids:
        owner = claimed.get(child_id)
        if owner is not None:
            counters.child_collision(
                session_id=session_id, vendor=vendor, child_id=child_id,
                owner_id=owner, claimant_id=owner_id,
            )
            continue
        claimed[child_id] = owner_id
        accepted.append(child_id)
    return accepted


# --- entry points ----------------------------------------------------------

class GraphBuildError(ValueError):
    """Stable public failure raised when a normalized session cannot be built."""

    code = "graph_build_failed"


def _build_graph_model(
    session: dict, config: ReasoningConfig | None = None, *, query_config: QueryConfig | None = None,
) -> Graph:
    """Internal model-producing builder used while assembling an in-memory Family.

    ``config`` selects the reasoning-estimate constants (default: the built-in
    :func:`analysis.causal_graph.config.default_config`). Keyed by ``session["vendor"]``
    inside :func:`analysis.causal_graph.reasoning.estimate_reasoning` -- a vendor this config
    has no section for gets ratio 1.0 and zero framing, never Claude's constants.
    """
    if config is None:
        config = default_config()
    if query_config is None:
        query_config = load_query_config()
    events = session.get("events") or []
    vendor = session["vendor"]
    session_id = session["session_id"]

    title = None
    for event in events:
        if event.get("event_type") == "session_start":
            title = _payload(event).get("ai_title")
            break

    turns, startup = _segment_turns(events, session_id=session_id, vendor=vendor)

    # Session-scoped, built once (plan Step 2) -- a result or file_change that lands in a
    # different turn than its call (the turn key rotated before the record was written) still
    # resolves against the call's node. See _collect_results_and_file_changes.
    results, file_changes = _collect_results_and_file_changes(
        events, session_id=session_id, vendor=vendor,
    )
    # child_id -> owning parent id, session-scoped (plan Step 3) -- threaded through every
    # turn's children assignment below so a replay that crosses a turn boundary is still
    # caught. See _claim_children.
    claimed_children: dict[str, str] = {}

    graph = Graph(
        session_id=session_id,
        vendor=vendor,
        source="jsonl",
        built="complete",
        parent_session_id=session.get("parent_session_id"),
        meta={
            "title": title,
            "objective": None,  # unsolved in v0 (design §4.3); ai_title is too weak to stand in
            "resources": [
                e["record_id"] for e in startup if e.get("event_type") == "attachment"
            ],
            "query_config_revision": query_config.revision,
            "warnings": [],
        },
    )

    for turn in turns:
        turn_id = f"turn:{turn.run_ordinal}"
        assistants = _collapse_assistants(turn.events, vendor=vendor, config=config)
        by_message: dict[str, Node] = {n.attrs["message_id"]: n for n in assistants}
        for node in assistants:
            graph.add(node)

        # tool_call + tool_result -> one tool node; sub_agent_spawn -> spawn node. `results`
        # and `file_changes` are the session-scoped maps built above (plan Step 2) -- a call in
        # THIS turn may resolve against a result recorded after the turn key rotated.
        action_ids_by_message: dict[str, list[str]] = {}
        orphan_action_ids: list[str] = []
        for event in turn.events:
            etype = event.get("event_type")
            if etype == "tool_call":
                node, extraction_warnings = _merge_tool(
                    event, results.get(event.get("record_id")), query_config,
                )
                for warning in extraction_warnings:
                    structured = {**warning, "node_id": node.id, "tool_name": node.attrs["tool_name"]}
                    graph.meta["warnings"].append(structured)
                    _log.warning("command_extraction_warning", session_id=session_id, **structured)
                files = _tool_files(event, file_changes.get(event.get("record_id"), []))
                if files:
                    _refine_write_operations(files, node)
                    if _classify_memory_files(files, vendor=vendor, query_config=query_config):
                        node.attrs["memory_access"] = True
                    node.attrs["files"] = files
                    node.attrs["file_change"] = True  # the marker ops select(file_change) reads
            elif etype == "sub_agent_spawn":
                node = _spawn_node(event, results.get(event.get("record_id")))
            else:
                continue
            graph.add(node)
            mid = _payload(event).get("message_id")
            if isinstance(mid, str) and mid in by_message:
                action_ids_by_message.setdefault(mid, []).append(node.id)
            else:
                orphan_action_ids.append(node.id)

        for mid, action_ids in action_ids_by_message.items():
            by_message[mid].children = _claim_children(
                claimed_children, mid, action_ids, session_id=session_id, vendor=vendor,
            )

        attachment_ids = [
            e["record_id"] for e in turn.events if e.get("event_type") == "attachment"
        ]
        user_prompt = _user_prompt_node(turn, attachment_ids)

        has_assistant = bool(assistants)
        if not has_assistant:
            counters.empty_turn(session_id=session_id, vendor=vendor, turn_id=turn_id)
        permission_mode, plan_mode = _turn_mode(turn)
        turn_node = Node(
            id=turn_id,
            type=NodeType.TURN,
            ts=turn.start_ts,
            attrs={
                "turn_num": turn.run_ordinal,
                "prompt_id": turn.prompt_id,  # vendor value, demoted to an attribute (§4.1)
                "status": _turn_status(turn, has_assistant),
                "duration_ms": _turn_duration_ms(turn),
                "permission_mode": permission_mode,
                "plan_mode": plan_mode,
            },
        )
        children: list[str] = []
        if user_prompt is not None:
            graph.add(user_prompt)
            children.append(user_prompt.id)
        children.extend(n.id for n in assistants)
        children.extend(orphan_action_ids)
        turn_node.children = _claim_children(
            claimed_children, turn_id, children, session_id=session_id, vendor=vendor,
        )
        graph.add(turn_node)
        graph.roots.append(turn_node.id)

    _link_parents(graph)
    for root in graph.roots:
        roll_up_turn(graph, root)

    # Compaction is out-of-band (no promptId), so it is handled after turns are complete: it
    # adds session-level compaction nodes and marks the turns each one links, never touching
    # `roots` (turns-only) or the token rollup (assistant-only, so metrics-neutral).
    _attach_compactions(graph, turns, events)

    _log.debug(
        "graph_built",
        session_id=graph.session_id,
        turns=len(graph.roots),
        nodes=len(graph.nodes),
    )
    return graph


def build_graph(
    normalized_session: dict,
    config: ReasoningConfig | None = None,
    *,
    query_config: QueryConfig | None = None,
) -> dict:
    """Return a JSON-compatible graph dictionary without performing source or sink I/O.

    ``normalized_session`` is exactly one member of a normalized envelope's ``sessions``
    array. Retrieval and storage remain the caller's responsibility.
    """
    if not isinstance(normalized_session, dict):
        raise GraphBuildError("normalized_session must be a dictionary")
    try:
        return _build_graph_model(
            normalized_session, config, query_config=query_config,
        ).to_dict()
    except GraphBuildError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise GraphBuildError(f"normalized session cannot be built: {exc}") from exc
