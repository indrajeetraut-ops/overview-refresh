"""What every graph node and attribute means.

The *shapes* are derived from real built graphs and cannot drift into fiction. The *meaning*
cannot be derived from anything — no amount of walking a node reveals that
`estimated_reasoning_tokens` is inside `output_tokens` rather than on top of it, or that
`resources` holds attachment event ids rather than file paths. So the prose is authored here
and rendered into both consumers:

  * `src/common/contracts/causal_graph/schemas/graph.schema.json` — the published contract:
    the generator refuses to write an undocumented field and the drift guard fails on stale
    output. Workers and programs integrate against this.
  * `examples/causal_graph/README.md` — the field table beside each node dump, regenerated
    deliberately by a human.

One authored source, two renderings, so the two cannot disagree.

**A field without an entry here cannot be published.** Adding an attr means adding a line here
in the same change — an undocumented field in a published contract is worse than no contract,
because it looks authoritative and says nothing.
"""
from __future__ import annotations

__all__ = ["ATTR_DOCS", "NODE_DOCS", "missing_descriptions"]

NODE_DOCS = {
    "turn": (
        "One `promptId` run: a user prompt and everything the agent did before the next "
        "prompt. Turns are the graph's roots — they have no parent node, which is why two "
        "nodes in different turns have no containment path between them. The canonical "
        "identity is **`{session_id, turn_num}`**. Inside one session the local node `id` is "
        "`\"turn:{turn_num}\"`; `attrs.turn_num` is the same 1-based sequence number. "
        "`attrs.prompt_id` is retained only as vendor provenance and is never identity. "
        "Every family-wide index also scopes non-turn local ids by `session_id`, because "
        "sibling agent graphs may legitimately reuse any local id. A compaction does not add a "
        "turn or renumber one here — it is a separate `compaction` node, and the turns it "
        "relates carry `is_after_compact` / `triggered_compaction`. **Known limit:** `turn_num` is "
        "positional — stable across a rebuild of the same transcript, but if the vendor itself "
        "rewrites earlier history the positions shift."
    ),
    "user_prompt": (
        "The instruction that opened a turn. In a subagent graph this is the parent's "
        "delegation, not something a person typed — `is_injected` tells them apart."
    ),
    "assistant": (
        "One API response. Claude Code splits a response across several transcript records; "
        "they are collapsed to one node here, so one node is one billable response."
    ),
    "tool": (
        "One tool invocation merged with its result, so a node carries both what was asked "
        "and what happened. Tools under one assistant are an unordered set — they share a "
        "`message_id` and nothing sequences them."
    ),
    "spawn": (
        "A subagent delegation. Exactly two tool names become one — `Agent` and `Task`; every "
        "other tool stays a `tool` node. `TaskCreate`/`TaskUpdate` are todo-list bookkeeping "
        "and delegate nothing, despite the name. The subagent's work is in a *separate* graph, "
        "reached through `refs.child_session_id`; nothing about the child is inlined here."
    ),
    "compaction": (
        "A context compaction: the harness paused the conversation, summarized it with a "
        "dedicated API call, and resumed with a smaller context. **Not part of any turn** — it "
        "is a session-level out-of-band node with no containment parent and is not in `roots` "
        "(the ordered turn index). It is found by type or through non-containment `refs`: "
        "`refs.resumed_turn_id` (the turn that continued after it, also flagged "
        "`turn.is_after_compact`) and, for an `auto` compaction only, "
        "`refs.triggering_turn_id` (the turn whose growth triggered it, also flagged "
        "`turn.triggered_compaction`). Its summarization-call usage is not recoverable here — "
        "see `usage_known`."
    ),
}

ATTR_DOCS: dict[str, dict[str, str]] = {
    "turn": {
        "turn_num": (
            "The 1-based position of this turn within its session. Together with the graph's "
            "`session_id` it is the canonical turn identity. The local node `id` is derived "
            "as `turn:{turn_num}`."
        ),
        "prompt_id": (
            "The vendor's `promptId` for this turn, unchanged, queryable — but **not the "
            "turn's identity**. It is provenance for correlation with a raw transcript; "
            "identity is `{session_id, turn_num}`."
        ),
        "status": (
            "`completed` (a close marker was seen), `interrupted` (work happened, no close "
            "marker — the session was cut off), or `empty` (a prompt with no work after it)."
        ),
        "duration_ms": (
            "Wall-clock for the turn. Never null: taken from the vendor's `turn_duration` "
            "record, else OTEL's `interaction.duration_ms`, else last-event minus start."
        ),
        "tokens": (
            "Usage of every assistant **beneath this turn**, summed at build time: "
            "`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, "
            "`estimated_reasoning_tokens`. Nested rather than flat on purpose — a turn "
            "carrying `output_tokens` beside an assistant's identically named field invites "
            "summing both and double counting the session. **Containment only**: tokens spent "
            "by subagents this turn spawned are not included, because they live in a separate "
            "graph. `budget(by='turn')` is the figure that walks the spawn edge and includes "
            "them; this is the turn's own cost."
        ),
        "permission_mode": (
            "The permission mode the turn's opening prompt was sent under — `auto`, `plan`, "
            "`acceptEdits`, `default`. **Null, not a default**, when the vendor recorded no "
            "mode (245 of 584 turns in the measured corpus, all from older agent versions): "
            "unknown and a particular mode are different claims. Stamped once per prompt — no "
            "tool result carries one — so there is no mid-turn mode change to read."
        ),
        "plan_mode": (
            "True when this turn ran in plan mode — `permission_mode == 'plan'`, kept "
            "separately because \"was this planning or doing\" is the common filter. Null, "
            "not false, when no mode was recorded. A *planning episode* spans several turns "
            "and is not stored: it is a run of consecutive turns with `plan_mode` true, closed "
            "by an accepted `ExitPlanMode` (see `tool.result.exit_plan_mode`) or by nothing at "
            "all — 5 of 10 runs in the corpus ended without one."
        ),
        "attribution": (
            "Which skill, plugin, agent and effort level produced the work under this turn — "
            "the distinct non-null values of the assistants beneath it, as "
            "`{skill, plugin, agent, effort}`. **Every field is always a list**, `[]` when "
            "nothing was attributed: never a scalar, never null, so a consumer needs one code "
            "path rather than a type check. Order is first-seen. Nested rather than flat for "
            "the same reason `tokens` is — a turn carrying `attribution_skill` beside an "
            "assistant's identically named field invites reading one for the other. "
            "**Containment only**: a subagent's attribution stays in the child graph."
        ),
        "is_after_compact": (
            "Present and `true` only on a turn that resumed after a context compaction — its "
            "`isCompactSummary` message descends from a compaction node listed in "
            "`refs.resumed_from_compaction_ids`. Absent (not `false`) on an ordinary turn. "
            "Named vendor-neutrally "
            "rather than after the vendor's `isCompactSummary` flag, since another vendor may "
            "signal a resume differently."
        ),
        "triggered_compaction": (
            "Present and `true` only on the turn whose context growth triggered an **auto** "
            "compaction, resolved from the compaction's preserved pre-compaction tail; "
            "`refs.triggered_compaction_ids` links every such node. Absent on every other turn — "
            "a user-initiated "
            "(`manual`) compaction is unrelated to any turn and marks none. For a mid-turn auto "
            "compaction this is the same turn that also carries `is_after_compact`."
        ),
    },
    "user_prompt": {
        "text": (
            "The instruction, uncapped. Prompts are the primary intent signal, so unlike "
            "every other content field they are never truncated."
        ),
        "is_injected": (
            "**True means the harness produced this, not a person** — a skill body, a system "
            "reminder, a task notification, a subagent's delegated instruction. Filter on it "
            "before treating a prompt as something a human asked for."
        ),
        "model": "Model named in the prompt record; usually null, the assistant carries it.",
        "resources": (
            "Ids of `attachment` events in context for this turn — pointers, not copies. The "
            "attachment bodies live in the normalized events, not in the graph."
        ),
    },
    "assistant": {
        "message_id": (
            "The API response id. Several transcript records share it; this node is their "
            "collapse, so the id is also the node's identity."
        ),
        "model": "Model that produced the response. Subagents routinely differ from the parent.",
        "has_thinking": (
            "Whether the model reasoned here. The reasoning text itself is stripped by Claude "
            "Code before the transcript is written, so this flag plus the token estimate below "
            "is all that survives."
        ),
        "input_tokens": "Tokens sent. From the response's settled usage, not a per-record partial.",
        "output_tokens": (
            "Tokens produced, **including reasoning** — it is billed here and never broken "
            "out separately by the vendor."
        ),
        "cache_read_tokens": "Tokens served from cache.",
        "cache_write_tokens": "Tokens written to cache, all TTLs.",
        "estimated_reasoning_tokens": (
            "Reasoning recovered by subtraction: `output_tokens` minus the counted visible "
            "output minus per-tool-call framing. **Inside `output_tokens`, not on top of it** "
            "— summing both double counts. An estimate unless `reasoning_source` says "
            "`reported`. See docs/design/notes/claude_token_usage.md."
        ),
        "reasoning_source": (
            "`reported` when the vendor published a reasoning count (Codex does today, Claude "
            "may later) — then no estimation ran. `estimated` when it was derived."
        ),
        "reasoning_ratio": (
            "The tokenizer ratio applied to reach the estimate: 1.52 for the Claude 5 family, "
            "1.13 for Sonnet 4.6. **1.0 means the model was not measured** and the raw "
            "tokenizer count was used unconverted, making the estimate an upper bound."
        ),
        "estimated_visible_tokens": (
            "Raw tokenizer count of the visible output — reply text plus tool names and "
            "arguments, reasoning excluded. Stored unconverted so a ratio change re-derives "
            "from the graph without re-running the normalizer."
        ),
        "tool_call_count": (
            "Tool-use blocks in this response. Each costs a fixed block of output tokens "
            "beyond its arguments, which is why the reasoning estimate needs the count."
        ),
        "attribution_skill": (
            "The skill whose instructions were in force for this response, as the harness "
            "stamped it — e.g. `my-arch-doc`, `superpowers:writing-plans`. Null when none was "
            "active. This is what the response actually ran under, which the `skill_listing` "
            "attachment cannot tell you: that only says what was offered in context."
        ),
        "attribution_plugin": (
            "The plugin supplying the active skill. Looks like the prefix of "
            "`attribution_skill` (`superpowers:…`) and is carried separately anyway — the two "
            "are not in exact correspondence (3,601 vs 3,879 records in the measured corpus), "
            "so deriving one from the other would be a guess."
        ),
        "attribution_agent": (
            "The agent type this response was produced by — `general-purpose`, `Explore`, a "
            "named custom agent. Non-null only on a subagent stream, so it is null throughout "
            "a master session. Not the same as `spawn.agent_type`, which is what the parent "
            "*requested*; this is what the running side reports."
        ),
        "effort": (
            "The reasoning-effort level the response ran at, when the vendor reported one. "
            "Null when unreported — which is not the same as low effort."
        ),
    },
    "tool": {
        "cmd": (
            "Ordered executable basenames extracted during graph construction from a configured "
            "shell-capable tool's complete command. It is absent for other harness tools and "
            "is never reparsed while querying."
        ),
        "cmd": (
            "Ordered executable basenames extracted from configured shell-capable tool input. "
            "Empty for non-shell tools or failed command parsing; repeated commands are retained."
        ),
        "tool_name": (
            "The tool as the model named it, e.g. `Bash`, `Edit`, `Read`. Note "
            "`TaskCreate`/`TaskUpdate` appear here and are todo-list bookkeeping, not subagent "
            "delegation — only `Agent`/`Task` become `spawn` nodes."
        ),
        "input": (
            "The arguments the model generated, verbatim and vendor-shaped. Generated file "
            "content is scrubbed upstream when configured."
        ),
        "is_error": "Whether the tool reported failure. From the paired result, not the call.",
        "exit_status": "Process exit status, for tools that run commands.",
        "duration_ms": (
            "How long the tool took. Frequently null even on success — only present when the "
            "vendor happened to report it."
        ),
        "blocked_on_user_ms": (
            "Time spent waiting for a human to approve the call. OTEL-only; always null in a "
            "graph built from JSONL, which has no equivalent."
        ),
        "tool_denial_kind": (
            "Why a call was refused, when it was: the user declined, a permission rule blocked "
            "it. Null when the call was not denied."
        ),
        "file_change": (
            "True when this call touched files. `select(type='file_change')` matches on it — "
            "files are carried on tool nodes, there is no separate file node type."
        ),
        "files": (
            "One entry per file touched: `path`, `extension`, `artifact_class`, `operation`, "
            "`hunks`. Reach into it with a `files[].field` path — e.g. "
            "`where {\"files[].extension\": \".md\"}` — since none of it is top-level."
        ),
        "result": (
            "The tool's outcome beyond pass/fail, extracted from the vendor's raw result into "
            "vendor-neutral facets — `process` (Bash stdout/stderr), `git` (a durable git "
            "action the vendor flagged: commit/push/pr/branch), `file` (Read/Write/Edit facts "
            "not already on `files[]` — no `path`, no `hunks`, those live there), `interaction` "
            "(AskUserQuestion), `web` (WebFetch/WebSearch), `todo` (TaskCreate/Update/List), "
            "`skill` (Skill), `search` (ToolSearch), `worktree` (Enter/ExitWorktree), `message` "
            "(SendMessage), `findings` (ReportFindings), `monitor` (Monitor), `exit_plan_mode` "
            "(the ExitPlanMode tool — not plan mode, see `turn.plan_mode`), and `mcp` (any "
            "`mcp__*` tool, resolved through the vendor's parsed carrier when present). Always "
            "carries `outcome` (`ok`/`error`/`denied`) and `error_text` (the bare-string result "
            "on failure, else null); every other facet is present only when applicable — never "
            "an empty object. `result.unmapped` holds any vendor key no facet claimed, "
            "verbatim, so nothing is silently dropped as the vendor's shape drifts. Absent "
            "entirely, not an empty object, when the call has no result at all."
        ),
    },
    "spawn": {
        "agent_type": "Which agent was asked for, e.g. `Explore`, `general-purpose`.",
        "description": "Short task label recorded when the subagent was spawned; it is not the full delegated instruction.",
        "prompt_char_count": "Length of the spawn instruction before scrubbing. The instruction content is not repeated here.",
        "requested_model": "Model requested for the child at spawn time, when the producer supplied one.",
        "status": "Outcome once the child finished. Null while it is still running.",
        "resolved_model": (
            "Model the child actually used, which routinely differs from both the request and "
            "the parent — so a subagent's cost cannot be assumed from the parent's."
        ),
        "run_in_background": "Whether the parent continued without waiting for the child, when supplied by the spawn event.",
        "duration_ms": "Child duration recorded with the spawn event, when supplied; it is not calculated from a loaded child graph.",
        "result": (
            "The delegation's outcome, in the same facet-grouped shape as a tool node's "
            "`result` — see `tool.result`. Only the `delegation` facet is expected here in "
            "practice: `agent_id`, `prompt` (the full delegated instruction — the one place it "
            "survives; the call side only ever carries its character count), `duration_ms`, "
            "`tool_use_count`, `tool_stats`, and, for a delegation still running when its "
            "parent read the result, `is_async`/`output_file`/`can_read_output_file` in place "
            "of the duration/count fields. `agent_type`/`status`/`resolved_model` are not "
            "repeated here — they are already this node's own attrs, from the call side."
        ),
    },
    "compaction": {
        "trigger": (
            "`auto` (the harness compacted because the context hit its limit) or `manual` (a "
            "user-initiated `/compact`). Only an `auto` compaction is attributed to a triggering "
            "turn — see `turn.triggered_compaction`."
        ),
        "tokens_before": (
            "Context size just before the compaction, from the vendor's `compactMetadata` — the "
            "size that tripped the limit. **A context size, not this call's input tokens**: the "
            "summarization request's own usage is absent from the JSONL (`usage_known`)."
        ),
        "tokens_after": "Context size the session resumed with, from `compactMetadata`.",
        "tokens_reclaimed": (
            "`tokens_before - tokens_after` — how much context the compaction freed. A "
            "waste/efficiency signal, not a billed amount."
        ),
        "duration_ms": (
            "Wall-clock the harness spent compacting, from `compactMetadata`. This is the pause "
            "the summarization call cost in time, not its token cost."
        ),
        "usage_known": (
            "Always `false` for JSONL. The compaction is a dedicated Claude API request whose own "
            "token usage is **not** recorded in the JSONL, so its cost cannot be attributed from "
            "this source; the `tokens_*` fields are context sizes, not the call's usage (issue "
            "#40). The flag names the missing fact directly so a consumer never reads the deltas "
            "as usage or cost."
        ),
    },
}


def missing_descriptions(shapes: dict[str, dict]) -> list[str]:
    """`node_type.field` for every derived path with no entry above. Empty means complete."""
    gaps: list[str] = []
    for node_type, fields in sorted(shapes.items()):
        if node_type not in NODE_DOCS:
            gaps.append(f"{node_type} (node type itself)")
        known = ATTR_DOCS.get(node_type, {})
        for field in sorted(fields):
            if not known.get(field):
                gaps.append(f"{node_type}.{field}")
    return gaps
