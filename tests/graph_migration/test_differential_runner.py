"""Step-0 CI test (plan §7): freeze the `EconFold` `session_info` baseline for the *sanitized*
recorded fixtures, and self-test the differential runner.

The committed golden here uses only `tests/fixtures/normalizer/recorded/*` (sanitized, already in
the repo) -- never the developer's private `~/.claude` corpus. The full local-corpus baseline is a
gitignored dev artifact produced by `cli_baseline.py`.

Regenerate the golden after an *intended* extraction change (then eyeball the git diff):

    GRAPH_ORACLE_REGEN=1 pytest tests/graph_migration/test_differential_runner.py
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from tests.graph_migration.differential import (
    AllowRule,
    diff,
    regressions,
)
_GOLDEN = Path(__file__).resolve().parent / "baseline_fixtures.json"


def _golden() -> dict:
    return json.loads(_GOLDEN.read_text(encoding="utf-8"))


def test_frozen_baseline_covers_step0_categories():
    """The frozen baseline (github-normalizer + EconFold, 'today's dashboard behavior') must exercise
    a single session and a multi-subagent rollup, or the A/B isn't proving what it needs."""
    g = _golden()
    assert set(g["session-regular"]) == {"session-regular"}  # single: master only
    sub = g["session-subagents"]["session-subagents"]
    assert len(g["session-subagents"]) >= 3  # master + >=2 children
    assert len(sub["subagents"]) >= 2
    assert sub["info"]["inclusive_tokens"] >= sub["tokens_total"]


# --- runner self-tests: it flags a real diff and honours the allowlist -------------------------

def _baseline_family() -> dict:
    return _golden()["session-subagents"]["session-subagents"]


def test_runner_clean_on_identical():
    base = _baseline_family()
    assert regressions(base, copy.deepcopy(base)) == []


def test_runner_flags_injected_non_allowlisted_diff():
    base = _baseline_family()
    cand = copy.deepcopy(base)
    cand["subagent_count"] = base["subagent_count"] + 1  # not on any allowlist
    problems = regressions(base, cand)
    assert [p.path for p in problems] == ["subagent_count"]
    assert problems[0].kind == "changed"


def test_runner_flags_injected_added_field():
    base = _baseline_family()
    cand = copy.deepcopy(base)
    cand["info"]["a_new_field"] = 1  # a graph-backed field that shouldn't appear
    assert [p.path for p in regressions(base, cand)] == ["info.a_new_field"]


def test_runner_honors_dropped_allowlist():
    """Removing a §4.C-dropped field (info.errors) is NOT a regression."""
    base = _baseline_family()
    cand = copy.deepcopy(base)
    cand["info"].pop("errors", None)
    cand.pop("error_count", None)
    assert regressions(base, cand) == []


def test_runner_honors_token_collapse_allowlist():
    """A value change on a token-collapse (§4.A) path is NOT a regression, but the same-shaped
    change on a non-token path IS -- proving the allowlist is path-scoped, not blanket."""
    base = _baseline_family()
    waived = copy.deepcopy(base)
    waived["tokens_total"] = base["tokens_total"] + 1
    assert regressions(base, waived) == []

    not_waived = copy.deepcopy(base)
    not_waived["compaction_count"] = base["compaction_count"] + 1
    assert [p.path for p in regressions(base, not_waived)] == ["compaction_count"]


def test_allowlist_scoping_does_not_overreach():
    """`models.*` waives token buckets but a custom allowlist must not accidentally waive a
    sibling; a value rule waives only `changed`, never a removal."""
    base = _baseline_family()
    cand = copy.deepcopy(base)
    # drop a whole model entry -> that's a removal, which the _VALUE ("changed"-only) rule must NOT waive
    some_model = next(iter(cand["models"]))
    cand["models"].pop(some_model)
    problems = regressions(base, cand)
    assert problems and all(p.kind == "removed" and p.path.startswith("models.") for p in problems)


def test_allowrule_glob_crosses_dots():
    from tests.graph_migration.differential import is_allowed, Difference

    rule = (AllowRule("info.errors*", frozenset({"removed"}), "test"),)
    assert is_allowed(Difference("info.errors.by_class.rate_limit", "removed"), rule)
