"""Beat detection and segmentation (R4 -- the surviving half of storytelling after the
feasibility probe; session-storytelling.md §4's taxonomy). Computed at fold time, not
read-path as that doc's original draft specified: segments persist (`info.segments`) and
carry priced cost, which needs a stable, once-computed span -- the same reasoning every other
rollup in this package is fold-time, and what makes "boundaries survive a re-fold unchanged"
(R4's own done-when bar) a testable fold-idempotency property rather than a UI concern.

Every beat = `{kind, t0, t1, evidence, confidence}`. `confidence` is `"fact"` for a beat read
straight off an event, `"inferred"` for the one taxonomy member that requires interpretation
(`revision_loop` -- STORY §4's own note: the only inference in the eleven, and the reason
"mark as expected" exists in that doc's future work).

No narrator line, no acts prose (revised-scope plan §2, R4's own rule): beats and segments are
labelled and priced, never narrated.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from common.model_pricing import Pricer, price_to_micros

from ._util import INTERRUPT_SENTINEL, event_tokens
from .config import BeatsConfig

_READ_TOOLS = {"Read", "Grep", "Glob", "NotebookRead"}

#: R4.2's segmentation-boundary subset -- context_squeeze, wrap_up and delegation are always
#: strong; `pause` only counts when it clears the higher `segment_pause_threshold_s` bar (a
#: beat-level Pause fires far more often than a real phase change warrants).
_BOUNDARY_KINDS = {"context_squeeze", "wrap_up", "delegation"}


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _compile(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def detect_beats(events: list[dict], turns_list: list[dict], config: BeatsConfig,
                 verifier_patterns: list[str]) -> list[dict]:
    """All eleven kinds (R4.1), sorted by `t0`. `verifier_patterns` is `FoldConfig.verifiers`
    -- the same Bash-command allowlist `practices.py`'s verifier detection already uses, so
    "was this a Check beat" and "did this session verify at all" never drift apart."""
    beats: list[dict] = []
    beats.extend(_setup(events))
    beats.extend(_survey(events, config.survey_min_reads))
    beats.extend(_build(events, config.build_cluster_gap_s))
    beats.extend(_check(events, _compile(verifier_patterns)))
    beats.extend(_revision_loop(events, config.revision_loop_min_edits))
    beats.extend(_delegation(events))
    beats.extend(_context_squeeze(events))
    beats.extend(_stall(events, config.stall_cluster_gap_s, config.stall_min_events))
    beats.extend(_pause(turns_list, config.pause_threshold_s))
    beats.extend(_interruption(events))
    beats.extend(_wrap_up(events))
    beats.sort(key=lambda b: b["t0"] or "")
    return beats


def _setup(events: list[dict]) -> list[dict]:
    """"Context loaded" -- the first assistant_message's own context (`cache_read +
    cache_write`, the same "what was live" definition R2/R3 already use), not a by-origin
    breakdown (that's the parked provenance work)."""
    for event in events:
        if event["event_type"] != "assistant_message":
            continue
        payload = event["payload"]
        tokens = (payload.get("cache_read_tokens") or 0) + (payload.get("cache_write_tokens") or 0)
        if tokens <= 0:
            return []
        ts = event.get("event_ts")
        return [{"kind": "setup", "t0": ts, "t1": ts, "evidence": {"tokens": tokens}, "confidence": "fact"}]
    return []


def _survey(events: list[dict], min_reads: int) -> list[dict]:
    """A maximal run of read-type tool_calls (Read/Grep/Glob/NotebookRead) with no
    `file_change` breaking it, at least `min_reads` long."""
    beats = []
    run: list[dict] = []

    def flush():
        if len(run) >= min_reads:
            beats.append({
                "kind": "survey", "t0": run[0].get("event_ts"), "t1": run[-1].get("event_ts"),
                "evidence": {"reads": len(run)}, "confidence": "fact",
            })

    for event in events:
        etype = event["event_type"]
        if etype == "file_change":
            flush()
            run = []
            continue
        if etype != "tool_call":
            continue
        if event["payload"].get("tool_name") in _READ_TOOLS:
            run.append(event)
        else:
            flush()
            run = []
    flush()
    return beats


def _build(events: list[dict], cluster_gap_s: float) -> list[dict]:
    """A cluster of `file_change` events no more than `cluster_gap_s` apart."""
    changes = [e for e in events if e["event_type"] == "file_change"]
    beats = []
    cluster: list[dict] = []

    def flush():
        if not cluster:
            return
        paths = {c["payload"].get("path") for c in cluster}
        beats.append({
            "kind": "build", "t0": cluster[0].get("event_ts"), "t1": cluster[-1].get("event_ts"),
            "evidence": {
                "files": len(paths),
                "lines_added": sum(c["payload"].get("lines_added") or 0 for c in cluster),
                "lines_removed": sum(c["payload"].get("lines_removed") or 0 for c in cluster),
            },
            "confidence": "fact",
        })

    prev_ts = None
    for e in changes:
        ts = _parse_ts(e.get("event_ts"))
        if cluster and prev_ts is not None and ts is not None and (ts - prev_ts).total_seconds() > cluster_gap_s:
            flush()
            cluster = []
        cluster.append(e)
        prev_ts = ts
    flush()
    return beats


def _check(events: list[dict], verifier_patterns: list[re.Pattern]) -> list[dict]:
    """One beat per Bash call matching a configured verifier pattern -- the same allowlist
    `practices.py`'s `verified`/`verifier_runs` already uses."""
    beats = []
    for event in events:
        if event["event_type"] != "tool_call":
            continue
        payload = event["payload"]
        if payload.get("tool_name") != "Bash":
            continue
        command = (payload.get("input") or {}).get("command")
        if not isinstance(command, str) or not any(p.search(command) for p in verifier_patterns):
            continue
        ts = event.get("event_ts")
        beats.append({
            "kind": "check", "t0": ts, "t1": ts,
            "evidence": {"ok": payload.get("exit_status") == 0}, "confidence": "fact",
        })
    return beats


def _revision_loop(events: list[dict], min_edits: int) -> list[dict]:
    """(Inferred.) A path with `min_edits` or more `file_change` events anywhere in the
    session -- "revised N times," STORY §4's own phrasing, is the only taxonomy member that
    interprets rather than reads a fact straight off one event."""
    by_path: dict[str, list[dict]] = {}
    for e in events:
        if e["event_type"] != "file_change":
            continue
        by_path.setdefault(e["payload"].get("path"), []).append(e)

    beats = []
    for path, changes in by_path.items():
        if len(changes) < min_edits or not path:
            continue
        beats.append({
            "kind": "revision_loop", "t0": changes[0].get("event_ts"), "t1": changes[-1].get("event_ts"),
            "evidence": {"file": path, "count": len(changes)}, "confidence": "inferred",
        })
    return beats


def _delegation(events: list[dict]) -> list[dict]:
    """One beat per `sub_agent_spawn` -- own-session evidence only (`count`, `agent_type`);
    the child's own token totals arrive on a separate fold and are not cross-referenced here,
    the same own-events boundary every other `_recompute`-time rollup respects."""
    beats = []
    for e in events:
        if e["event_type"] != "sub_agent_spawn":
            continue
        ts = e.get("event_ts")
        beats.append({
            "kind": "delegation", "t0": ts, "t1": ts,
            "evidence": {"agent_type": e["payload"].get("agent_type")}, "confidence": "fact",
        })
    return beats


def _context_squeeze(events: list[dict]) -> list[dict]:
    """One beat per `compaction` event."""
    beats = []
    for e in events:
        if e["event_type"] != "compaction":
            continue
        payload = e["payload"]
        ts = e.get("event_ts")
        beats.append({
            "kind": "context_squeeze", "t0": ts, "t1": ts,
            "evidence": {"before": payload.get("tokens_before"), "after": payload.get("tokens_after")},
            "confidence": "fact",
        })
    return beats


def _stall(events: list[dict], cluster_gap_s: float, min_events: int) -> list[dict]:
    """A cluster of `api_error` events no more than `cluster_gap_s` apart, at least
    `min_events` long -- a single retry is normal traffic, not a stall."""
    errors = [e for e in events if e["event_type"] == "api_error"]
    beats = []
    cluster: list[dict] = []

    def flush():
        if len(cluster) >= min_events:
            beats.append({
                "kind": "stall", "t0": cluster[0].get("event_ts"), "t1": cluster[-1].get("event_ts"),
                "evidence": {"retries": len(cluster)}, "confidence": "fact",
            })

    prev_ts = None
    for e in errors:
        ts = _parse_ts(e.get("event_ts"))
        if cluster and prev_ts is not None and ts is not None and (ts - prev_ts).total_seconds() > cluster_gap_s:
            flush()
            cluster = []
        cluster.append(e)
        prev_ts = ts
    flush()
    return beats


def _pause(turns_list: list[dict], threshold_s: float) -> list[dict]:
    """A turn-to-turn gap past `threshold_s` -- the same gap `cache_rollup`'s decay detection
    and `resume_reload_rollup` (R3) walk, at the beat-taxonomy's own (looser) threshold."""
    beats = []
    for prev_turn, next_turn in zip(turns_list, turns_list[1:]):
        prev_end = _parse_ts(prev_turn.get("ended"))
        next_start = _parse_ts(next_turn.get("started"))
        if prev_end is None or next_start is None:
            continue
        gap_s = (next_start - prev_end).total_seconds()
        if gap_s > threshold_s:
            beats.append({
                "kind": "pause", "t0": prev_turn.get("ended"), "t1": next_turn.get("started"),
                "evidence": {"gap_s": int(gap_s)}, "confidence": "fact",
            })
    return beats


def _interruption(events: list[dict]) -> list[dict]:
    """One beat per `[Request interrupted by user]` sentinel."""
    beats = []
    for e in events:
        if e["event_type"] != "user_message":
            continue
        text = e["payload"].get("text")
        if isinstance(text, str) and text.strip() == INTERRUPT_SENTINEL:
            ts = e.get("event_ts")
            beats.append({"kind": "interruption", "t0": ts, "t1": ts, "evidence": {}, "confidence": "fact"})
    return beats


def beats_in_range(all_beats: list[dict], t0: str | None, t1: str | None) -> list[dict]:
    """R5's scoring model (session_type.classify_segment) needs each segment's own beat
    *objects* -- `check.evidence.ok`, `revision_loop.evidence`, timing for the diagnostic-vs-
    exploratory read split -- not just `build_segments`'s own `{kind: count}` tally.

    Filters on the segment dict's own stored `t0`/`t1` (a closed range, both real event
    timestamps `build_segments` already sets from `seg_events[0]`/`seg_events[-1]`) rather than
    reusing `build_segments`'s internal cut-based range -- deliberately: that range is a detail
    private to boundary detection, and reaching back into it would couple this helper to
    `build_segments`'s own control flow. The (small, edge-case-only) cost is a beat landing in
    the sliver between a segment's last event and the next segment's cut boundary can be missed
    here even though `build_segments`'s own tally counted it -- acceptable since this feeds a
    scoring model, not a persisted count that needs to match exactly."""
    if t0 is None or t1 is None:
        return []
    return [b for b in all_beats if b.get("t0") is not None and t0 <= b["t0"] <= t1]


def segment_boundaries(all_beats: list[dict], segment_pause_threshold_s: float) -> list[str]:
    """R4.2: segments are the spans between *strong* beats -- `context_squeeze`, `wrap_up`
    and `delegation` always qualify (a compaction, a commit/PR, or a subagent spawn is a real
    phase change, Task-Classification-and-Analysis.md §4's own list); `pause` only qualifies
    past `segment_pause_threshold_s`, well above the beat-level `pause_threshold_s` -- a
    session pauses far more often than it changes phase, so using every beat-level Pause as a
    boundary would over-segment badly."""
    marks = []
    for b in all_beats:
        if b["kind"] in _BOUNDARY_KINDS and b.get("t0"):
            marks.append(b["t0"])
        elif b["kind"] == "pause" and (b["evidence"].get("gap_s") or 0) > segment_pause_threshold_s and b.get("t1"):
            marks.append(b["t1"])  # the resuming turn's start, not the gap's own start
    return sorted(set(marks))


def build_segments(events: list[dict], turns_list: list[dict], all_beats: list[dict],
                   config: BeatsConfig, pricing: Pricer | None,
                   ) -> list[tuple[dict, list[dict]]]:
    """R4.2/R4.3: `[(segment, segment_events)]`, unlabelled -- the caller (driver.py) runs
    R5's classifier over each `segment_events` slice and attaches `label`/`confidence` before
    persisting `segment`; `segment_events` itself is never persisted (it is raw event content,
    the same boundary `metrics.py`'s rollups never cross).

    `segment = {index, t0, t1, turns, files, beats: {kind: count}, carry_usd_micros,
    cost_usd_micros, models}`. Cost (R4.3) is priced fresh per segment -- not sliced out of
    `info.carry`'s session-wide total -- so "the context it was dragging" is exactly the
    context live during *this* segment's own requests, on the same all-or-nothing pricing
    terms as `carry_rollup`/`resume_reload_rollup`: no `pricing`, or any request's
    model/date unpriced, and `carry_usd_micros`/`cost_usd_micros`/`models` are all `None`
    rather than partial. `models` (X4/model-fit-per-segment) is the per-model token/cost
    breakdown behind that same total -- `{model: {input, output, cache_read, cache_write,
    cost_usd_micros}}`, summing to `cost_usd_micros` by construction."""
    if not events:
        return []

    boundaries = segment_boundaries(all_beats, config.segment_pause_threshold_s)
    session_start = events[0].get("event_ts")
    cuts = sorted({session_start, *boundaries} - {None})
    ranges = [(cuts[i], cuts[i + 1] if i + 1 < len(cuts) else None) for i in range(len(cuts))]

    # Claude Code splits one API response into one `assistant_message` per content block
    # (thinking/text/tool_use), and every one of them repeats the *whole response's* usage
    # (`usage_scope: "response"`). Pricing each record billed a response 2-3x. One response is
    # priced once: on its first-seen record, the same selector the graph's
    # `_collapse_assistants` uses. Session-wide, not per segment, so a response whose blocks
    # straddle a segment boundary still counts exactly once (in the segment its first block
    # opened). A record with no `message_id` is its own response.
    priced_message_ids: set[str] = set()

    out: list[tuple[dict, list[dict]]] = []
    for t0, t1 in ranges:
        def _in_range(ts, t0=t0, t1=t1):
            return ts is not None and ts >= t0 and (t1 is None or ts < t1)

        seg_events = [e for e in events if _in_range(e.get("event_ts"))]
        if not seg_events:
            continue

        turns_in_span = sum(1 for t in turns_list if _in_range(t.get("started")))
        file_paths = {e["payload"].get("path") for e in seg_events if e["event_type"] == "file_change"}
        beat_tally: dict[str, int] = {}
        for b in all_beats:
            if _in_range(b.get("t0")):
                beat_tally[b["kind"]] = beat_tally.get(b["kind"], 0) + 1

        carry_micros = 0
        cost_micros = 0
        # X4/model-fit-per-segment: the per-message model was already being read to price this
        # segment's total -- kept here too, per model, so a segment run partly on Opus and
        # partly on Sonnet can be attributed instead of only the session's one dominant model
        # (D1 context-curve/reasoning-rollup precedent: keep the per-turn series already
        # computed, don't throw it away). `models` mirrors `session_info.models`'s own
        # `{input, output, cache_read, cache_write}` bucket shape (`dominant_model` reads
        # either), plus `cost_usd_micros` since a segment (unlike the session) has no other
        # per-model cost source to fall back on.
        models: dict[str, dict] = {}
        priced_complete = pricing is not None
        for e in seg_events:
            if e["event_type"] != "assistant_message":
                continue
            if not priced_complete:
                break
            payload = e["payload"]
            message_id = payload.get("message_id")
            if isinstance(message_id, str):
                if message_id in priced_message_ids:
                    continue
                priced_message_ids.add(message_id)
            ts = e.get("event_ts")
            model = payload.get("model")
            cache_read = payload.get("cache_read_tokens") or 0
            full_tokens = {
                "input": payload.get("input_tokens") or 0, "output": payload.get("output_tokens") or 0,
                "cache_read": cache_read, "cache_write": payload.get("cache_write_tokens") or 0,
            }
            if not any(full_tokens.values()):
                # Nothing billed -- e.g. Claude Code's zero-usage `<synthetic>` marker messages, whose
                # "model" has no price. Skipped rather than priced, the same way `_price_channel`
                # skips a zero count, so a free record never voids the whole segment's total.
                continue
            full_price = pricing.price_tokens(model, full_tokens, ts,
                                            cache_write_detail=payload.get("cache_write_detail"))
            carry_price = (pricing.price_tokens(model, {"cache_read": cache_read}, ts)
                          if cache_read else Decimal(0))
            if full_price is None or carry_price is None:
                priced_complete = False
                break
            msg_cost_micros = price_to_micros(full_price) or 0
            cost_micros += msg_cost_micros
            carry_micros += price_to_micros(carry_price) or 0
            if model:
                bucket = models.setdefault(model, {"input": 0, "output": 0, "cache_read": 0,
                                                    "cache_write": 0, "cost_usd_micros": 0})
                bucket["input"] += full_tokens["input"]
                bucket["output"] += full_tokens["output"]
                bucket["cache_read"] += full_tokens["cache_read"]
                bucket["cache_write"] += full_tokens["cache_write"]
                bucket["cost_usd_micros"] += msg_cost_micros

        out.append(({
            "index": len(out), "t0": seg_events[0].get("event_ts"), "t1": seg_events[-1].get("event_ts"),
            "turns": turns_in_span, "files": len(file_paths), "beats": beat_tally,
            "carry_usd_micros": carry_micros if priced_complete else None,
            "cost_usd_micros": cost_micros if priced_complete else None,
            "models": models if priced_complete else None,
        }, seg_events))
    return out


def _commit_sha_from_tool_result(event: dict) -> str | None:
    """The vendor's verified `gitOperation.commit.sha` -- a real committed hash, never a regex
    re-guess at the command text. It rides the tool RESULT (a `tool_result` event's verbatim
    `payload.raw.toolUseResult`), NOT the tool_call, and the normalizer keeps the result under
    `raw` without flattening it. `toolUseResult` is a plain string for some tools, so guard every
    step of the descent."""
    if event.get("event_type") != "tool_result":
        return None
    tur = ((event.get("payload") or {}).get("raw") or {}).get("toolUseResult")
    if not isinstance(tur, dict):
        return None
    commit = (tur.get("gitOperation") or {}).get("commit")
    if not isinstance(commit, dict):
        return None
    return commit.get("sha")


def _wrap_up(events: list[dict]) -> list[dict]:
    """One beat per successful commit (the vendor's own verified `gitOperation.commit` on a tool
    RESULT -- see `_commit_sha_from_tool_result`) or `pr_link` event. Every occurrence, not just
    the session's final one: Task-Classification-and-Analysis.md §4 calls a commit/PR push a
    boundary in its own right, independent of position -- STORY §4's narrower "near the end"
    framing was for the (now cut) narrator line."""
    beats = []
    for e in events:
        sha = _commit_sha_from_tool_result(e)
        if sha is not None:
            ts = e.get("event_ts")
            beats.append({
                "kind": "wrap_up", "t0": ts, "t1": ts,
                "evidence": {"sha": sha}, "confidence": "fact",
            })
        elif e["event_type"] == "pr_link":
            ts = e.get("event_ts")
            beats.append({
                "kind": "wrap_up", "t0": ts, "t1": ts,
                "evidence": {"pr_number": e["payload"].get("pr_number")}, "confidence": "fact",
            })
    return beats


def interrupted_cost_rollup(events: list[dict]) -> dict:
    """info.interrupted (R6.3): {events: [{ts, tokens}], count, total_tokens}.

    "Input paid, output discarded": when a user interrupts (the same sentinel the
    `interruption` beat and `turns.py`'s own interrupt count key on), the assistant_message
    immediately before it was billed in full -- input, output, any cache activity -- for a
    response the user then threw away. `tokens` is that message's own total
    (`metrics.event_tokens`), not priced (R6 stays token-only, insight 18's convention, not
    R2/R3's per-request pricing).

    Lives here, not `metrics.py`, purely to avoid a circular import: `turns.py` already
    imports `metrics`, so `metrics` importing `turns.INTERRUPT_SENTINEL` back would cycle;
    `beats.py` sits downstream of both and already owns the `interruption` beat this pairs
    with.

    Also the flag other rollups can key on later so an interrupted turn's numbers -- an
    artificially short `output_tokens`, in particular -- don't get read as a representative
    turn in some future average (the plan's own "does not distort other measures" note); no
    existing rollup currently averages over turn shape, so there is nothing to retrofit yet."""
    out = []
    prev_assistant = None
    for e in events:
        if e["event_type"] == "assistant_message":
            prev_assistant = e
            continue
        if e["event_type"] != "user_message":
            continue
        text = e["payload"].get("text")
        if not (isinstance(text, str) and text.strip() == INTERRUPT_SENTINEL):
            continue
        if prev_assistant is None:
            continue
        out.append({"ts": e.get("event_ts"), "tokens": event_tokens(prev_assistant)})

    return {"events": out, "count": len(out), "total_tokens": sum(e["tokens"] for e in out)}
