"""`session_info` assembly from a request ledger (plan §5 P7). `build_session_info` is the
single place every `info.*` sub-key gets produced; several of them are direct ports of
`graph_fold.py` formulas (channel cost, cache busts, resume cost, the story shape classifier),
kept parity-tested against the real functions in `test_parity_graph_fold.py`.

**Implementation judgment calls** (the plan specifies most of this at the formula level; a
handful of fields have no stated distribution and are this module's own reasonable, documented
choice -- flagged here and in the progress log):
- `tools`' fixed per-tool rates (share of R that becomes a Read/Edit/Write/... call) are not
  given a number in the plan; chosen once here and reused for every session.
- `practices.at_refs`, `.review_subagent`, and `.subagent_research` have no stated formula;
  implemented as small, session-scoped draws from the `practices` stream.
- File line allocation across a PR's sessions is deterministic (weighted by each session's own
  `T(s)`, no extra randomness needed): calling `build_session_info` once per session of the same
  PR, each with the FULL per-session weight map, gives a result that still sums exactly to the
  PR's file totals regardless of call order (`allocate_integer` is a pure function of its inputs).
"""
from __future__ import annotations

import math
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Mapping

from ..allocation import allocate_integer
from ..randomness import rng_for
from . import ids, lexicon
from .config import ResolvedOrg
from .models import SessionInfoRecord, synthetic_marker
from .pricing import OrgPricer
from .requests import Ledger, Request, simulate
from .sessions import SessionPlan
from .work import WorkItem

_LONG_TURN_THRESHOLD_S = 1800  # graph_fold._LONG_TURN_THRESHOLD_S -- kept in sync (parity test)
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

#: session_type._SCORED_LABELS + ("MISC",), copied so `segments.label` uses the same vocabulary.
_SCORED_LABELS = ("BUG_FIX", "IMPLEMENT", "DESIGN", "OPS", "PLAN", "REVIEW", "COMPREHEND", "MISC")
_MAIN_LABEL_BY_CHANGE_TYPE = {"feature": "IMPLEMENT", "fix": "BUG_FIX", "refactor": "IMPLEMENT",
                              "test": "IMPLEMENT", "docs": "DESIGN", "chore": "OPS"}

_TOOL_RATES = {"Read": 0.35, "Edit": 0.25, "Write": 0.05, "Bash": 0.30, "Grep": 0.15,
              "Glob": 0.08, "TaskCreate": 0.02, "TaskUpdate": 0.02}


def _story_downsample(points: list[list[float]], max_points: int) -> list[list[float]]:
    """Ports `graph_fold._story_downsample` verbatim: fixed-stride, first+last kept."""
    n = len(points)
    if n <= max_points or max_points <= 0:
        return points
    if max_points == 1:
        return [points[-1]]
    step = (n - 1) / (max_points - 1)
    indices = sorted({round(i * step) for i in range(max_points)})
    return [points[i] for i in indices]


def _story_shape(points: list[list[float]]) -> str:
    """Ports `graph_fold._story_shape` verbatim."""
    if len(points) < 2:
        return "short_spark"
    total_duration_s = points[-1][0] - points[0][0]
    total_tokens = points[-1][1] - points[0][1]
    drops = sum(1 for prev, nxt in zip(points, points[1:]) if nxt[2] < prev[2])
    if drops >= _STORY_SAWTOOTH_MIN_DROPS:
        return "sawtooth"
    segments = []
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


def _team_of(resolved: ResolvedOrg, l1_user: str) -> str:
    return next(t.team_name for t in resolved.teams if l1_user in t.user_ids)


def _models_dict(ledger: Ledger) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for req in ledger.requests:
        b = out.setdefault(req.model, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
        b["input"] += req.input
        b["output"] += req.output
        b["cache_read"] += req.cache_read
        b["cache_write"] += req.cache_write
    return out


def _build_turns(ledger: Ledger, plan: SessionPlan,
                 children_plans: Mapping[str, SessionPlan]) -> list[dict]:
    by_turn: dict[int, list[Request]] = {}
    for req in ledger.requests:
        by_turn.setdefault(req.turn, []).append(req)
    turns = []
    for t_idx in sorted(by_turn):
        reqs = by_turn[t_idx]
        started, ended = reqs[0].ts, reqs[-1].ts
        duration_ms = int((ended - started).total_seconds() * 1000)
        tokens = sum(r.input + r.output + r.cache_read + r.cache_write for r in reqs)
        turns.append({
            "index": t_idx + 1, "started": started.isoformat(), "ended": ended.isoformat(),
            "duration_ms": duration_ms, "tokens": tokens,
            "long": duration_ms > _LONG_TURN_THRESHOLD_S * 1000, "source": "fallback",
            "files_changed": 0, "message_count": 2 * len(reqs) + 1,
            "subagents_spawned": 0, "subagent_session_ids": [],
        })
    for cid, cplan in children_plans.items():
        for turn in turns:
            if turn["started"] <= cplan.started_at.isoformat() <= turn["ended"]:
                turn["subagent_session_ids"].append(cid)
                turn["subagents_spawned"] = len(turn["subagent_session_ids"])
                break
    return turns


def _build_files_linked(resolved: ResolvedOrg, plan: SessionPlan, item: WorkItem, pr: dict,
                        item_session_tokens: Mapping[str, int]) -> dict[str, dict]:
    repo_name = item.repo.name
    weights = {sid: max(1, item_session_tokens.get(sid, 0)) for sid in item.sessions}
    files: dict[str, dict] = {}
    for f in pr.get("files") or []:
        path = f["path"]
        add_alloc = allocate_integer(f.get("additions") or 0, weights) if f.get("additions") else {
            k: 0 for k in weights}
        del_alloc = allocate_integer(f.get("deletions") or 0, weights) if f.get("deletions") else {
            k: 0 for k in weights}
        my_add = add_alloc.get(plan.session_id, 0)
        my_del = del_alloc.get(plan.session_id, 0)
        if my_add == 0 and my_del == 0:
            continue
        ext = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
        abs_path = f"/workspace/{repo_name}/{path}"
        files[abs_path] = {
            "changes": 1, "created": f.get("op") == "added" and my_add > 0, "extension": ext,
            "first_seen": plan.started_at.isoformat(), "last_seen": plan.ended_at.isoformat(),
            "lines_added": my_add, "lines_removed": my_del,
        }
    return files


def _build_files_unlinked(resolved: ResolvedOrg, plan: SessionPlan) -> dict[str, dict]:
    r = rng_for(resolved.config.org.seed, "files", "session", plan.session_id)
    if r.random() < 0.6:
        return {}
    n = r.randint(1, 3)
    candidates = ["README.md", "CHANGELOG.md", "docs/notes.md", ".env.example",
                 "config/settings.yaml", "Makefile", "docs/runbook.md"]
    paths = r.sample(candidates, min(n, len(candidates)))
    out = {}
    for path in paths:
        abs_path = f"/workspace/{plan.repo.name}/{path}"
        added = r.randint(1, 20)
        removed = r.randint(0, 5)
        ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
        out[abs_path] = {"changes": 1, "created": False, "extension": ext,
                         "first_seen": plan.started_at.isoformat(),
                         "last_seen": plan.ended_at.isoformat(),
                         "lines_added": added, "lines_removed": removed}
    return out


def _build_tools(resolved: ResolvedOrg, plan: SessionPlan, ledger: Ledger, subagent_count: int) -> dict[str, dict]:
    r = rng_for(resolved.config.org.seed, "tools", plan.session_id)
    R = len(ledger.requests)
    tools: dict[str, dict] = {}
    for name, rate in _TOOL_RATES.items():
        count = max(0, round(R * rate * r.uniform(0.7, 1.3)))
        if count == 0:
            continue
        failed = sum(1 for _ in range(count) if r.random() < 0.03)
        bytes_read_total = count * round(r.uniform(500, 4000)) if name == "Read" else 0
        tools[name] = {"ok": count - failed, "type": "builtin", "count": count, "failed": failed,
                       "bytes_read_total": bytes_read_total, "duration_ms_total": 0}
    if subagent_count > 0:
        tools["Task"] = {"ok": subagent_count, "type": "builtin", "count": subagent_count,
                         "failed": 0, "bytes_read_total": 0, "duration_ms_total": 0}
    return tools


def _subagent_purpose(seed: int, session_id: str) -> str:
    """The child's `purpose`, drawn once from `rng_for(seed, "text", "subagent", session_id)`'s
    FIRST value. Called independently from both the child's own `info.title` (build_session_info)
    and the parent's `subagents[session_id].purpose` (`_build_subagents`) -- `rng_for`'s
    determinism keeps the two in sync without threading a value between them."""
    return rng_for(seed, "text", "subagent", session_id).choice(lexicon.SUBAGENT_PURPOSES)


def _build_subagents(resolved: ResolvedOrg, children_plans: Mapping[str, SessionPlan],
                     children_records: Mapping[str, SessionInfoRecord]) -> dict[str, dict]:
    out = {}
    for cid, cplan in children_plans.items():
        record = children_records[cid]
        text_r = rng_for(resolved.config.org.seed, "text", "subagent", cid)
        dominant_model = max(record.models, key=lambda m: sum(record.models[m].values())) if record.models else None
        duration_ms = int((cplan.ended_at - cplan.started_at).total_seconds() * 1000)
        out[cid] = {
            "depth": 1, "files": {}, "model": dominant_model, "tools": record.tools,
            "origin": "human", "tokens": record.tokens_total,
            "purpose": text_r.choice(lexicon.SUBAGENT_PURPOSES),
            "agent_type": text_r.choice(lexicon.SUBAGENT_TYPES),
            "duration_ms": duration_ms, "inclusive_tokens": record.tokens_total,
        }
    return out


def _cache_busts(pricer: OrgPricer, ledger: Ledger, at) -> dict:
    events = []
    for prev, cur in zip(ledger.requests, ledger.requests[1:]):
        if cur.kind != "bust":
            continue
        context_before = prev.cache_read + prev.cache_write
        gap_s = (cur.ts - prev.ts).total_seconds()
        usd = pricer.rewrite_recoverable_usd_micros(cur.model, cur.cache_write, context_before,
                                                     cur.cache_read, at)
        events.append({"ts": cur.ts.isoformat(), "gap_s": int(gap_s), "model": cur.model,
                       "usd_micros": usd, "context_before": context_before,
                       "rewrite_tokens": cur.cache_write})
    total = sum(e["usd_micros"] for e in events) if all(e["usd_micros"] is not None for e in events) else None
    return {"events": events, "count": len(events), "total_usd_micros": total}


def _resume_cost(pricer: OrgPricer, ledger: Ledger, cfg, at) -> dict:
    events = []
    for prev, cur in zip(ledger.requests, ledger.requests[1:]):
        if cur.kind != "resume":
            continue
        context_before = prev.cache_read + prev.cache_write
        gap_s = (cur.ts - prev.ts).total_seconds()
        usd = pricer._price_channel({cur.model: {"cache_write": cur.cache_write}}, "cache_write", at)
        recoverable = pricer.rewrite_recoverable_usd_micros(cur.model, cur.cache_write, context_before,
                                                             cur.cache_read, at)
        events.append({"ts": cur.ts.isoformat(), "turn": cur.turn + 1, "gap_s": int(gap_s),
                       "ttl_s": cfg.ttl_s, "usd_micros": usd, "reload_tokens": cur.cache_write,
                       "context_before": context_before, "recoverable_usd_micros": recoverable})
    total = sum(e["usd_micros"] for e in events) if all(e["usd_micros"] is not None for e in events) else None
    total_recoverable = (sum(e["recoverable_usd_micros"] for e in events)
                        if all(e["recoverable_usd_micros"] is not None for e in events) else None)
    projected = None
    if ledger.requests:
        last = ledger.requests[-1]
        ctx_tokens = last.cache_read + last.cache_write
        projected = {"context_tokens": ctx_tokens, "ttl_s": cfg.ttl_s,
                    "usd_micros": pricer._price_channel({last.model: {"cache_write": ctx_tokens}},
                                                         "cache_write", at)}
    return {"events": events, "count": len(events), "total_usd_micros": total,
           "total_recoverable_usd_micros": total_recoverable, "projected": projected}


def _interrupted(pricer: OrgPricer, ledger: Ledger, at) -> dict:
    events = []
    for req in ledger.requests:
        if not req.interrupted:
            continue
        tokens = {"input": req.input, "output": req.output, "cache_read": req.cache_read,
                 "cache_write": req.cache_write}
        detail = [{"ttl_s": ttl, "tokens": n} for ttl, n in
                  ((300, req.cache_write_5m), (3600, req.cache_write_1h)) if n]
        usd = pricer.request_micros(req.model, tokens, at, detail)
        events.append({"ts": req.ts.isoformat(), "tokens": tokens, "usd_micros": usd})
    return {"events": events, "count": len(events),
           "total_tokens": sum(sum(e["tokens"].values()) for e in events),
           "total_usd_micros": sum(e["usd_micros"] for e in events) if events else 0}


def _build_segments(resolved: ResolvedOrg, plan: SessionPlan, ledger: Ledger, item: WorkItem | None,
                    turns: list[dict], pricer: OrgPricer, at) -> list[dict]:
    R = len(ledger.requests)
    n_turns = len(turns)
    if n_turns == 0:
        return []
    count = min(n_turns, max(1, min(4, 1 + R // 150)))
    r = rng_for(resolved.config.org.seed, "segments", plan.session_id)

    if item is not None:
        main = _MAIN_LABEL_BY_CHANGE_TYPE[item.change_type]
        pool = [main]
        if count >= 2:
            pool = [r.choice(("COMPREHEND", "PLAN")), main] if r.random() < 0.5 else [main, r.choice(("REVIEW", "OPS"))]
        if count >= 3:
            pool = [r.choice(("COMPREHEND", "PLAN")), main, r.choice(("REVIEW", "OPS"))]
    else:
        pool = [r.choice(("COMPREHEND", "DESIGN", "OPS", "MISC")) for _ in range(count)]
    while len(pool) < count:
        pool.append(main if item is not None else r.choice(("COMPREHEND", "DESIGN", "OPS", "MISC")))
    pool = pool[:count]

    # Split `turns` into `count` contiguous groups.
    base_size = n_turns // count
    remainder = n_turns % count
    groups: list[list[dict]] = []
    idx = 0
    for i in range(count):
        size = base_size + (1 if i < remainder else 0)
        size = max(size, 1) if idx < n_turns else 0
        groups.append(turns[idx:idx + size])
        idx += size
    groups = [g for g in groups if g]
    pool = pool[:len(groups)]

    segments = []
    turn_index_by_pos = {t["index"] - 1: t for t in turns}
    for i, (label, group) in enumerate(zip(pool, groups)):
        turn_positions = {t["index"] - 1 for t in group}
        seg_requests = [req for req in ledger.requests if req.turn in turn_positions]
        models: dict[str, dict[str, int]] = {}
        for req in seg_requests:
            b = models.setdefault(req.model, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
            b["input"] += req.input
            b["output"] += req.output
            b["cache_read"] += req.cache_read
            b["cache_write"] += req.cache_write
        t0 = group[0]["started"]
        t1 = group[-1]["ended"]
        beats = {"setup": r.randint(0, 2), "survey": r.randint(0, 2), "build": r.randint(0, 3),
                "check": r.randint(0, 2), "pause": r.randint(0, 1), "revision_loop": r.randint(0, 1)}
        segments.append({
            "index": i, "t0": t0, "t1": t1, "label": label, "confidence": round(r.uniform(0.5, 0.95), 4),
            # a count, not the list of turn indices (api-latency plan D8): every SQL and
            # Python reader treats `turns` as "how many", matching worker/segments/beats.py's
            # `turns_in_span` and SessionScreen.tsx's `{seg.turns} turns`.
            "turns": len(group),
            "files": 0, "beats": beats, "models": models, "repo_key": plan.repo.name,
            "cost_usd_micros": pricer.cost_micros(models, at) if models else 0,
            "carry_usd_micros": pricer._price_channel(models, "cache_read", at) if models else 0,
        })
    return segments


def _build_practices(resolved: ResolvedOrg, plan: SessionPlan, ledger: Ledger, habits,
                     own_tokens: int, child_tokens: int, subagent_count: int,
                     compactions_by_trigger: Mapping[str, int]) -> dict:
    r = rng_for(resolved.config.org.seed, "practices", plan.session_id)
    interrupts = sum(1 for req in ledger.requests if req.interrupted)
    verified = r.random() < habits.verified_prob
    verifier_runs = r.randint(1, 4) if verified else 0
    verifier_pass_rate = round(r.uniform(0.6, 1.0), 4) if verifier_runs else None
    duration_h = (plan.ended_at - plan.started_at).total_seconds() / 3600
    fresh_session = not (duration_h > 4 and len(ledger.compactions) >= 2)
    delegation_ratio = round(child_tokens / own_tokens, 4) if subagent_count and own_tokens else None
    return {
        "at_refs": r.randint(0, 5), "auto_compactions": compactions_by_trigger.get("auto", 0),
        "manual_compactions": compactions_by_trigger.get("manual", 0), "clear_used": None,
        "delegation_ratio": delegation_ratio,
        "exitplan_count": 1 if r.random() < habits.plan_mode_prob else 0,
        "first_prompt_chars": round(r.uniform(40, 1200)), "fresh_session": fresh_session,
        "interrupts": interrupts, "plan_phase": None,
        "review_subagent": subagent_count > 0 and r.random() < 0.2,
        "subagent_research": subagent_count, "verified": verified,
        "verifier_pass_rate": verifier_pass_rate, "verifier_runs": verifier_runs,
    }


def build_session_info(resolved: ResolvedOrg, plan: SessionPlan, ledger: Ledger, *,
                       item: WorkItem | None, pr: dict | None, pr_list: list[dict] | None,
                       item_session_tokens: Mapping[str, int] | None,
                       children_plans: Mapping[str, SessionPlan],
                       children_records: Mapping[str, SessionInfoRecord],
                       pricer: OrgPricer, purpose: str | None = None,
                       session_commit_count: int = 0) -> SessionInfoRecord:
    seed = resolved.config.org.seed
    team = resolved.team_effective[_team_of(resolved, plan.l1_user)]
    is_root = plan.parent_session_id is None
    at = plan.started_at.date()
    linked = item is not None

    models = _models_dict(ledger)
    tokens_total = sum(sum(b.values()) for b in models.values())
    cost_micros = pricer.cost_micros(models, at)
    cost_usd = (Decimal(cost_micros) / Decimal(1_000_000)).quantize(Decimal("0.0001"),
                                                                     rounding=ROUND_HALF_EVEN)
    repo_id = f"github:{resolved.config.org.github_owner}/{plan.repo.name}".lower()

    turns = _build_turns(ledger, plan, children_plans) if is_root else []
    long_turn_count = sum(1 for t in turns if t["long"])
    subagent_count = len(plan.children)
    tools = _build_tools(resolved, plan, ledger, subagent_count)
    error_count = sum(t.get("failed", 0) for t in tools.values())

    if not is_root:
        files = {}  # children always report no files, regardless of the parent's link status
    elif linked and pr is not None:
        files = _build_files_linked(resolved, plan, item, pr, item_session_tokens or {})
    else:
        files = _build_files_unlinked(resolved, plan)

    subagents = _build_subagents(resolved, children_plans, children_records) if is_root and children_plans else {}

    total_cr = sum(m["cache_read"] for m in models.values())
    total_input = sum(m["input"] for m in models.values())
    hit_ratio = round(total_cr / (total_cr + total_input), 4) if (total_cr + total_input) else 0.0

    git_branch = plan.repo.default_branch
    if item is not None and pr is not None:
        git_branch = pr.get("branch") or plan.repo.default_branch

    contexts = [req.cache_read + req.cache_write for req in ledger.requests]
    carry = {
        "requests": len(ledger.requests), "cache_read_tokens": total_cr,
        "carry_usd_micros": pricer._price_channel(models, "cache_read", at),
        "peak_context": max(contexts, default=0),
        "mean_context": round(sum(contexts) / len(contexts)) if contexts else 0,
    }

    if not is_root:
        # Children: "the purpose" (plan §5 P7's title table) -- the same value
        # `_build_subagents` puts in the parent's `subagents[session_id].purpose`, since both
        # draw from the same `rng_for(seed, "text", "subagent", session_id)` stream.
        title = purpose if purpose is not None else _subagent_purpose(seed, plan.session_id)
    elif linked and pr is not None:
        title = lexicon.session_title(rng_for(seed, "text", "session", plan.session_id), pr["title"])
    else:
        # Unlinked, or linked to an item that never got a PR (undefined by the title table's
        # 3-way split; falls back to the unlinked pool since there is no PR title to draw from).
        title = rng_for(seed, "text", "session", plan.session_id).choice(lexicon.UNLINKED_TITLES)

    compactions_by_trigger: dict[str, int] = {}
    for c in ledger.compactions:
        compactions_by_trigger[c.trigger] = compactions_by_trigger.get(c.trigger, 0) + 1

    commit_count = session_commit_count
    git_r = rng_for(seed, "text", "git_commands", plan.session_id)
    info: dict = {
        "title": title,
        "repo": {"cwd": f"/workspace/{plan.repo.name}", "git_branch": git_branch, "repo_id": repo_id,
                "repo_key": plan.repo.name, "repo_id_provenance": "remote",
                "repos": [{"refs": len(ledger.requests), "primary": True, "repo_id": repo_id,
                          "repo_key": plan.repo.name, "repo_id_provenance": "remote"}]},
        "cache": {"hit_ratio": hit_ratio},
        "carry": carry,
        "channel_cost": pricer.channel_micros(models, at),
        "cache_busts": _cache_busts(pricer, ledger, at),
        "resume_cost": _resume_cost(pricer, ledger, team.sessions, at),
        "interrupted": _interrupted(pricer, ledger, at),
        "compactions": [{"ts": c.ts.isoformat(), "before": c.before_tokens, "after": c.after_tokens,
                        "trigger": c.trigger, "reclaimed": c.before_tokens - c.after_tokens,
                        "duration_ms": c.duration_ms} for c in ledger.compactions],
        "reasoning_tokens": round(sum(m["output"] for m in models.values()) *
                                  rng_for(seed, "requests", plan.session_id, "reasoning").uniform(0.2, 0.5)),
        "duration_ms": int((plan.ended_at - plan.started_at).total_seconds() * 1000),
        "event_count": 3 * len(ledger.requests) + 2 * ledger.turn_count,
        "mcp_servers": {"attached": [], "by_server": {}},
        "git_commands": {"commit": commit_count, "push": 1 if commit_count > 0 else 0,
                        "checkout": 1 if linked else 0, "branch": 0,
                        "fetch": git_r.randint(0, 1), "pull": git_r.randint(0, 1), "merge": 0},
        "read_then_discard": {"events": [], "total_bytes_read": 0, "total_est_tokens": 0},
        # TODO(synthetic-org): read-then-discard waste is not simulated; always empty.
        "synthetic": synthetic_marker(seed),
        "tokens_total": tokens_total,
    }
    info["own_event_count"] = info["event_count"]

    if is_root:
        requests_by_turn: dict[int, list[Request]] = {}
        for req in ledger.requests:
            requests_by_turn.setdefault(req.turn, []).append(req)
        compaction_positions = {c.position for c in ledger.compactions}
        context_curve = []
        for t in turns:
            turn_reqs = requests_by_turn.get(t["index"] - 1)
            if not turn_reqs:
                continue
            last = turn_reqs[-1]
            context_curve.append({
                "ts": last.ts.isoformat(), "index": t["index"],
                "compaction": any(req.index in compaction_positions for req in turn_reqs),
                "context_tokens": last.cache_read + last.cache_write,
            })
        info["context_curve"] = context_curve
        t0 = plan.started_at
        raw_curve = []
        cum = 0
        for req in ledger.requests:
            cum += req.input + req.output + req.cache_read + req.cache_write
            minutes = (req.ts - t0).total_seconds() / 60
            raw_curve.append([minutes, cum, cum])
        info["story"] = {"curve": _story_downsample(raw_curve, _STORY_MAX_CURVE_POINTS),
                         "shape": _story_shape(raw_curve), "anchors": {"superlatives": []}}
        info["segments"] = _build_segments(resolved, plan, ledger, item, turns, pricer, at)
        child_tokens = sum(children_records[c].tokens_total for c in children_plans) if children_plans else 0
        info["practices"] = _build_practices(resolved, plan, ledger, team.habits, tokens_total,
                                             child_tokens, subagent_count, compactions_by_trigger)
        if children_plans:
            info["inclusive_tokens"] = tokens_total + child_tokens

    return SessionInfoRecord(
        session_id=plan.session_id, models=models, tokens_total=tokens_total,
        cost_effective_usd_micros=cost_micros, cost_usd=cost_usd, repo_id=repo_id,
        turn_count=len(turns) if is_root else ledger.turn_count,
        long_turn_count=long_turn_count, compaction_count=len(ledger.compactions),
        subagent_count=subagent_count, error_count=error_count, turns=turns, files=files,
        tools=tools, skills=[], subagents=subagents, pr_list=pr_list or [], info=info)


def build_all(resolved: ResolvedOrg, plans: list[SessionPlan], work, git_result, pricer: OrgPricer,
             *, log=None) -> tuple[dict[str, SessionInfoRecord], dict[str, Ledger]]:
    """Orchestrate `simulate` + `build_session_info` over every session: resolve each session's
    item/PR/purpose, compute `item_session_tokens` once per item, and build children before
    their parent (`build_session_info`'s `subagents` field needs the children's own records).
    A non-streaming convenience used by this task's own tests and reused by P8's per-session
    loop (which additionally streams straight into the `DatasetWriter` instead of returning a
    dict). Returns `(records, ledgers)`: the ledgers carry the per-request five-type split that
    `SessionInfoRecord.models` (one combined `cache_write`) cannot, for the I1 check."""
    plans_by_id = {p.session_id: p for p in plans}
    children_by_parent: dict[str, list[SessionPlan]] = {}
    for p in plans:
        if p.parent_session_id:
            children_by_parent.setdefault(p.parent_session_id, []).append(p)

    items_by_pr_key = work.items
    pr_by_pr_key: dict[str, dict] = {}
    for a in git_result.artifacts:
        if a["kind"] != "pr" or a["info"].get("github.is_revert"):
            continue
        for key in items_by_pr_key:
            if a["artifact_id"].endswith(f":pr:{key}"):
                pr_by_pr_key[key] = a
                break

    commit_count_by_session: dict[str, int] = {}
    for link in git_result.session_links:
        if link["method"] == "sha":
            commit_count_by_session[link["session_id"]] = commit_count_by_session.get(
                link["session_id"], 0) + 1

    item_session_tokens: dict[str, int] = {}
    for item in work.items.values():
        for sid in item.sessions:
            plan = plans_by_id[sid]
            total = sum(sum(c.values()) for c in plan.models.values())
            for child in children_by_parent.get(sid, ()):
                total += sum(sum(c.values()) for c in child.models.values())
            item_session_tokens[sid] = total

    def _team_of_l1(l1_user: str) -> str:
        return next(t.team_name for t in resolved.teams if l1_user in t.user_ids)

    def _item_and_pr(sid: str):
        item_id = work.session_item.get(sid)
        item = work.items.get(item_id) if item_id else None
        pr = pr_by_pr_key.get(item.item_id) if item and item.has_pr else None
        return item, pr

    records: dict[str, SessionInfoRecord] = {}
    ledgers: dict[str, Ledger] = {}
    roots = [p for p in plans if p.parent_session_id is None]
    children = [p for p in plans if p.parent_session_id is not None]

    for plan in children:
        item, pr = _item_and_pr(plan.session_id)
        team = resolved.team_effective[_team_of_l1(plan.l1_user)]
        ledger = simulate(resolved, plan, team.sessions, team.habits, log=log)
        ledgers[plan.session_id] = ledger
        purpose = _subagent_purpose(resolved.config.org.seed, plan.session_id)
        records[plan.session_id] = build_session_info(
            resolved, plan, ledger, item=item, pr=pr,
            pr_list=git_result.pr_list_by_session.get(plan.session_id, []),
            item_session_tokens=item_session_tokens, children_plans={}, children_records={},
            pricer=pricer, purpose=purpose,
            session_commit_count=commit_count_by_session.get(plan.session_id, 0))

    for plan in roots:
        item, pr = _item_and_pr(plan.session_id)
        team = resolved.team_effective[_team_of_l1(plan.l1_user)]
        ledger = simulate(resolved, plan, team.sessions, team.habits, log=log)
        ledgers[plan.session_id] = ledger
        cplans = {c: plans_by_id[c] for c in plan.children}
        crecords = {c: records[c] for c in plan.children if c in records}
        records[plan.session_id] = build_session_info(
            resolved, plan, ledger, item=item, pr=pr,
            pr_list=git_result.pr_list_by_session.get(plan.session_id, []),
            item_session_tokens=item_session_tokens, children_plans=cplans,
            children_records=crecords, pricer=pricer,
            session_commit_count=commit_count_by_session.get(plan.session_id, 0))

    return records, ledgers
