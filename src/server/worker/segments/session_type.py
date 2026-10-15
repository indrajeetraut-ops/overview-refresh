"""Session/segment type classification -- Tier 0, weighted-evidence model (R5 overhaul).
Taxonomy from Task-Classification-and-Analysis.md §2/§6; not that doc's aspirational
config-driven predicate engine (R5.4's "no Tier 1" restraint extended to the rule engine
itself: many signals still don't need one, they need to be *combined*, not ordered).

The original shape here was a fixed six-signal, first-match-wins `if`/`elif` chain over one
segment's own event slice. Two real failure modes motivated the rewrite: (1) a segment with no
`file_change` events of its own had nothing left to fall back on but a raw read count, so a
diagnostic read burst (checking a fixture right after a failing test) looked identical to
open-ended research; (2) one matching rule won the whole segment outright, with no way to
combine partial evidence from a mixed segment.

Two-tier decision now:

**Tier A -- fact short-circuits**, unchanged from the original, confidence 1.0: an explicit
`sub_agent_spawn` of a known type, or an explicit review slash command. These are vendor-
reported/user-issued facts, not inferences.

**Tier B -- weighted evidence**, replacing every other rule: each segment gets a score per
label from every cue below, and the argmax wins. Confidence is `margin_ratio x sufficiency`
(`_resolve_scores`), not a hand-picked constant per rule -- a segment with strong, uncontested,
multi-signal evidence reports high confidence; a segment with one weak signal and nothing else
reports low confidence, honestly.

Cues, beyond the original six (read/edit ratio, path/extension mix, plan-mode markers, slash
commands, subagent types, web-tool usage):

  - **Conventional-commit prefixes** (`fix:`/`feat:`/`docs:`/`chore:`/...) parsed straight out
    of a `git commit -m "..."` command's own text -- the developer's explicit self-label, the
    single highest-weight cue since it isn't inferred at all.
  - **User-message intent keywords** (fix/bug/crash -> BUG_FIX, implement/add/build -> IMPLEMENT,
    design/architecture -> DESIGN, etc.), regexed against the segment's own `user_message.text`
    -- the same "read the text, persist only the match outcome" discipline `practices.py`'s
    `at_refs` already established.
  - **Diagnostic vs. exploratory reads**: a read/web-tool cluster within `diagnostic_read_window_s`
    of a failing check counts toward BUG_FIX; within that window of a build/edit (no fail
    nearby) counts toward IMPLEMENT; neither counts toward COMPREHEND. This is the direct fix
    for failure mode (1) above.
  - **Check-beat correlation by matched verifier pattern**, not "any fail then any pass" --
    replaces `_has_fail_then_pass_check`; a failed `pytest` run followed by an unrelated
    passed `eslint` run no longer reads as a fix.
  - **`revision_loop`/`build` beat evidence**, reused from `beats.py` rather than re-derived.
  - **Continuous docs-fraction scoring** (DESIGN vs IMPLEMENT), not a hard >=0.5 cliff.
  - **Widened OPS scope**: package-manager installs, Docker/`kubectl`, and CI/infra paths, on
    top of the original git-verb count -- the taxonomy's own description ("environment setup,
    CI wrangling") was never actually covered by the original git-only check.

A segment with no `file_change` events of its own and low confidence also gets a second look
in `classify_segments` (the session-wide entry point): if both neighboring segments, within
`neighbor_gap_threshold_s`, share a label, this one inherits it at damped confidence rather
than defaulting to MISC or a weak COMPREHEND -- the safety net for when a loop got fragmented
across a segment boundary rather than misjudged within one.

MISC is still a real label (R5.3) -- total evidence below `min_evidence_score` reports MISC
at confidence 1.0 ("certain we don't know"), never a guess dressed up as one of the other
seven.

Explicitly out of scope: Bash-based file edits (`sed -i`, a heredoc, a formatter invoked via
Bash) produce no `file_change` event at all (the normalizer only derives one from
Edit/Write/MultiEdit) -- a session edited that way looks file-change-free to every cue here
that depends on `file_changes`, no matter how well-weighted the scoring is. Known, deferred;
see the classification-overhaul design note for the diagnosis.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ._util import git_command_counts
from .config import SessionTypeConfig

_READ_TOOLS = {"Read", "Grep", "Glob", "NotebookRead"}
_WEB_TOOLS = {"WebFetch", "WebSearch"}

_SCORED_LABELS = ("BUG_FIX", "IMPLEMENT", "DESIGN", "OPS", "PLAN", "REVIEW", "COMPREHEND")

_KEYWORD_FIELD_BY_LABEL = {
    "BUG_FIX": "bug_fix_keywords", "IMPLEMENT": "implement_keywords", "DESIGN": "design_keywords",
    "PLAN": "plan_keywords", "REVIEW": "review_keywords", "COMPREHEND": "comprehend_keywords",
}

_GIT_COMMIT_RE = re.compile(r"\bgit\s+commit\b")
_COMMIT_MSG_RE = re.compile(r"""-m\s+(['"])(.*?)\1""")
_CONVENTIONAL_PREFIX_RE = re.compile(r"^(\w+)(?:\([^)]*\))?:\s")

# Weights (module-level, not config -- same posture as the taxonomy/rule-shape living in code
# per SessionTypeConfig's own docstring: these are the "how much does this cue matter" knobs,
# analytically chosen, tunable by editing this file, not meant for per-org config the way
# keyword lists and path prefixes are).
_W_COMMIT_PREFIX = 3.0
_W_KEYWORD = 1.2
_W_KEYWORD_CAP = 2.4
_W_CHECK_FAIL_THEN_PASS = 3.0
_W_REVISION_LOOP = 2.5  # must clearly outweigh a single file_change's own baseline IMPLEMENT score
_W_BUILD_PER_FILE = 0.6
_W_BUILD_CAP = 3.0
_W_DIAGNOSTIC_READ = 1.0
_W_EXPLORATORY_READ = 1.0
_W_DOCS_FRACTION = 2.5
_W_DOCS_BONUS = 1.0
_W_IMPLEMENT_FRACTION = 1.5
_W_IMPLEMENT_PER_FILE = 0.4
_W_IMPLEMENT_FILE_CAP = 2.0
_W_OPS_GIT_PER_COMMAND = 1.2  # one bare git command with no edits must clear min_evidence_score on its own
_W_OPS_GIT_CAP = 2.4
_W_OPS_COMMAND = 1.5
_W_OPS_COMMAND_CAP = 3.0
_W_OPS_PATH = 3.0  # must clearly outweigh a single file_change's own baseline IMPLEMENT score
_W_OPS_PATH_CAP = 3.0
_W_EXITPLAN_NO_EDITS = 2.0
_W_EXITPLAN_WITH_EDITS = 0.3

_CONFIDENCE_EPSILON = 0.15


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fact_tier_label(segment_events: list[dict], review_agent_types: list[str],
                     review_slash_commands: list[str]) -> str | None:
    """Tier A: vendor-reported/user-issued facts, never scored -- an explicit subagent type or
    an explicit review slash command outrank any amount of inferred evidence. Unchanged from
    the original rule order (first matching spawn wins; same as before)."""
    review_types = {t.lower() for t in review_agent_types}
    for e in segment_events:
        if e["event_type"] != "sub_agent_spawn":
            continue
        agent_type = (e["payload"].get("agent_type") or "").lower()
        if agent_type == "explore":
            return "COMPREHEND"
        if agent_type == "plan":
            return "PLAN"
        if agent_type in review_types:
            return "REVIEW"

    review_slash = {c.lower() for c in review_slash_commands}
    for e in segment_events:
        if e["event_type"] != "user_message":
            continue
        cmd = e["payload"].get("slash_command")
        if isinstance(cmd, str) and cmd.lower() in review_slash:
            return "REVIEW"

    return None


def _commit_prefix_scores(segment_events: list[dict], commit_prefix_labels: dict[str, str]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for e in segment_events:
        if e["event_type"] != "tool_call" or e["payload"].get("tool_name") != "Bash":
            continue
        command = (e["payload"].get("input") or {}).get("command")
        if not isinstance(command, str) or not _GIT_COMMIT_RE.search(command):
            continue
        m = _COMMIT_MSG_RE.search(command)
        if not m:
            continue
        prefix_m = _CONVENTIONAL_PREFIX_RE.match(m.group(2))
        if not prefix_m:
            continue
        label = commit_prefix_labels.get(prefix_m.group(1).lower())
        if label:
            scores[label] = scores.get(label, 0.0) + _W_COMMIT_PREFIX
    return scores


def _keyword_scores(segment_events: list[dict], config: SessionTypeConfig) -> dict[str, float]:
    texts = [
        e["payload"].get("text") for e in segment_events
        if e["event_type"] == "user_message" and isinstance(e["payload"].get("text"), str)
    ]
    if not texts:
        return {}
    scores: dict[str, float] = {}
    for label, field in _KEYWORD_FIELD_BY_LABEL.items():
        patterns = [re.compile(p, re.IGNORECASE) for p in getattr(config, field)]
        hits = sum(1 for text in texts for p in patterns if p.search(text))
        if hits:
            scores[label] = min(_W_KEYWORD_CAP, hits * _W_KEYWORD)
    return scores


def _beat_scores(segment_beats: list[dict]) -> dict[str, float]:
    scores: dict[str, float] = {}
    if any(b["kind"] == "revision_loop" for b in segment_beats):
        scores["BUG_FIX"] = scores.get("BUG_FIX", 0.0) + _W_REVISION_LOOP
    for b in segment_beats:
        if b["kind"] != "build":
            continue
        files = (b.get("evidence") or {}).get("files") or 0
        scores["IMPLEMENT"] = scores.get("IMPLEMENT", 0.0) + min(_W_BUILD_CAP, files * _W_BUILD_PER_FILE)
    return scores


def _is_doc_change(e: dict, config: SessionTypeConfig) -> bool:
    payload = e["payload"]
    path = payload.get("path") or ""
    ext = payload.get("extension") or Path(path).suffix
    return ext in config.doc_extensions or any(path.startswith(p) for p in config.doc_path_prefixes)


def _docs_and_implement_scores(file_changes: list[dict], config: SessionTypeConfig) -> dict[str, float]:
    if not file_changes:
        return {}
    doc_count = sum(1 for e in file_changes if _is_doc_change(e, config))
    fraction = doc_count / len(file_changes)
    scores = {
        "DESIGN": fraction * _W_DOCS_FRACTION
                 + (_W_DOCS_BONUS if fraction >= config.docs_dominant_fraction else 0.0),
        "IMPLEMENT": (1 - fraction) * _W_IMPLEMENT_FRACTION
                    + min(_W_IMPLEMENT_FILE_CAP, len(file_changes) * _W_IMPLEMENT_PER_FILE),
    }
    return {k: v for k, v in scores.items() if v > 0}


def _check_fail_then_pass_same_pattern(segment_events: list[dict], verifier_patterns: list[str]) -> bool:
    """R5 overhaul: replaces the old any-fail-then-any-pass check. Correlates by *which*
    verifier pattern matched -- the same allowlist Check beats and `practices.py`'s own
    verifier detection already share -- so a failed `pytest` run followed by an unrelated
    passed `eslint` run no longer counts as a fix."""
    patterns = [re.compile(p, re.IGNORECASE) for p in verifier_patterns]
    failed_patterns: set[int] = set()
    for e in segment_events:
        if e["event_type"] != "tool_call" or e["payload"].get("tool_name") != "Bash":
            continue
        command = (e["payload"].get("input") or {}).get("command")
        if not isinstance(command, str):
            continue
        for i, p in enumerate(patterns):
            if not p.search(command):
                continue
            if e["payload"].get("exit_status") == 0:
                if i in failed_patterns:
                    return True
            else:
                failed_patterns.add(i)
            break
    return False


def _read_runs(segment_events: list[dict]) -> list[list[dict]]:
    """Maximal runs of read/web tool_calls, broken by any other event -- the same "run" shape
    `beats.py`'s own `_survey` detects, kept local since this module depends on beats.py's
    *output* (`segment_beats`) elsewhere, not its detection internals."""
    runs: list[list[dict]] = []
    current: list[dict] = []
    for e in segment_events:
        if e["event_type"] == "tool_call" and e["payload"].get("tool_name") in (_READ_TOOLS | _WEB_TOOLS):
            current.append(e)
            continue
        if current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _near_any(start: datetime, end: datetime, anchors: list[datetime], window_s: float) -> bool:
    for a in anchors:
        if abs((a - start).total_seconds()) <= window_s or abs((a - end).total_seconds()) <= window_s:
            return True
    return False


def _classify_read_clusters(segment_events: list[dict], segment_beats: list[dict],
                            window_s: float, min_reads: int) -> dict[str, int]:
    """A read/web-tool cluster (>= `min_reads` long, same size floor the original read-count
    rule used) is *diagnostic* when a failing check or an edit/build sits within `window_s` of
    either end of it, *exploratory* otherwise. This is the direct fix for the reported failure
    mode: reading a fixture right after a failing test now scores toward BUG_FIX instead of
    reading identically to open-ended research."""
    out = {"diagnostic_fail": 0, "diagnostic_edit": 0, "exploratory": 0}
    fail_anchors = [
        ts for b in segment_beats if b["kind"] == "check" and not (b.get("evidence") or {}).get("ok", True)
        and (ts := _parse_ts(b.get("t0"))) is not None
    ]
    edit_anchors = [
        ts for b in segment_beats if b["kind"] in ("build", "revision_loop")
        and (ts := _parse_ts(b.get("t0"))) is not None
    ] + [
        ts for e in segment_events if e["event_type"] == "file_change"
        and (ts := _parse_ts(e.get("event_ts"))) is not None
    ]

    for run in _read_runs(segment_events):
        if len(run) < min_reads:
            continue
        start = _parse_ts(run[0].get("event_ts"))
        end = _parse_ts(run[-1].get("event_ts"))
        if start is None or end is None:
            out["exploratory"] += 1
        elif _near_any(start, end, fail_anchors, window_s):
            out["diagnostic_fail"] += 1
        elif _near_any(start, end, edit_anchors, window_s):
            out["diagnostic_edit"] += 1
        else:
            out["exploratory"] += 1
    return out


def _ops_score(segment_events: list[dict], file_changes: list[dict], config: SessionTypeConfig,
               git_commands: list[str]) -> float:
    score = 0.0
    git_total = sum(git_command_counts(segment_events, git_commands).values())
    if git_total and not file_changes:
        score += min(_W_OPS_GIT_CAP, git_total * _W_OPS_GIT_PER_COMMAND)

    ops_patterns = [re.compile(p, re.IGNORECASE) for p in config.ops_command_patterns]
    ops_cmd_hits = 0
    for e in segment_events:
        if e["event_type"] != "tool_call" or e["payload"].get("tool_name") != "Bash":
            continue
        command = (e["payload"].get("input") or {}).get("command")
        if isinstance(command, str) and any(p.search(command) for p in ops_patterns):
            ops_cmd_hits += 1
    score += min(_W_OPS_COMMAND_CAP, ops_cmd_hits * _W_OPS_COMMAND)

    ops_path_hits = sum(
        1 for e in file_changes
        if any((e["payload"].get("path") or "").startswith(p) for p in config.ops_path_prefixes)
    )
    score += min(_W_OPS_PATH_CAP, ops_path_hits * _W_OPS_PATH)
    return score


def _plan_score(segment_events: list[dict], file_changes: list[dict]) -> float:
    exitplan = any(
        e["event_type"] == "tool_call" and e["payload"].get("tool_name") == "ExitPlanMode"
        for e in segment_events
    )
    if not exitplan:
        return 0.0
    return _W_EXITPLAN_NO_EDITS if not file_changes else _W_EXITPLAN_WITH_EDITS


def _score_segment(segment_events: list[dict], segment_beats: list[dict], config: SessionTypeConfig,
                   verifier_patterns: list[str], git_commands: list[str]) -> dict[str, float]:
    file_changes = [e for e in segment_events if e["event_type"] == "file_change"]
    scores: dict[str, float] = {}

    def add(other: dict[str, float]) -> None:
        for k, v in other.items():
            scores[k] = scores.get(k, 0.0) + v

    add(_commit_prefix_scores(segment_events, config.commit_prefix_labels))
    add(_keyword_scores(segment_events, config))
    add(_beat_scores(segment_beats))
    add(_docs_and_implement_scores(file_changes, config))

    if _check_fail_then_pass_same_pattern(segment_events, verifier_patterns):
        scores["BUG_FIX"] = scores.get("BUG_FIX", 0.0) + _W_CHECK_FAIL_THEN_PASS

    clusters = _classify_read_clusters(segment_events, segment_beats,
                                       config.diagnostic_read_window_s, config.misc_min_reads)
    if clusters["diagnostic_fail"]:
        scores["BUG_FIX"] = scores.get("BUG_FIX", 0.0) + clusters["diagnostic_fail"] * _W_DIAGNOSTIC_READ
    if clusters["diagnostic_edit"]:
        scores["IMPLEMENT"] = scores.get("IMPLEMENT", 0.0) + clusters["diagnostic_edit"] * _W_DIAGNOSTIC_READ
    if clusters["exploratory"]:
        scores["COMPREHEND"] = scores.get("COMPREHEND", 0.0) + clusters["exploratory"] * _W_EXPLORATORY_READ

    ops = _ops_score(segment_events, file_changes, config, git_commands)
    if ops:
        scores["OPS"] = scores.get("OPS", 0.0) + ops

    plan = _plan_score(segment_events, file_changes)
    if plan:
        scores["PLAN"] = scores.get("PLAN", 0.0) + plan

    return scores


def _resolve_scores(scores: dict[str, float], config: SessionTypeConfig) -> dict:
    """Confidence = margin_ratio x sufficiency: a segment with strong, uncontested, multi-
    signal evidence reports high confidence; a segment with one weak signal and nothing else
    reports low confidence honestly, instead of the original's flat per-rule constant."""
    total = sum(scores.values())
    if not scores or total < config.min_evidence_score:
        return {"label": "MISC", "confidence": 1.0}
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    winner_label, winner_score = ranked[0]
    runner_up_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin_ratio = winner_score / (winner_score + runner_up_score + _CONFIDENCE_EPSILON)
    sufficiency = min(1.0, total / config.confident_evidence_score)
    confidence = round(min(0.95, margin_ratio * sufficiency), 2)
    return {"label": winner_label, "confidence": confidence}


def classify_segment(segment_events: list[dict], segment_beats: list[dict], config: SessionTypeConfig,
                     review_agent_types: list[str], verifier_patterns: list[str],
                     git_commands: list[str]) -> dict:
    """`{label, confidence}` for one R4 segment's own event slice (R5.2 -- segments, not whole
    sessions, are the classified unit). `segment_beats` is this segment's own slice of the
    session's beats (`beats.beats_in_range`), threaded in by `driver.py` so BUG_FIX/IMPLEMENT
    scoring can read `check`/`revision_loop`/`build` evidence without re-deriving it.
    `review_agent_types`/`verifier_patterns`/`git_commands` are the matching `FoldConfig`
    fields `practices.py`/`beats.py`/`metrics.git_command_counts` already use -- one definition
    of "review subagent"/"this verified"/"this is a git op," not a second one drifting here."""
    fact = _fact_tier_label(segment_events, review_agent_types, config.review_slash_commands)
    if fact is not None:
        return {"label": fact, "confidence": 1.0}
    scores = _score_segment(segment_events, segment_beats, config, verifier_patterns, git_commands)
    return _resolve_scores(scores, config)


def _neighbor_label(segments: list[tuple[dict, list[dict], list[dict]]], results: list[dict],
                    i: int, step: int, config: SessionTypeConfig) -> str | None:
    j = i + step
    if j < 0 or j >= len(segments):
        return None
    this_edge = segments[i][0]["t0"] if step < 0 else segments[i][0]["t1"]
    other_edge = segments[j][0]["t1"] if step < 0 else segments[j][0]["t0"]
    this_ts, other_ts = _parse_ts(this_edge), _parse_ts(other_edge)
    if this_ts is None or other_ts is None:
        return None
    if abs((this_ts - other_ts).total_seconds()) > config.neighbor_gap_threshold_s:
        return None
    return results[j]["label"]


def classify_segments(segments: list[tuple[dict, list[dict], list[dict]]], config: SessionTypeConfig,
                      review_agent_types: list[str], verifier_patterns: list[str],
                      git_commands: list[str]) -> list[dict]:
    """R5's actual `driver.py` entry point (replaces the old per-segment loop). `segments` is
    `[(segment, segment_events, segment_beats)]`, `build_segments`'s own order.

    Classifies every segment independently, then revises the ones with no `file_change`
    evidence of their own and confidence below `neighbor_inherit_confidence_floor`: if both
    the previous and next segment, within `neighbor_gap_threshold_s`, agree on a label, this
    one inherits it at damped confidence (`neighbor_inherit_confidence_factor`) instead of
    defaulting to MISC or a weak COMPREHEND. The safety net for a loop fragmented across a
    segment boundary (a compaction or a long pause splitting an edit-verify-fix arc) -- the
    scoring model above should already resolve the *un*-fragmented case on its own."""
    results = [
        classify_segment(events, beats, config, review_agent_types, verifier_patterns, git_commands)
        for _, events, beats in segments
    ]
    for i, (_segment, events, _beats) in enumerate(segments):
        if any(e["event_type"] == "file_change" for e in events):
            continue
        # MISC is deliberately confidence 1.0 ("certain we don't know," R5.3) -- a plain
        # `confidence < floor` check alone would never catch it, since MISC always clears any
        # floor below 1.0. A segment is "weak" here when it's MISC outright *or* when a scored
        # label won with low confidence, not just the latter.
        weak = results[i]["label"] == "MISC" or results[i]["confidence"] < config.neighbor_inherit_confidence_floor
        if not weak:
            continue
        prev_label = _neighbor_label(segments, results, i, -1, config)
        next_label = _neighbor_label(segments, results, i, 1, config)
        if prev_label is not None and prev_label == next_label:
            damped = min(results[i - 1]["confidence"], results[i + 1]["confidence"]) * config.neighbor_inherit_confidence_factor
            results[i] = {"label": prev_label, "confidence": round(damped, 2)}
    return results
