"""The per-field `session_info` differential runner (plan §7 Layer 3).

Deep-diff two serialized `session_info` snapshots by exact field path (list indices and dynamic
dict keys included, e.g. `info.segments[2].label`, `models.claude-opus-4-8.input`) and classify
every difference as `removed` / `added` / `changed`. An **allowlist** of intended differences waives
specific (path-glob, kind) pairs, each with a written reason -- so the Step-3+ graph-backed extractor
can be proven behavior-preserving *except where we chose to change it*. Any difference not waived is a
regression.

This is the tool; the A/B comparison (legacy fold vs graph-backed) runs in Step 3+. In Step 0 the
runner is self-tested: it must flag an injected diff and ignore an allowlisted one.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass


@dataclass(frozen=True)
class AllowRule:
    """Waive differences at paths matching `glob` whose kind is in `kinds`. `glob` is fnmatch over
    the full dotted path, and `*` crosses dots -- so `info.errors*` covers the whole subtree."""

    glob: str
    kinds: frozenset[str]
    reason: str


@dataclass(frozen=True)
class Difference:
    path: str
    kind: str  # "removed" | "added" | "changed"
    baseline: object = None
    candidate: object = None

    def describe(self) -> str:
        if self.kind == "removed":
            return f"{self.path}: removed (baseline={self.baseline!r})"
        if self.kind == "added":
            return f"{self.path}: added (candidate={self.candidate!r})"
        return f"{self.path}: changed ({self.baseline!r} -> {self.candidate!r})"


# --- the decided allowlist (companion doc §7) -------------------------------
# FIRM: fields dropped by explicit decision -- absent/zeroed in the graph-backed output by design.
_DROPPED = frozenset({"removed", "added", "changed"})
# PROVISIONAL: §4.A token-collapse changes the VALUE of token-bearing fields (the graph collapses
# assistant records by message_id; fold sums per-record). Fields stay present; only values move.
# Seeded with the clearly-affected paths; refine the exact set in Step 3 against observed diffs.
_VALUE = frozenset({"changed"})

DEFAULT_ALLOWLIST: tuple[AllowRule, ...] = (
    AllowRule("info.errors", _DROPPED, "§4.C api_error dropped from parity"),
    AllowRule("info.errors.*", _DROPPED, "§4.C api_error dropped (by_class/retry_count/retried_tokens_est)"),
    AllowRule("error_count", _DROPPED, "§4.C api_error dropped -> error_count not produced"),
    AllowRule("info.cache.writes_5m", _DROPPED, "§4.D cache-TTL split dropped (totals kept)"),
    AllowRule("info.cache.writes_1h", _DROPPED, "§4.D cache-TTL split dropped (totals kept)"),
    # §4.A token-collapse (PROVISIONAL — expand in Step 3 once real A/B diffs are visible):
    AllowRule("models.*", _VALUE, "§4.A token collapse: per-model token buckets"),
    AllowRule("tokens_total", _VALUE, "§4.A token collapse"),
    AllowRule("info.tokens_total", _VALUE, "§4.A token collapse"),
    AllowRule("info.inclusive_tokens", _VALUE, "§4.A token collapse (rollup)"),
    AllowRule("subagents.*.tokens", _VALUE, "§4.A token collapse (github over-counted per-record; graph is correct)"),
    AllowRule("subagents.*.inclusive_tokens", _VALUE, "§4.A token collapse"),
    AllowRule("subagents.*.duration_ms", _VALUE, "graph node-span vs EconFold event-span duration (minor timing diff)"),
    # bytes_read: the integ normalizer sets bytes_read=None on purpose; the graph tracks read size
    # as num_lines/total_lines (result.file), a finer signal. So byte totals go to 0 -- an intended
    # difference (a lines-based read metric can replace it later). Applies to tools, mcp, and the
    # §4.B read_then_discard waste detector.
    # bytes_read: RESTORED 2026-08-30 -- carried from the tool_call payload onto the tool node
    # (builder _merge_tool), so tool/mcp byte totals match the baseline. Ledger #4 closed.
    # lines_added/lines_removed: integ's normalizer keeps only hunk SIZES (newLines/oldLines) and
    # drops the diff body the +/- counts come from, so these go to 0. HEADLINE code-churn metric --
    # flagged for a decision: enrich the normalizer to restore (port github's _lines_added_removed)
    # vs accept the loss. Provisionally allowlisted so the rest of `files` can be validated.
    # lines_added/lines_removed: RESTORED via normalizer enrich (2026-08-30) -- exact +/- counts now
    # carried on file entries, so these match the baseline (no allowlist needed). Ledger #5 closed.
    # created: github marked every Write as created=True; integ distinguishes a Write that created a
    # file from one that overwrote an existing file (_refine_write_operations), so an overwrite is
    # created=False. An accuracy improvement (like the token collapse), not a regression.
    AllowRule("*.created", _VALUE, "integ distinguishes Write-create from Write-overwrite (incl. nested subagents.*.files)"),
    # turns: the graph segments by promptId runs, EconFold by text-based human-message detection --
    # different turn counts and a shifted array, so the whole turns structure is graph-native and
    # accepted wholesale (ledger #7). Also covers per-turn token collapse (§4.A), message_count/ended
    # (graph-native definitions). turn_count/long_turn_count follow.
    AllowRule("turns[].*", _DROPPED, "graph-native turn model (promptId); differs from EconFold text-based turns"),
    AllowRule("turns[].subagent_session_ids[]", _DROPPED, "graph-native turn model: shifted array"),
    AllowRule("turn_count", _VALUE, "graph-native turn count (promptId segmentation)"),
    AllowRule("long_turn_count", _VALUE, "graph-native turn model"),
    # info dict -- §4.B heuristics. LANDED (2026-08-31): interrupted + read_then_discard are byte-
    # identical (no waiver); carry + cache.hit_ratio are produced but differ by the §4.A token collapse
    # (the graph de-dups per-record assistant messages -> the graph is the CORRECT per-request figure).
    AllowRule("info.carry.*", _VALUE, "§4.A token collapse: per-request carry (graph de-dups per-record assistants; graph is correct)"),
    AllowRule("info.cache.hit_ratio", _VALUE, "§4.A token collapse: cache hit ratio (graph de-dups per-record)"),
    AllowRule("info.cache.decay_events*", _DROPPED, "§4.B decided drop: TTL decay needs per-write cache_write_detail + a TTL model the graph doesn't carry"),
    # info.cache.writes_5m/writes_1h are decided drops covered by their own rules above (§4.D TTL split).
    # resume_cost LANDED (2026-08-31): count/gap_s/context_before/ttl_s/projected are byte-identical; two
    # sub-fields carry already-accepted diffs -- `turn` follows the graph-native turn index (ledger #7)
    # and `reload_tokens` is the §4.A collapse (the per-record fold over-counted).
    AllowRule("info.resume_cost.events[].turn", _VALUE, "graph-native turn index (ledger #7), same offset as turns[]"),
    AllowRule("info.resume_cost.events[].reload_tokens", _VALUE, "§4.A token collapse: cache-write reload sum (graph de-dups per-record)"),
    AllowRule("info.resume_cost.projected.ttl_s", _VALUE, "accepted TTL-coarsening: graph has no per-write cache_write_detail, so resume_cost assumes 1h (cf. cache.writes_5m/decay_events drop)"),
    AllowRule("info.resume_cost.events[].ttl_s", _VALUE, "accepted TTL-coarsening: assumed 1h (no cache_write_detail on the graph)"),
    # practices LANDED (2026-08-31): 14/15 fields byte-identical (root-session only, gated like turns);
    # delegation_ratio = child_tokens/own_tokens is the §4.A collapse (both sides de-duped per-record).
    AllowRule("info.practices.delegation_ratio", _VALUE, "§4.A token collapse: child/own token ratio (graph de-dups per-record)"),
    # story LANDED (2026-08-31): shape + curve token columns byte-identical (root-session only); the
    # curve's t_rel column drifts uniformly by the normalizer's first-event t0 choice (event-timing,
    # cf. ledger #10). Only the t_rel diffs actually fire; the token columns match.
    AllowRule("info.story.curve*", _VALUE, "curve t_rel drifts by the normalizer's t0 selection (event-timing, cf. #10); token columns + shape exact"),
    # segments LANDED (2026-09-01): computed from the graph turn model + event stream via the vendored
    # (behavior-preserving) beat/classification library. Driven by graph turns, the boundaries/counts/
    # labels are GRAPH-NATIVE and differ from the github-normalizer baseline (the beat detectors are
    # normalizer-granularity sensitive: 1->0, 6->5, 31->29 segs) -- accepted wholesale like the turn
    # model itself (ledger #7). segments is now PRESENT; its exact shape is graph-native, not golden.
    AllowRule("info.segments*", _DROPPED, "graph-native segments (beats over graph turns + integ events); differs from the github-normalizer baseline, accepted wholesale like turns[] (ledger #7)"),
    # info scalars that differ by collapse / graph-native definition:
    AllowRule("info.channel_cost.*.tokens", _VALUE, "§4.A token collapse (channel token counts)"),
    AllowRule("info.duration_ms", _VALUE, "graph node-span vs EconFold event-span duration"),
    AllowRule("info.event_count", _VALUE, "integ normalizer emits per-record events (granularity differs)"),
    AllowRule("info.own_event_count", _VALUE, "integ normalizer event granularity differs"),
    AllowRule("info.reasoning_tokens", _DROPPED, "F5: reasoning-token rollup -- new field, no EconFold/baseline equivalent to diff against"),
    AllowRule("info.context_curve*", _DROPPED, "D1-context-curve: per-turn context-size series -- new field, no EconFold/baseline equivalent to diff against"),
    # Recoverable-spend rework (efficiency note §2, 2026-09-16): new priced fields with no baseline
    # equivalent -- cache busts (bucket 2), the recoverable share of a resume reload (bucket 1), and
    # the priced interrupted response (bucket 3). The reload itself is now the resuming turn's first
    # request only, so reload_tokens/usd_micros can also differ from the baseline's windowed sum.
    AllowRule("info.cache_busts*", _DROPPED, "recoverable-spend bucket 2: cache busts -- new field, no baseline equivalent"),
    AllowRule("info.resume_cost.total_recoverable_usd_micros", _DROPPED, "recoverable-spend bucket 1: write-minus-read share -- new field"),
    AllowRule("info.resume_cost.events[].ts", _DROPPED, "reload request timestamp -- new field (rework bucket de-overlap)"),
    AllowRule("info.resume_cost.events[].recoverable_usd_micros", _DROPPED, "recoverable-spend bucket 1: per-reload write-minus-read share -- new field"),
    AllowRule("info.resume_cost.events[].usd_micros", _VALUE, "reload priced on the resuming turn's first request only, not every write in the window"),
    AllowRule("info.resume_cost.total_usd_micros", _VALUE, "reload priced on the resuming turn's first request only, not every write in the window"),
    AllowRule("info.interrupted.total_usd_micros", _DROPPED, "recoverable-spend bucket 3: priced interrupted response -- new field"),
    AllowRule("info.interrupted.events[].usd_micros", _DROPPED, "recoverable-spend bucket 3: priced interrupted response -- new field"),
)


def flatten(value, prefix: str = "") -> dict[str, object]:
    """{leaf-path: value}. Dicts descend by key; lists by index. Leaves are scalars, empty
    containers, or None (an empty dict/list is itself a leaf so its presence/absence is diffable)."""
    out: dict[str, object] = {}
    if isinstance(value, dict) and value:
        for key in value:
            out.update(flatten(value[key], f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list) and value:
        for i, item in enumerate(value):
            out.update(flatten(item, f"{prefix}[{i}]"))
    else:
        out[prefix] = value
    return out


def diff(baseline: dict, candidate: dict) -> list[Difference]:
    """Every leaf-path difference between two serialized session_info snapshots, unfiltered."""
    b, c = flatten(baseline), flatten(candidate)
    problems: list[Difference] = []
    for path in sorted(set(b) - set(c)):
        problems.append(Difference(path, "removed", baseline=b[path]))
    for path in sorted(set(c) - set(b)):
        problems.append(Difference(path, "added", candidate=c[path]))
    for path in sorted(set(b) & set(c)):
        if b[path] != c[path]:
            problems.append(Difference(path, "changed", baseline=b[path], candidate=c[path]))
    return problems


def _normalize_indices(path: str) -> str:
    """`turns[0].subagent_session_ids[1]` -> `turns[].subagent_session_ids[]`, so an allowlist glob
    can waive a whole list position-independently (fnmatch treats `[N]` as a char class otherwise)."""
    import re
    return re.sub(r"\[\d+\]", "[]", path)


def is_allowed(problem: Difference, allowlist: tuple[AllowRule, ...] = DEFAULT_ALLOWLIST) -> bool:
    norm = _normalize_indices(problem.path)
    return any(
        problem.kind in rule.kinds and (fnmatch.fnmatch(problem.path, rule.glob) or fnmatch.fnmatch(norm, rule.glob))
        for rule in allowlist
    )


def regressions(
    baseline: dict, candidate: dict, allowlist: tuple[AllowRule, ...] = DEFAULT_ALLOWLIST
) -> list[Difference]:
    """The differences that are NOT waived by the allowlist -- i.e. the regressions. Empty == the
    candidate is behavior-preserving to within the decided, documented differences."""
    return [p for p in diff(baseline, candidate) if not is_allowed(p, allowlist)]


def format_problems(problems: list[Difference]) -> str:
    return "\n  ".join(p.describe() for p in problems)
