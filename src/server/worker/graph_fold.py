"""GraphFold: compute `session_info` from a causal-graph family instead of the raw event fold
(combined sub-plan §3).

This is the extraction half. It takes an already-assembled `Family` (in tests, built in memory;
in production, loaded by GraphService from the session_graph/session_tree tables) plus the owning
`SessionState`, and produces the same `session_info` fields `EconFold` did -- from the graph's
named APIs and node surface, not by summing raw events.

Incremental: §4.A structural fields are implemented here; the §4.B heuristics (segments, story,
practices, cache, read_then_discard, interrupted, carry, resume_cost) are pending-port (parent Step
4) and are intentionally omitted -- the A/B differential (test_ab_graphfold) allowlists their absence
until each is restored. Fields still read from the event stream (only where the graph doesn't model
them, e.g. pr_list from pr_link events) are marked inline.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

import structlog

from server.worker import segments as segments_mod
from server.worker.fold_api import SessionFoldResult, SessionInfo, SessionInfoDelta, SessionState

from analysis.causal_graph import GraphContext, NodeType, budget, build_graph
from analysis.causal_graph.models import Family, Graph
from common import git_ident
from common.model_pricing import Pricer, price_to_micros
from common.model_pricing.errors import CatalogError

log = structlog.get_logger(module="worker.graph_fold")


def load_pricing_survivable(org: str | None = None, version: str | None = None) -> Pricer | None:
    """`GraphFold(pricing=...)`'s intended loader for real call sites (`ingest/app.py`,
    `worker/cli_work.py`) -- same posture as `otel_trueup.py`'s own pricing load: an absent
    catalog (`CatalogError`) is survivable (channel_cost/carry usd_micros just stay unpriced,
    the baseline before this existed), a malformed config (`ConfigError`) is not."""
    try:
        return Pricer.load(org, version)
    except CatalogError as exc:
        log.warning("pricing_catalog_absent", org=org, version=version, error=str(exc),
                    effect="channel_cost/carry usd_micros stay unpriced")
        return None


def repo_info(events: list[dict]) -> dict | None:
    """info.repo + the promoted repo_id/repo_id_provenance -- an evidence ladder over normalized
    events (pr_link > remote > path), copied verbatim from EconFold's `metrics.repo_info` (it reads
    events and common.git_ident, not the graph, and survives the fold's removal). The `remote`
    tier reads `session_start.git_remote` (a real `git remote get-url origin`, backfilled by
    collector/engine.py from the upload manifest's per-file `repo_url`/`repo` -- resolved
    client-side at the session's actual cwd and matched onto the file it belongs to directly,
    per the uploader/collector integration plan, 2026-09-22) ahead of anything scraped from a
    `tool_call`'s own transcript evidence; when neither is present it resolves at the `path`
    tier from cwd, same as the original baseline."""
    cwd = git_branch = remote = None
    found_session_start = False
    for event in events:
        if event["event_type"] == "session_start":
            found_session_start = True
            cwd = event["payload"].get("cwd")
            git_branch = event["payload"].get("git_branch")
            # A real `git remote get-url origin`, resolved by the uploader client at this
            # session's actual working directory and carried on the upload manifest's own
            # file entry (collector/engine.py backfills it from there, no separate cwd-keyed
            # lookup) -- preferred over anything merely scraped from a transcript's own tool
            # calls below.
            remote = event["payload"].get("git_remote")
            break
    if not found_session_start:
        return None

    pr_repository = None
    for event in events:
        etype = event["event_type"]
        if etype == "pr_link" and pr_repository is None:
            pr_repository = event["payload"].get("pr_repository")
        elif etype == "tool_call" and remote is None:
            remote = event["payload"].get("git_remote")

    repo_key = git_ident.path_repo_key(cwd)
    pr_repo_id = git_ident.normalize_remote(pr_repository) if pr_repository else None
    remote_repo_id = git_ident.normalize_remote(remote) if remote else None
    if pr_repo_id is not None:
        repo_id, provenance = pr_repo_id, "pr_link"
    elif remote_repo_id is not None:
        repo_id, provenance = remote_repo_id, "remote"
    elif repo_key is not None:
        repo_id, provenance = f"local:{repo_key}", "path"
    else:
        repo_id, provenance = None, None

    primary_refs = 0
    secondary_refs: dict[str, int] = {}
    stripped_cwd = git_ident.strip_worktree_suffix(cwd).rstrip("/") if cwd else None
    if stripped_cwd:
        for event in events:
            if event["event_type"] != "tool_call":
                continue
            for f in event["payload"].get("files") or []:
                path = f.get("path")
                if not path:
                    continue
                if path == stripped_cwd or path.startswith(stripped_cwd + "/"):
                    primary_refs += 1
                    continue
                sibling = git_ident.sibling_project_root(stripped_cwd, path)
                if sibling is not None:
                    secondary_refs[sibling] = secondary_refs.get(sibling, 0) + 1

    repos = []
    if repo_id is not None:
        repos.append({"repo_id": repo_id, "repo_id_provenance": provenance,
                      "repo_key": repo_key, "refs": primary_refs, "primary": True})
    for root, refs in sorted(secondary_refs.items(), key=lambda kv: (-kv[1], kv[0])):
        key = root.rsplit("/", 1)[-1]
        repos.append({"repo_id": f"local:{key}", "repo_id_provenance": "path",
                      "repo_key": key, "refs": refs, "primary": False})

    return {"cwd": cwd, "git_branch": git_branch, "repo_key": repo_key,
            "repo_id": repo_id, "repo_id_provenance": provenance, "repos": repos}


def _usage_to_bucket(usage: dict[str, int]) -> dict[str, int]:
    """budget usage envelope -> EconFold's per-model bucket names."""
    return {
        "input": usage.get("input_tokens", 0),
        "output": usage.get("output_tokens", 0),
        "cache_read": usage.get("cache_read_tokens", 0),
        "cache_write": usage.get("cache_write_tokens", 0),
    }


def models_of(family: Family, scope: str = "self") -> dict[str, dict[str, int]]:
    """{model: {input, output, cache_read, cache_write}} from budget(by=model).

    A null model maps to 'unknown', matching EconFold's `payload.get("model") or "unknown"`.
    Token *values* differ from EconFold by the §4.A collapse (allowlisted): the graph takes usage
    from the first_block_of_message record instead of summing every per-record assistant event.
    """
    report = budget(family, scope=scope, by="model")
    out: dict[str, dict[str, int]] = {}
    for row in report["rows"]:
        model = row["key"].get("model") or "unknown"
        bucket = out.setdefault(model, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
        for k, v in _usage_to_bucket(row["usage"]).items():
            bucket[k] += v
    return out


def tokens_total_of(models: dict[str, dict[str, int]]) -> int:
    """Sum of every model bucket -- EconFold's `metrics.tokens_total(models)`."""
    return sum(sum(bucket.values()) for bucket in models.values())


def _self_graph(family: Family, session_id: str):
    return family.resolve(session_id)


def tools_of(family: Family, session_id: str) -> dict[str, dict[str, Any]]:
    """{tool_name: {count, type, ok, failed, mcp_server?, bytes_read_total, duration_ms_total}}
    -- EconFold's `metrics.tool_totals`, rebuilt from Tool nodes.

    outcome (ok/failed) uses the node's derived outcome; bytes_read/duration are summed only when
    present, exactly as tool_totals did.
    """
    graph = _self_graph(family, session_id)
    tools: dict[str, dict[str, Any]] = {}
    if graph is None:
        return tools
    for node in graph.nodes.values():
        if node.type is not NodeType.TOOL:
            continue
        name = node.attrs.get("tool_name") or "unknown"
        mcp_server = node.attrs.get("mcp_server")
        bucket = tools.setdefault(
            name,
            {"count": 0, "type": "mcp" if mcp_server else "builtin", "ok": 0, "failed": 0,
             "bytes_read_total": 0, "duration_ms_total": 0},
        )
        bucket["count"] += 1
        if mcp_server:
            bucket["mcp_server"] = mcp_server
        is_error = node.attrs.get("is_error")
        if is_error:
            bucket["failed"] += 1
        else:
            bucket["ok"] += 1
        bytes_read = node.attrs.get("bytes_read")
        if bytes_read:
            bucket["bytes_read_total"] += bytes_read
        duration_ms = node.attrs.get("duration_ms")
        if duration_ms:
            bucket["duration_ms_total"] += duration_ms
    return tools


def files_of(family: Family, session_id: str) -> dict[str, dict[str, Any]]:
    """{path: {changes, lines_added, lines_removed, extension, created, first_seen, last_seen}}
    -- EconFold's `metrics.file_totals`, rebuilt from the file entries on Tool nodes.

    Counts *changes* (create/edit/delete), never reads -- matching file_totals, which folded only
    file_change events. Chronological by tool ts for created/first_seen/last_seen.

    lines_added/lines_removed are the exact +/- counts the normalizer now carries on each file entry
    (computed from structuredPatch before the diff body is dropped; the graph's hunk sizes include
    context and cannot reproduce them).
    """
    graph = _self_graph(family, session_id)
    files: dict[str, dict[str, Any]] = {}
    if graph is None:
        return files
    tools = [n for n in graph.nodes.values() if n.type is NodeType.TOOL]
    tools.sort(key=lambda n: (n.ts or ""))
    for node in tools:
        for f in node.attrs.get("files") or []:
            path = f.get("path")
            op = f.get("operation")
            if not path or op == "read":
                continue
            ts = node.ts
            entry = files.setdefault(path, {
                "changes": 0, "lines_added": 0, "lines_removed": 0,
                "extension": f.get("extension"),
                "created": op == "create",
                "first_seen": ts, "last_seen": ts,
            })
            entry["changes"] += 1
            entry["lines_added"] += f.get("lines_added") or 0
            entry["lines_removed"] += f.get("lines_removed") or 0
            if ts:
                entry["last_seen"] = ts
    return files


def compactions_of(family: Family, session_id: str) -> list[dict[str, Any]]:
    """info.compactions -- EconFold's `metrics.compaction_rollup`, from Compaction nodes."""
    graph = _self_graph(family, session_id)
    out: list[dict[str, Any]] = []
    if graph is None:
        return out
    nodes = [n for n in graph.nodes.values() if n.type is NodeType.COMPACTION]
    nodes.sort(key=lambda n: (n.ts or ""))
    for n in nodes:
        out.append({
            "ts": n.ts,
            "trigger": n.attrs.get("trigger"),
            "before": n.attrs.get("tokens_before"),
            "after": n.attrs.get("tokens_after"),
            "reclaimed": n.attrs.get("tokens_reclaimed"),
            "duration_ms": n.attrs.get("duration_ms"),
        })
    return out


def spawn_count_of(family: Family, session_id: str) -> int:
    graph = _self_graph(family, session_id)
    if graph is None:
        return 0
    return sum(1 for n in graph.nodes.values() if n.type is NodeType.SPAWN)


def _descendants(graph, root):
    found, pending, seen = [], list(reversed(root.children)), set()
    while pending:
        nid = pending.pop()
        if nid in seen:
            continue
        seen.add(nid)
        node = graph.nodes.get(nid)
        if node is None:
            continue
        found.append(node)
        pending.extend(reversed(node.children))
    return found


_ASSISTANT_USAGE = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
_LONG_TURN_THRESHOLD_S = 1800
# §4.B heuristics. INTERRUPT_SENTINEL is the exact user-message text EconFold keyed on (the
# "for tool use" variant is deliberately NOT an interrupt). The read-then-discard window is
# config/worker.yaml's fold.waste.read_then_discard_window_s default (hardcoded here like the
# long-turn threshold above; the §4.B port reuses the fold thresholds verbatim).
INTERRUPT_SENTINEL = "[Request interrupted by user]"
_READ_THEN_DISCARD_WINDOW_S = 600.0


def _parse_iso(v):
    from datetime import datetime

    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def turns_of(family: Family, session_id: str, threshold_s: int = _LONG_TURN_THRESHOLD_S) -> list[dict[str, Any]]:
    """Per-turn records -- EconFold's `turns.compute_turns`, rebuilt from Turn nodes + descendants.

    Turn boundaries differ from EconFold: the graph segments by promptId runs, EconFold by text-based
    human-message detection (ledger #7). Several per-turn fields are also semantic/collapse diffs
    (tokens = §4.A collapse; message_count/ended/source have graph-native definitions) -- allowlisted.
    """
    graph = _self_graph(family, session_id)
    if graph is None:
        return []
    turn_nodes = sorted(
        (n for n in graph.nodes.values() if n.type is NodeType.TURN),
        key=lambda n: n.attrs.get("turn_num", 0),
    )
    out: list[dict[str, Any]] = []
    for tn in turn_nodes:
        desc = _descendants(graph, tn)
        tokens = 0
        spawn_ids: list[str] = []
        files_changed = 0
        last_ts = tn.ts
        for d in desc:
            if d.ts and (last_ts is None or d.ts > last_ts):
                last_ts = d.ts
            if d.type is NodeType.ASSISTANT:
                tokens += sum(d.attrs.get(f) or 0 for f in _ASSISTANT_USAGE)
            elif d.type is NodeType.SPAWN:
                child = d.refs.get("child_session_id")
                if isinstance(child, str):
                    spawn_ids.append(child)
            elif d.type is NodeType.TOOL:
                files_changed += sum(
                    1 for f in (d.attrs.get("files") or [])
                    if f.get("path") and f.get("operation") != "read"
                )
        duration_ms = tn.attrs.get("duration_ms")
        status = tn.attrs.get("status")
        out.append({
            "index": tn.attrs.get("turn_num"),
            "started": tn.ts,
            "ended": last_ts,
            "duration_ms": duration_ms,
            "message_count": sum(1 for d in desc if d.type is not NodeType.USER_PROMPT),
            "source": "turn_duration" if status == "completed" else "fallback",
            "tokens": tokens,
            "subagents_spawned": len(spawn_ids),
            "subagent_session_ids": spawn_ids,
            "files_changed": files_changed,
            "long": bool(duration_ms is not None and duration_ms > threshold_s * 1000),
        })
    return out


def context_curve_of(family: Family, session_id: str) -> list[dict[str, Any]]:
    """info.context_curve (D1) -- a per-turn context-window-size series for SessionScreen's
    "Context over the session" chart. A turn's value is its END-of-turn per-request context
    (`cache_read_tokens + cache_write_tokens` of the turn's last assistant request), which is the
    fold's own definition of per-request context (`carry_of`) and what actually rides the model's
    context window. `carry_of` already computes this per assistant node but keeps only the last
    point; this keeps the whole series. `compaction` flags a turn during which a compaction node
    fired (the context drops right after) so the chart can mark it.

    Built from the same Turn-node + descendant walk as `turns_of`, kept as its own series rather
    than collapsed into segment boundaries. Turns with no assistant request (tool-only) contribute
    no point. Root-session-only, gated by the caller like `segments`/`turns`."""
    graph = _self_graph(family, session_id)
    if graph is None:
        return []
    turn_nodes = sorted(
        (n for n in graph.nodes.values() if n.type is NodeType.TURN),
        key=lambda n: n.attrs.get("turn_num", 0),
    )
    # ts are ISO-8601 UTC strings; lexical compare is the existing pattern here (see carry_of).
    compaction_ts = [c["ts"] for c in compactions_of(family, session_id) if c.get("ts")]
    out: list[dict[str, Any]] = []
    for tn in turn_nodes:
        assistants = sorted(
            (d for d in _descendants(graph, tn) if d.type is NodeType.ASSISTANT and d.ts),
            key=lambda n: n.ts,
        )
        if not assistants:
            continue
        last = assistants[-1]
        ctx = (last.attrs.get("cache_read_tokens") or 0) + (last.attrs.get("cache_write_tokens") or 0)
        had_compaction = any(
            tn.ts is not None and last.ts is not None and tn.ts <= c <= last.ts
            for c in compaction_ts
        )
        out.append({
            "index": tn.attrs.get("turn_num"),
            "ts": last.ts,
            "context_tokens": ctx,
            "compaction": had_compaction,
        })
    return out


def _dominant_model(models: dict[str, dict[str, int]]) -> str | None:
    if not models:
        return None
    return max(models, key=lambda m: sum(models[m].values()))


def _session_duration_ms(graph) -> int | None:
    from datetime import datetime

    ts = [n.ts for n in graph.nodes.values() if n.ts]
    if len(ts) < 2:
        return None
    def parse(v):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    parsed = [p for p in (parse(t) for t in ts) if p is not None]
    if len(parsed) < 2:
        return None
    return int((max(parsed) - min(parsed)).total_seconds() * 1000)


def _inclusive_tokens(family: Family, session_id: str, children_of: dict[str, list[str]]) -> int:
    own = tokens_total_of(_models_for(family, session_id))
    return own + sum(_inclusive_tokens(family, c, children_of) for c in children_of.get(session_id, []))


def _models_for(family: Family, session_id: str) -> dict[str, dict[str, int]]:
    report = budget(family, scope={"kind": "session", "session_id": session_id}, by="model")
    out: dict[str, dict[str, int]] = {}
    for row in report["rows"]:
        model = row["key"].get("model") or "unknown"
        b = out.setdefault(model, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
        for k, v in _usage_to_bucket(row["usage"]).items():
            b[k] += v
    return out


def subagents_of(family: Family, session_id: str) -> dict[str, dict[str, Any]]:
    """{child_session_id: {tokens, inclusive_tokens, duration_ms, purpose, agent_type, origin,
    depth, model, tools, files}} -- EconFold's `rollup_subagents` subagents map, for `session_id`'s
    DIRECT children (per-parent, so nested rollup is correct). Empty when its children aren't loaded
    (a plain self-fold) -- they fill at rollup over the family GraphService loads."""
    graph = family.resolve(session_id)
    if graph is None:
        return {}
    origin = "human" if graph.parent_session_id is None else "subagent"
    # parent -> direct children, for inclusive-token recursion over the whole family.
    children_of: dict[str, list[str]] = {}
    for sid, child in family.children.items():
        children_of.setdefault(child.parent_session_id, []).append(sid)
    # THIS session's Spawn nodes carry agent_type/description/child_session_id.
    spawns: dict[str, dict] = {}
    for node in graph.nodes.values():
        if node.type is NodeType.SPAWN:
            cid = node.refs.get("child_session_id")
            if isinstance(cid, str):
                spawns[cid] = {
                    "agent_type": node.attrs.get("agent_type"),
                    "description": node.attrs.get("description"),
                }
    direct = {sid: child for sid, child in family.children.items() if child.parent_session_id == session_id}
    out: dict[str, dict[str, Any]] = {}
    for cid, child in direct.items():
        models = _models_for(family, cid)
        spawn = spawns.get(cid, {})
        out[cid] = {
            "tokens": tokens_total_of(models),
            "inclusive_tokens": _inclusive_tokens(family, cid, children_of),
            "duration_ms": _session_duration_ms(child),
            "purpose": spawn.get("description") or spawn.get("agent_type"),
            "agent_type": spawn.get("agent_type"),
            "origin": origin,
            "depth": 1,
            "model": _dominant_model(models),
            "tools": tools_of(family, cid),
            "files": files_of(family, cid),
        }
    return out


_ARGS_GIST_CAP = 80


def skills_of(family: Family, session_id: str) -> list[dict[str, Any]]:
    """[{skill_name, count, caller_type, success, allowed_tools, tokens, started, ended, args_gist}]
    -- EconFold's `metrics.skill_reports`, from Skill Tool nodes.

    tokens are the calling assistant's -- in the graph that's the Skill tool node's PARENT assistant
    (cleaner than EconFold's most-recent-assistant adjacency; the value is the §4.A collapse, so it
    differs from the baseline and is allowlisted). caller_type is null on the graph (not carried) --
    matches the baseline, which is null for every observed skill. allowed_tools stays [] as EconFold's did.
    """
    graph = _self_graph(family, session_id)
    by_name: dict[str, dict[str, Any]] = {}
    if graph is None:
        return []
    skill_nodes = sorted(
        (n for n in graph.nodes.values()
         if n.type is NodeType.TOOL and n.attrs.get("tool_name") == "Skill"),
        key=lambda n: (n.ts or ""),
    )
    for node in skill_nodes:
        tool_input = node.attrs.get("input") or {}
        name = tool_input.get("skill") or "unknown"
        parent = graph.nodes.get(node.parent_id) if node.parent_id else None
        parent_tokens = sum(parent.attrs.get(f) or 0 for f in _ASSISTANT_USAGE) if parent and parent.type is NodeType.ASSISTANT else 0
        ts = node.ts
        args = tool_input.get("args")
        entry = by_name.setdefault(name, {
            "skill_name": name, "count": 0, "caller_type": None, "success": True,
            "allowed_tools": [], "tokens": 0, "started": ts, "ended": ts,
            "args_gist": args[:_ARGS_GIST_CAP] if isinstance(args, str) else None,
        })
        entry["count"] += 1
        entry["tokens"] += parent_tokens
        exit_status = node.attrs.get("exit_status")
        if exit_status not in (0, None):
            entry["success"] = False
        if ts and (entry["started"] is None or ts < entry["started"]):
            entry["started"] = ts
        if ts and (entry["ended"] is None or ts > entry["ended"]):
            entry["ended"] = ts
    return list(by_name.values())


def mcp_servers_of(tools: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """info.mcp_servers -- EconFold's `metrics.mcp_server_rollup`, from the tools tally."""
    by_server: dict[str, dict[str, int]] = {}
    for info in tools.values():
        server = info.get("mcp_server")
        if not server:
            continue
        b = by_server.setdefault(server, {"calls": 0, "bytes_read_total": 0})
        b["calls"] += info.get("count", 0)
        b["bytes_read_total"] += info.get("bytes_read_total", 0)
    return {"attached": sorted(by_server), "by_server": by_server}


_GIT_SUBCOMMANDS = ["commit", "checkout", "switch", "branch", "merge", "push", "pull", "fetch"]
_GIT_RE = re.compile(r"\bgit\s+(?:\S+\s+){0,3}?(?P<sub>" + "|".join(_GIT_SUBCOMMANDS) + r")\b")


def git_commands_of(family: Family, session_id: str) -> dict[str, int]:
    """info.git_commands -- EconFold's `metrics.git_command_counts`, over Bash Tool nodes.
    checkout/switch collapse into one 'checkout' bucket."""
    graph = _self_graph(family, session_id)
    counts: dict[str, int] = {}
    if graph is None:
        return counts
    for node in graph.nodes.values():
        if node.type is not NodeType.TOOL or node.attrs.get("tool_name") != "Bash":
            continue
        command = (node.attrs.get("input") or {}).get("command")
        if not isinstance(command, str):
            continue
        for m in _GIT_RE.finditer(command):
            sub = m.group("sub")
            bucket = "checkout" if sub == "switch" else sub
            counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def _price_channel(model_tokens: dict[str, dict[str, int]], kind: str,
                   pricing: Pricer | None, at: datetime | None) -> int | None:
    """Sum one channel's dollar cost across every model that contributed tokens to it, priced
    at `at` -- the session's own date, not "today". `price_tokens` looks up whichever rate was
    in effect on that date (the catalog's dated rows), so this is correct forever regardless
    of when it's computed: a real rate change tomorrow only ever prices sessions from tomorrow,
    never retroactively repriced or made stale by running this again later.

    `None` -- never a partial sum -- the instant any contributing model can't be priced for
    this kind/date (`price_tokens`'s own "a partial total looks like an answer" rule, one level
    up: a channel that's half-priced is a worse answer than an honestly missing one). Also
    `None` outright when no pricing table was passed at all -- every call site and test fixture
    that predates this."""
    if pricing is None or at is None:
        return None
    total = Decimal(0)
    for model, bucket in model_tokens.items():
        count = bucket.get(kind, 0)
        if not count:
            continue
        price = pricing.price_tokens(model, {kind: count}, at)
        if price is None:
            return None
        total += price
    return price_to_micros(total)


def channel_cost_of(models: dict[str, dict[str, int]], pricing: Pricer | None = None,
                    at: datetime | None = None) -> dict[str, Any]:
    """info.channel_cost -- token counts per billing channel, each priced per model at `at`
    (the session's own date) when a pricing table is given -- `None` otherwise, matching the
    original baseline this was built without pricing at all. recache == cache writes."""
    return {
        "input": {"tokens": sum(m["input"] for m in models.values()),
                  "usd_micros": _price_channel(models, "input", pricing, at)},
        "output": {"tokens": sum(m["output"] for m in models.values()),
                   "usd_micros": _price_channel(models, "output", pricing, at)},
        "recache": {"tokens": sum(m["cache_write"] for m in models.values()),
                    "usd_micros": _price_channel(models, "cache_write", pricing, at)},
    }


def interrupted_of(events: list[dict], pricing: Pricer | None = None,
                   at: datetime | None = None) -> dict[str, Any]:
    """info.interrupted -- EconFold's `beats.interrupted_cost_rollup`: {events:[{ts,tokens}], count,
    total_tokens}. When a user interrupts (the exact sentinel), the assistant message right before it
    was billed in full for a reply they threw away; `tokens` is that message's own usage total.

    Read from the event stream, not the graph -- the interrupt sentinel is a raw user-message signal
    the builder doesn't preserve as a node (the exact-sentinel UserPrompt is dropped, and turn
    `status="interrupted"` keys on non-completion, a different and broader signal). Same targeted
    event-read pattern as `pr_list_of`/`repo_info`.

    `usd_micros` prices that discarded response's own usage at its own model, at the session's `at`
    (efficiency note §2, bucket 3 -- the whole response is recoverable); `total_usd_micros` is
    all-or-nothing, `None` without a pricing table. The previous record's usage is response-scoped
    (`usage_scope: "response"`), so one record is the whole response -- no per-record over-count."""
    out = []
    prev_assistant = None
    for e in events:
        etype = e.get("event_type")
        if etype == "assistant_message":
            prev_assistant = e
            continue
        if etype != "user_message":
            continue
        text = (e.get("payload") or {}).get("text")
        if not (isinstance(text, str) and text.strip() == INTERRUPT_SENTINEL):
            continue
        if prev_assistant is None:
            continue
        payload = prev_assistant.get("payload") or {}
        usd_micros = None
        tokens = {"input": payload.get("input_tokens") or 0, "output": payload.get("output_tokens") or 0,
                  "cache_read": payload.get("cache_read_tokens") or 0,
                  "cache_write": payload.get("cache_write_tokens") or 0}
        if pricing is not None and at is not None:
            if not any(tokens.values()):
                usd_micros = 0  # nothing billed (e.g. a zero-usage `<synthetic>` record, whose model has no price)
            else:
                price = pricing.price_tokens(payload.get("model"), tokens, at,
                                             cache_write_detail=payload.get("cache_write_detail"))
                usd_micros = price_to_micros(price) if price is not None else None
        out.append({"ts": e.get("event_ts"), "tokens": sum(payload.get(f) or 0 for f in _ASSISTANT_USAGE),
                    "usd_micros": usd_micros})
    return {"events": out, "count": len(out), "total_tokens": sum(e["tokens"] for e in out),
            "total_usd_micros": _all_or_nothing_sum(out, "usd_micros", pricing, at)}


def carry_of(family: Family, session_id: str, pricing: Pricer | None = None,
            at: datetime | None = None) -> dict[str, Any]:
    """info.carry -- EconFold's `metrics.carry_rollup`, from assistant nodes (one per request, since
    the graph collapses per-record assistant messages by message_id). Per-request context size =
    cache_read + cache_write; `peak_context`/`mean_context` are its max/mean, `requests` the node
    count, `cache_read_tokens` the sum. The collapse makes `requests`/`cache_read_tokens`/
    `mean_context` the TRUE per-request figures (the per-record fold over-counted) -- an intended
    §4.A value diff (allowlisted).

    `carry_usd_micros` prices the cache-read total per model (`_price_channel`, same as
    `channel_cost_of`) -- `None` when no pricing table is given, same posture as before this."""
    graph = _self_graph(family, session_id)
    contexts: list[int] = []
    cache_read_total = 0
    cache_read_by_model: dict[str, dict[str, int]] = {}
    if graph is not None:
        for n in graph.nodes.values():
            if n.type is not NodeType.ASSISTANT:
                continue
            cache_read = n.attrs.get("cache_read_tokens") or 0
            cache_write = n.attrs.get("cache_write_tokens") or 0
            contexts.append(cache_read + cache_write)
            cache_read_total += cache_read
            model = n.attrs.get("model") or "unknown"
            cache_read_by_model.setdefault(model, {"cache_read": 0})["cache_read"] += cache_read
    requests = len(contexts)
    return {
        "cache_read_tokens": cache_read_total,
        "carry_usd_micros": _price_channel(cache_read_by_model, "cache_read", pricing, at),
        "peak_context": max(contexts) if contexts else 0,
        "mean_context": round(sum(contexts) / requests) if requests else 0,
        "requests": requests,
    }


def reasoning_tokens_of(family: Family, session_id: str) -> int:
    """info.reasoning_tokens (F5) -- the session total of each assistant node's own
    `estimated_reasoning_tokens` (`analysis/causal_graph/reasoning.py::estimate_reasoning`,
    already attached per-node at build time by `builder._collapse_assistants`; this is a pure
    summation, no new extraction). A node with no source at all (`reasoning_source is None` --
    `output_tokens` missing upstream) contributes 0, same "missing counts as nothing, not a
    crash" posture `carry_of`/`cache_of` already take on their own per-node fields."""
    graph = _self_graph(family, session_id)
    if graph is None:
        return 0
    return sum(
        n.attrs.get("estimated_reasoning_tokens") or 0
        for n in graph.nodes.values() if n.type is NodeType.ASSISTANT
    )


def cache_of(family: Family, session_id: str) -> dict[str, Any]:
    """info.cache -- only `hit_ratio` is ported (EconFold's `metrics.cache_rollup`):
    cache_read / (cache_read + input) over assistant nodes. A §4.A collapse value diff vs the
    per-record baseline (allowlisted). The TTL split (`writes_5m`/`writes_1h`) and `decay_events`
    need per-write `cache_write_detail` + a TTL/decay model the graph doesn't carry, and are decided
    drops (the write TOTAL already lives in `channel_cost.recache`); the A/B allowlists their absence."""
    graph = _self_graph(family, session_id)
    total_cache_read = total_input = 0
    if graph is not None:
        for n in graph.nodes.values():
            if n.type is not NodeType.ASSISTANT:
                continue
            total_cache_read += n.attrs.get("cache_read_tokens") or 0
            total_input += n.attrs.get("input_tokens") or 0
    denom = total_cache_read + total_input
    return {"hit_ratio": round(total_cache_read / denom, 4) if denom else 0.0}


def read_then_discard_of(
    family: Family, session_id: str, window_s: float = _READ_THEN_DISCARD_WINDOW_S
) -> dict[str, Any]:
    """info.read_then_discard -- EconFold's `metrics.read_then_discard_rollup`, from Compaction nodes +
    Tool `bytes_read`: a tool read within `window_s` before a compaction is paid to load, then thrown
    away when the compaction summarizes that context. `est_tokens = bytes // 4` (the fold's estimator).
    `bytes_read` is carried on tool nodes (ledger #4), so this is a faithful graph port."""
    graph = _self_graph(family, session_id)
    out = []
    if graph is not None:
        compactions = [n.ts for n in graph.nodes.values() if n.type is NodeType.COMPACTION and n.ts]
        reads = [
            (n.ts, n.attrs.get("bytes_read")) for n in graph.nodes.values()
            if n.type is NodeType.TOOL and n.attrs.get("bytes_read") and n.ts
        ]
        for c_ts in compactions:
            c_dt = _parse_iso(c_ts)
            if c_dt is None:
                continue
            bytes_read = 0
            for r_ts, size in reads:
                r_dt = _parse_iso(r_ts)
                if r_dt is None or r_dt > c_dt:
                    continue
                if (c_dt - r_dt).total_seconds() <= window_s:
                    bytes_read += size
            if bytes_read > 0:
                out.append({"compaction_ts": c_ts, "bytes_read": bytes_read, "est_tokens": bytes_read // 4})
    total_bytes = sum(e["bytes_read"] for e in out)
    return {"events": out, "total_bytes_read": total_bytes, "total_est_tokens": total_bytes // 4}


def _all_or_nothing_sum(events: list[dict], key: str, pricing: Pricer | None,
                       at: datetime | None) -> int | None:
    """A priced total over `events[key]`: `None` with no pricing table, or the moment any one event
    is unpriced -- never a partial sum (the `_price_channel` posture)."""
    if pricing is None or at is None:
        return None
    if any(e[key] is None for e in events):
        return None
    return sum(e[key] for e in events)


def _rewrite_recoverable_usd_micros(model: str, rewrite_tokens: int, context_before: int, cache_read_tokens: int,
                                    pricing: Pricer | None, at: datetime | None) -> int | None:
    """The recoverable share of a cache re-write (efficiency note §2, buckets 1-2): only context that was
    already cached and is being paid for again counts -- `min(rewrite, context_before - what this request
    still read)`; any excess is the request's own new context, written either way. Priced as the difference
    between writing and reading those tokens, since the do-it-well counterfactual still reads them."""
    if pricing is None or at is None:
        return None
    rewritten = min(rewrite_tokens, max(0, context_before - cache_read_tokens))
    write = pricing.price_tokens(model, {"cache_write": rewritten}, at)
    read = pricing.price_tokens(model, {"cache_read": rewritten}, at)
    if write is None or read is None:
        return None
    return price_to_micros(max(Decimal(0), write - read))


_ASSUMED_CACHE_TTL_S = 3600  # CC v1 writes 1h caches; the graph doesn't carry per-write cache_write_detail
                             # (writes_5m == 0 across the corpus), so resume_cost assumes the 1h tier --
                             # the same accepted TTL-drop as info.cache.writes_5m / decay_events.


def resume_cost_of(family: Family, session_id: str, pricing: Pricer | None = None,
                   at: datetime | None = None) -> dict[str, Any]:
    """info.resume_cost -- EconFold's `metrics.resume_reload_rollup`: a turn gap that exceeds the last
    cache write's TTL, where the next turn re-caches at least half of what was live before the gap, is a
    paid session reload. {events:[{turn,gap_s,ttl_s,context_before,reload_tokens,usd_micros}], count,
    total_usd_micros, projected}.

    Ported from the graph's assistant nodes (per-request context = cache_read+cache_write) + turn nodes
    (gap timing). `count`, `gap_s`, `context_before`, `ttl_s`, and `projected` reproduce the baseline
    exactly; two fields carry already-accepted differences: `turn` follows the graph-native turn index
    (ledger #7) and `reload_tokens` is the §4.A collapse (the per-record fold over-counted). TTL is
    assumed 1h (`_ASSUMED_CACHE_TTL_S`).

    `reload_tokens` are real cache-write tokens re-paid on a real resuming assistant node -- pricing
    them at that node's own model, at the session's `at` (same convention as `channel_cost_of`/
    `carry_of`), is faithful, not an estimate (ground rule 3), so `usd_micros`/`total_usd_micros`/
    `projected.usd_micros` are priced via `_price_channel` when a table is given, `None` (never a
    guessed 0) otherwise -- same posture as every other GraphFold money field. A reload window that
    spans more than one write is priced per contributing model (`_price_channel`'s own all-or-nothing
    grouping), not against a single node's rate, in the rare case a model switch lands inside one gap.
    `projected` prices the *last* assistant node's full context as a hypothetical cache_write, since
    that is what a reload right now would have to re-pay.

    The reload is the resuming turn's first request only (`ts` is its timestamp). `usd_micros` is
    what that re-write cost; `recoverable_usd_micros` is the recoverable-spend share (efficiency
    note §2, bucket 1): the already-cached context it re-wrote, priced as cache-write minus the
    cache-read that request would have paid had the cache still been warm -- the counterfactual
    still re-reads the context, so the full write price is not all recoverable."""
    graph = _self_graph(family, session_id)
    if graph is None:
        return {"events": [], "count": 0, "total_usd_micros": None,
                "total_recoverable_usd_micros": None, "projected": None}
    assistants = sorted(
        (n for n in graph.nodes.values() if n.type is NodeType.ASSISTANT and n.ts), key=lambda n: n.ts
    )
    contexts = [(n.ts, (n.attrs.get("cache_read_tokens") or 0) + (n.attrs.get("cache_write_tokens") or 0),
                n.attrs.get("model")) for n in assistants]
    writes = [(n.ts, n.attrs.get("model"), n.attrs.get("cache_write_tokens") or 0) for n in assistants
              if (n.attrs.get("cache_write_tokens") or 0) > 0]
    turns = turns_of(family, session_id)

    reload_events = []
    for prev_turn, next_turn in zip(turns, turns[1:]):
        prev_end = _parse_iso(prev_turn.get("ended"))
        next_start = _parse_iso(next_turn.get("started"))
        if prev_end is None or next_start is None:
            continue
        gap_s = (next_start - prev_end).total_seconds()
        if gap_s <= 0:
            continue
        prev_end_str = prev_turn.get("ended")
        if not any(w[0] <= prev_end_str for w in writes):  # a cache write must precede the gap
            continue
        if gap_s <= _ASSUMED_CACHE_TTL_S:
            continue
        context_before = 0
        for ts, ctx, _model in reversed(contexts):
            if ts <= prev_end_str:
                context_before = ctx
                break
        if context_before <= 0:
            continue
        next_end_str = next_turn.get("ended") or next_turn.get("started")
        # The reload is the next turn's FIRST request -- the one that finds the cache expired and
        # re-writes it. Later writes in that turn are ordinary growth (the turn's own new context),
        # which summing every write in the window used to bill as reload.
        reload_node = next((n for n in assistants if prev_end_str < n.ts <= next_end_str), None)
        if reload_node is None:
            continue
        reload_tokens = reload_node.attrs.get("cache_write_tokens") or 0
        if reload_tokens < context_before / 2:  # a real reload, not an incidental small write
            continue
        model = reload_node.attrs.get("model") or "unknown"
        usd_micros = _price_channel({model: {"cache_write": reload_tokens}}, "cache_write", pricing, at)
        reload_events.append({
            "turn": next_turn.get("index"), "ts": reload_node.ts, "gap_s": int(gap_s),
            "ttl_s": _ASSUMED_CACHE_TTL_S, "context_before": context_before,
            "reload_tokens": reload_tokens, "usd_micros": usd_micros,
            "recoverable_usd_micros": _rewrite_recoverable_usd_micros(
                model, reload_tokens, context_before, reload_node.attrs.get("cache_read_tokens") or 0,
                pricing, at),
        })

    total_usd_micros = _all_or_nothing_sum(reload_events, "usd_micros", pricing, at)
    total_recoverable_usd_micros = _all_or_nothing_sum(reload_events, "recoverable_usd_micros", pricing, at)

    projected = None
    if contexts and writes:
        ctx_tokens, model = contexts[-1][1], contexts[-1][2]
        projected = {
            "context_tokens": ctx_tokens, "ttl_s": _ASSUMED_CACHE_TTL_S,
            "usd_micros": _price_channel({model: {"cache_write": ctx_tokens}}, "cache_write", pricing, at),
        }
    return {"events": reload_events, "count": len(reload_events),
            "total_usd_micros": total_usd_micros,
            "total_recoverable_usd_micros": total_recoverable_usd_micros, "projected": projected}

#: Cache-bust detection (efficiency note §2, bucket 2). A request counts as a bust when the context
#: live on the previous request was at least `_CACHE_BUST_MIN_CONTEXT` tokens and this request
#: re-wrote at least `_CACHE_BUST_MIN_REWRITE_SHARE` of it -- while the cache should still have been
#: warm (gap within `_ASSUMED_CACHE_TTL_S`), on the same model, with no compaction in between.
_CACHE_BUST_MIN_CONTEXT = 20_000
_CACHE_BUST_MIN_REWRITE_SHARE = 0.5


def cache_busts_of(family: Family, session_id: str, pricing: Pricer | None = None,
                   at: datetime | None = None) -> dict[str, Any]:
    """info.cache_busts -- {events:[{ts, gap_s, model, context_before, rewrite_tokens, usd_micros}],
    count, total_usd_micros}: a request that re-wrote most of an already-cached context while that
    cache should still have been valid -- the prefix was invalidated mid-session (a CLAUDE.md edit, an
    MCP server or tool-set change, ...; the graph can see *that* it happened, not *why*).

    Disjoint from `resume_cost_of` by construction: that one fires only past the TTL, this one only
    within it. A model switch (caches are per model) and a compaction (context is deliberately
    rebuilt) are excluded -- both are explained re-writes. The TTL is the same assumed 1h tier as
    `resume_cost_of`; a 5-minute-tier write expiring inside that hour reads as a bust (accepted --
    the graph carries no per-write TTL detail).

    `usd_micros` is the recoverable share only (`_rewrite_recoverable_usd_micros`), `None` without
    a pricing table; `total_usd_micros` is all-or-nothing."""
    graph = _self_graph(family, session_id)
    if graph is None:
        return {"events": [], "count": 0, "total_usd_micros": None}
    assistants = sorted(
        (n for n in graph.nodes.values() if n.type is NodeType.ASSISTANT and n.ts), key=lambda n: n.ts
    )
    compaction_ts = sorted(n.ts for n in graph.nodes.values() if n.type is NodeType.COMPACTION and n.ts)
    events: list[dict[str, Any]] = []
    for prev, cur in zip(assistants, assistants[1:]):
        context_before = (prev.attrs.get("cache_read_tokens") or 0) + (prev.attrs.get("cache_write_tokens") or 0)
        if context_before < _CACHE_BUST_MIN_CONTEXT:
            continue
        rewrite_tokens = cur.attrs.get("cache_write_tokens") or 0
        if rewrite_tokens < context_before * _CACHE_BUST_MIN_REWRITE_SHARE:
            continue
        prev_dt, cur_dt = _parse_iso(prev.ts), _parse_iso(cur.ts)
        if prev_dt is None or cur_dt is None:
            continue
        gap_s = (cur_dt - prev_dt).total_seconds()
        if gap_s > _ASSUMED_CACHE_TTL_S:
            continue  # expired, not busted -- resume_cost_of's
        model = cur.attrs.get("model") or "unknown"
        if model != (prev.attrs.get("model") or "unknown"):
            continue
        if any(prev.ts <= c_ts <= cur.ts for c_ts in compaction_ts):
            continue
        events.append({
            "ts": cur.ts, "gap_s": int(gap_s), "model": model, "context_before": context_before,
            "rewrite_tokens": rewrite_tokens,
            "usd_micros": _rewrite_recoverable_usd_micros(
                model, rewrite_tokens, context_before, cur.attrs.get("cache_read_tokens") or 0, pricing, at),
        })
    return {"events": events, "count": len(events),
            "total_usd_micros": _all_or_nothing_sum(events, "usd_micros", pricing, at)}



# story shape-archetype thresholds -- hardcoded, mirroring config/worker.yaml fold.story.* (geometry,
# never quality; the §4.B port reuses them verbatim -- keep in sync).
_STORY_MAX_CURVE_POINTS = 64
_STORY_SHORT_SPARK_MAX_DURATION_S = 300
_STORY_SHORT_SPARK_MAX_TOKENS = 20000
_STORY_SAWTOOTH_MIN_DROPS = 2
_STORY_CLIFF_SLOPE_MULTIPLIER = 4.0
_STORY_CLIFF_MIN_POINTS = 4
_STORY_LONG_TAIL_MIN_FRACTION = 0.4
_STORY_LONG_TAIL_MAX_GROWTH_FRACTION = 0.1
_STORY_STAIRCASE_MIN_FLATS = 2
_STORY_STAIRCASE_GAP_S = 300


def _story_raw_curve(events: list[dict]) -> list[list[float]]:
    """EconFold's `story._raw_curve`: [[t_rel_s, cum_tokens, ctx_tokens], ...], one point per
    assistant_message (cum/ctx grow by that event's own token total) plus a synthetic point at every
    compaction (ctx force-reset to tokens_after -- the sawtooth). Read from the event stream (the
    per-record token curve), not the collapsed graph nodes, so the curve reproduces the baseline."""
    t0 = None
    for event in events:
        t0 = _parse_iso(event.get("event_ts"))
        if t0 is not None:
            break
    if t0 is None:
        return []
    points: list[list[float]] = []
    cum_tokens = ctx_tokens = 0
    for event in events:
        ts = _parse_iso(event.get("event_ts"))
        if ts is None:
            continue
        t_rel = (ts - t0).total_seconds()
        etype = event.get("event_type")
        if etype == "assistant_message":
            tokens = sum((event.get("payload") or {}).get(f) or 0 for f in _ASSISTANT_USAGE)
            cum_tokens += tokens
            ctx_tokens += tokens
            points.append([t_rel, cum_tokens, ctx_tokens])
        elif etype == "compaction":
            after = (event.get("payload") or {}).get("tokens_after")
            if after is not None:
                ctx_tokens = after
                points.append([t_rel, cum_tokens, ctx_tokens])
    return points


def _story_downsample(points: list[list[float]], max_points: int) -> list[list[float]]:
    """Fixed-stride downsample by even index spacing (never value-based), keeping first + last --
    byte-identical for a given input (EconFold's `story._downsample`)."""
    n = len(points)
    if n <= max_points or max_points <= 0:
        return points
    if max_points == 1:
        return [points[-1]]
    step = (n - 1) / (max_points - 1)
    indices = sorted({round(i * step) for i in range(max_points)})
    return [points[i] for i in indices]


def _story_shape(points: list[list[float]]) -> str:
    """EconFold's `story._classify_shape`: the six geometry archetypes, checked most- to least-
    distinctive, over the RAW (un-downsampled) curve. Falls back to steady_climb."""
    if len(points) < 2:
        return "short_spark"
    total_duration_s = points[-1][0] - points[0][0]
    total_tokens = points[-1][1] - points[0][1]
    drops = sum(1 for prev, nxt in zip(points, points[1:]) if nxt[2] < prev[2])
    if drops >= _STORY_SAWTOOTH_MIN_DROPS:
        return "sawtooth"
    segments = []  # (dt, d_tokens, slope)
    for prev, nxt in zip(points, points[1:]):
        dt = nxt[0] - prev[0]
        if dt > 0:
            segments.append((dt, nxt[1] - prev[1], (nxt[1] - prev[1]) / dt))
    if len(segments) >= _STORY_CLIFF_MIN_POINTS:
        slopes = [s[2] for s in segments]
        median_slope = sorted(slopes)[len(slopes) // 2]
        if median_slope > 0 and max(slopes) > _STORY_CLIFF_SLOPE_MULTIPLIER * median_slope:
            return "cliff"
    if total_duration_s <= _STORY_SHORT_SPARK_MAX_DURATION_S and total_tokens <= _STORY_SHORT_SPARK_MAX_TOKENS:
        return "short_spark"
    if total_duration_s > 0 and total_tokens > 0:
        tail_start_t = points[-1][0] - _STORY_LONG_TAIL_MIN_FRACTION * total_duration_s
        tail_start_tokens = next((p[1] for p in points if p[0] >= tail_start_t), points[-1][1])
        if points[-1][1] - tail_start_tokens <= _STORY_LONG_TAIL_MAX_GROWTH_FRACTION * total_tokens:
            return "long_tail"
    flats = sum(1 for dt, _, _ in segments if dt >= _STORY_STAIRCASE_GAP_S)
    if flats >= _STORY_STAIRCASE_MIN_FLATS:
        return "staircase"
    return "steady_climb"


def story_of(events: list[dict]) -> dict[str, Any]:
    """info.story -- EconFold's `story.compute_story` (ROOT-session only, gated like turns):
    {curve, shape, anchors:{superlatives:[]}}. The per-record token curve reproduces the baseline
    exactly (integ's per-record assistant events sum to the same cumulative tokens); only the curve's
    `t_rel` column can drift by the normalizer's first-event `t0` choice (allowlisted, cf. ledger #10).
    Superlatives stay empty (EconFold pinned no rule); the personal median envelope is a query-time
    signal, never stored."""
    raw = _story_raw_curve(events)
    return {
        "curve": _story_downsample(raw, _STORY_MAX_CURVE_POINTS),
        "shape": _story_shape(raw),
        "anchors": {"superlatives": []},
    }


# practices thresholds -- hardcoded like _GIT_SUBCOMMANDS / _LONG_TURN_THRESHOLD_S, mirroring
# config/worker.yaml's fold.{verifiers,review_agent_types,mega_session_*} (the §4.B port reuses them
# verbatim; keep in sync with that section).
_VERIFIER_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"\bpytest\b", r"\bnpm\s+(run\s+)?test\b", r"\byarn\s+test\b", r"\bpnpm\s+test\b",
    r"\bgo\s+test\b", r"\bcargo\s+test\b", r"\btsc\b", r"\beslint\b", r"\bruff\b",
    r"\bmypy\b", r"\bnpm\s+run\s+build\b", r"\bmake\s+test\b",
)]
_REVIEW_AGENT_TYPES = {"review", "code-review", "code-reviewer", "reviewer"}
_MEGA_SESSION_SPAN_H = 4.0
_MEGA_SESSION_MIN_COMPACTIONS = 2
_COMMAND_WRAPPER_PREFIXES = ("<command-name>", "<local-command-stdout>")
_AT_REF_RE = re.compile(r"@\S+")


def _is_human_turn_start(payload: dict) -> bool:
    """EconFold's `turns.is_turn_boundary`, over a normalized user_message payload: a genuine human
    prompt -- not an injected/sidechain message, a slash-command wrapper, or the interrupt sentinel."""
    if payload.get("is_injected"):
        return False
    text = payload.get("text")
    if not isinstance(text, str) or not text:
        return False
    if text.lstrip().startswith(_COMMAND_WRAPPER_PREFIXES):
        return False
    if text.strip() == INTERRUPT_SENTINEL:
        return False
    return True


def practices_of(events: list[dict], compactions: list[dict], duration_ms: int | None) -> dict[str, Any]:
    """info.practices -- EconFold's `practices.compute_practices` (ROOT-session only; the caller gates
    on is_root, like turns). A regex/heuristic scan of the event stream: verifier commands + their
    exit status, ExitPlanMode, @-refs and char count of the first human prompt, interrupt sentinels,
    review/research spawns, and the compaction triggers (manual vs auto) + a fresh-session proxy.

    Reads events, not the graph (like `interrupted_of`): it needs raw per-message fields the graph
    doesn't surface as node semantics (char_count, the slash-command wrapper text, Bash exit_status).
    `delegation_ratio` is None here and patched at rollup from child tokens (build_session_info);
    `plan_phase`/`clear_used` stay None (EconFold deferred them, F6)."""
    verifier_runs = verifier_passes = exitplan_count = interrupts = at_refs = subagent_research = 0
    first_prompt_chars = None
    first_file_change_ts = None
    review_subagent = False
    seen_first_prompt = False
    for event in events:
        etype = event.get("event_type")
        payload = event.get("payload") or {}
        if etype == "user_message":
            text = payload.get("text")
            if isinstance(text, str) and text.strip() == INTERRUPT_SENTINEL:
                interrupts += 1
            if not seen_first_prompt and _is_human_turn_start(payload):
                seen_first_prompt = True
                first_prompt_chars = payload.get("char_count")
                if isinstance(text, str):
                    at_refs = len(_AT_REF_RE.findall(text))
        elif etype == "tool_call":
            name = payload.get("tool_name")
            if name == "ExitPlanMode":
                exitplan_count += 1
            elif name == "Bash":
                command = (payload.get("input") or {}).get("command")
                if isinstance(command, str) and any(p.search(command) for p in _VERIFIER_PATTERNS):
                    verifier_runs += 1
                    if payload.get("exit_status") == 0:
                        verifier_passes += 1
        elif etype == "file_change":
            if first_file_change_ts is None:
                first_file_change_ts = event.get("event_ts")
        elif etype == "sub_agent_spawn":
            agent_type = (payload.get("agent_type") or "").lower()
            ts = event.get("event_ts")
            if agent_type in _REVIEW_AGENT_TYPES:
                if first_file_change_ts is not None and (ts or "") >= first_file_change_ts:
                    review_subagent = True
            else:
                subagent_research += 1
    manual_compactions = sum(1 for c in compactions if c.get("trigger") == "manual")
    auto_compactions = len(compactions) - manual_compactions
    span_h = (duration_ms / 3_600_000) if duration_ms is not None else 0.0
    is_mega = span_h > _MEGA_SESSION_SPAN_H and len(compactions) >= _MEGA_SESSION_MIN_COMPACTIONS
    return {
        "verified": verifier_runs > 0,
        "verifier_runs": verifier_runs,
        "verifier_pass_rate": round(verifier_passes / verifier_runs, 4) if verifier_runs else None,
        "plan_phase": None,
        "exitplan_count": exitplan_count,
        "first_prompt_chars": first_prompt_chars,
        "at_refs": at_refs,
        "delegation_ratio": None,  # patched at rollup from child tokens (build_session_info)
        "subagent_research": subagent_research,
        "review_subagent": review_subagent,
        "interrupts": interrupts,
        "manual_compactions": manual_compactions,
        "auto_compactions": auto_compactions,
        "fresh_session": not is_mega,
        "clear_used": None,
    }


def info_of(family: Family, session_id: str, events: list[dict],
           pricing: Pricer | None = None, at: datetime | None = None) -> dict[str, Any]:
    """The `info` sub-dict -- §4.A parts + the ported §4.B heuristics produced from the graph/events:
    interrupted/carry/cache.hit_ratio/read_then_discard/resume_cost everywhere, plus practices/story/
    segments on root sessions. Only §4.C errors stay dropped (api_error, allowlisted).

    `pricing`/`at` (the session's own date) flow into `channel_cost_of`/`carry_of`/`resume_cost_of`
    and (root sessions only) `segments_mod.compute_segments`; everything else here is
    priced-independent."""
    graph = _self_graph(family, session_id)
    models = _models_for(family, session_id)
    compactions = compactions_of(family, session_id)
    duration_ms = _session_duration_ms(graph)
    info: dict[str, Any] = {
        "title": graph.meta.get("title") if graph else None,
        "tokens_total": tokens_total_of(models),
        "duration_ms": duration_ms,
        "compactions": compactions,
        "repo": repo_info(events),
        "mcp_servers": mcp_servers_of(tools_of(family, session_id)),
        "git_commands": git_commands_of(family, session_id),
        "channel_cost": channel_cost_of(models, pricing, at),
        "interrupted": interrupted_of(events, pricing, at),
        "carry": carry_of(family, session_id, pricing, at),
        "cache": cache_of(family, session_id),
        "read_then_discard": read_then_discard_of(family, session_id),
        "resume_cost": resume_cost_of(family, session_id, pricing, at),
        "cache_busts": cache_busts_of(family, session_id, pricing, at),
        "reasoning_tokens": reasoning_tokens_of(family, session_id),
        "event_count": len(events),
        "own_event_count": len(events),
    }
    # practices + story + segments are ROOT-session only (a subagent's user messages are injected task
    # prompts) -- gated like turns, matching EconFold's driver.
    if graph is not None and graph.parent_session_id is None:
        info["practices"] = practices_of(events, compactions, duration_ms)
        info["story"] = story_of(events)
        # segments are computed from the graph turn model (turns_of) + the event stream; the beat/
        # classification logic is behavior-preserving (vendored), but driven by graph turns its output
        # is graph-native -- accepted wholesale by the A/B (like the turn model, ledger #7).
        info["segments"] = segments_mod.compute_segments(
            events, turns_of(family, session_id), info["repo"], pricing, at,
        )
        # D1-context-curve: the per-turn context-size series SessionScreen's context chart reads.
        info["context_curve"] = context_curve_of(family, session_id)
    # inclusive_tokens is present only when the session has subagents (EconFold sets it in rollup).
    children_of: dict[str, list[str]] = {}
    for sid, child in family.children.items():
        children_of.setdefault(child.parent_session_id, []).append(sid)
    if session_id in children_of:
        info["inclusive_tokens"] = _inclusive_tokens(family, session_id, children_of)
    return info


def pr_list_of(events: list[dict]) -> list[dict[str, Any]]:
    """pr_list from pr_link events -- the graph doesn't model pr-link (out-of-tree), so this is a
    targeted event read (sub-plan §3 open q); identical to EconFold's `pr_list_rollup`."""
    out = []
    for event in events:
        if event.get("event_type") != "pr_link":
            continue
        p = event.get("payload", {})
        out.append({
            "ts": event.get("event_ts"),
            "pr_number": p.get("pr_number"),
            "pr_url": p.get("pr_url"),
            "pr_repository": p.get("pr_repository"),
        })
    return out


def build_session_info(family: Family, session_id: str, events: list[dict],
                       pricing: Pricer | None = None,
                       at: datetime | None = None) -> SessionInfo:
    """The canonical graph-based `session_info` for one session -- every field GraphFold produces,
    from the causal graph. Self-scoped when the family holds only this session; tree-scoped
    (subagents map, inclusive_tokens) when its children are loaded (rollup). `events` are this
    session's normalized events, used only for the few signals the graph doesn't model (pr_list, the
    repo evidence ladder). This is the single source of truth validated by the A/B differential and
    called by GraphFold.fold / rollup_from_family.

    `pricing`/`at` flow through to `info_of` -> `channel_cost_of`/`carry_of`/`resume_cost_of`/
    `compute_segments`; `None`/`None` (the default) reproduces the pre-pricing baseline exactly,
    which is what every existing call site and test fixture still gets.
    """
    graph = family.resolve(session_id)
    is_root = graph is not None and graph.parent_session_id is None
    models = _models_for(family, session_id)
    # Turns are ROOT-session only (a subagent's user messages are injected task prompts), matching
    # EconFold -- keyed on parent-null, not on being the family master.
    turns = turns_of(family, session_id) if is_root else []
    repo = repo_info(events)
    # subagent_count: len(subagents) once children are loaded (rollup); own spawn count on a plain
    # self-fold (no children in the family yet), matching EconFold's F1.
    subagents = subagents_of(family, session_id)
    own_tokens = tokens_total_of(models)
    info = info_of(family, session_id, events, pricing, at)
    # practices.delegation_ratio needs child token totals -- None on a plain self-fold, patched here
    # once the family's children are loaded (rollup), matching EconFold's rollup_subagents (F4).
    practices = info.get("practices")
    if practices is not None and subagents:
        child_tokens = sum(s["tokens"] for s in subagents.values())
        practices["delegation_ratio"] = round(child_tokens / own_tokens, 4) if own_tokens else None
    return SessionInfo(
        session_id=session_id,
        cost_usd=None,  # otel_trueup owns cost, in a separate write
        models=models,
        tools=tools_of(family, session_id),
        files=files_of(family, session_id),
        skills=skills_of(family, session_id),
        subagents=subagents,
        pr_list=pr_list_of(events),
        turns=turns,
        turn_count=len(turns),
        long_turn_count=sum(1 for t in turns if t["long"]),
        tokens_total=own_tokens,
        compaction_count=len(info["compactions"]),
        subagent_count=len(subagents) if subagents else spawn_count_of(family, session_id),
        error_count=0,  # api_error dropped (§4.C)
        info=info,
        repo_id=repo.get("repo_id") if repo else None,
        repo_id_provenance=repo.get("repo_id_provenance") if repo else None,
    )


def _parse_ts(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class GraphFold:
    """The graph-based replacement for EconFold. `fold()` builds THIS session's graph from its full
    deduped event blob and computes self-scoped `session_info` from it (returning the graph for the
    handler to persist). `rollup_from_family()` computes the tree-scoped fields from a family the
    handler loaded via GraphService. Same positional-dedup / recompute-from-full-blob idempotency as
    EconFold -- a re-delivery rebuilds from the full blob; a resend is a no-op.

    `pricing` prices `channel_cost`/`carry` at each session's own `started_at` (never "today") --
    `price_tokens` looks up whichever rate was in effect on that date, so a real rate change only
    ever prices sessions from that date forward; running the fold again later reproduces the exact
    same number. `None` (the default) reproduces the pre-pricing baseline exactly -- deliberately
    not auto-loaded here: `GraphFold()` bare is what every existing test call site constructs, and
    a constructor that silently starts pricing the moment a catalog exists on
    the caller's machine would change their behavior invisibly. Callers that want pricing load it
    themselves (`load_pricing_survivable`, below) and pass it in -- see `ingest/app.py`/
    `worker/cli_work.py` for the real ones."""

    def __init__(self, pricing: Pricer | None = None) -> None:
        self.pricing = pricing

    def fold(self, session: SessionState, session_events: list[dict]) -> SessionFoldResult:
        events = list(session.signal_blob.get("events", []))
        new_events = session_events[len(events):]
        for event in new_events:
            events.append(event)
            if event["event_type"] == "session_start" and session.parent_session_id is None:
                session.parent_session_id = event["payload"].get("parent_session_id")
        session.signal_blob = {**session.signal_blob, "events": events}

        stamps = [t for t in (_parse_ts(e.get("event_ts")) for e in events) if t is not None]
        if stamps:
            session.started_at = min(stamps)
            session.last_event_at = max(stamps)
        if new_events and session.status == "idle_closed":
            session.status = "live"

        graph = build_graph({
            "vendor": session.agent, "session_id": session.session_id, "source": "jsonl",
            "parent_session_id": session.parent_session_id, "events": events,
        })
        # A self-family holding just this session -- constructed directly (not GraphContext.load_self,
        # which is master-only) so a subagent's own arrival fold works too. Its subagents/tree fields
        # stay empty; they are filled at rollup over the loaded family.
        family = Family(master=Graph.from_dict(graph), children={})
        session_info = build_session_info(family, session.session_id, events,
                                          pricing=self.pricing, at=session.started_at)
        return SessionFoldResult(session=session, session_info=session_info, graph=graph)

    def rollup_from_family(self, parent: SessionState, family: Family) -> SessionInfoDelta:
        """Tree-scoped session_info for `parent` over an already-loaded family (its children
        resolved). `parent`'s own events ride in its signal_blob for the pr_list/repo signals."""
        events = list(parent.signal_blob.get("events", []))
        session_info = build_session_info(family, parent.session_id, events,
                                          pricing=self.pricing, at=parent.started_at)
        return SessionInfoDelta(session_info=session_info)
