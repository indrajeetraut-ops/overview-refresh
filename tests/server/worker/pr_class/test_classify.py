"""Change-type classification (docs/modules/pr-work-classification.md §5.1, §11's test bar):
golden labels per fixture PR, a `fix:` prefix beating inference, and low confidence correctly
falling to `unknown` rather than a guess."""
from __future__ import annotations

import json
from pathlib import Path

from server.worker.pr_class.classify import (
    classify_change_type,
    combine_change_type,
    tier0_classify,
    tier1_classify,
)
from server.worker.pr_class.config import ArtifactClassConfig, ChangeTypeConfig

FIXTURES = Path("tests/fixtures/github")
ARTIFACT_CLASS = ArtifactClassConfig()
CHANGE_TYPE = ChangeTypeConfig()


def _load_pr(fixture_name: str) -> dict:
    fx = json.loads((FIXTURES / f"{fixture_name}.json").read_text())
    return fx["payload"]["pull_request"] | {
        "info": {}, "reverts_sha": None, "labels": [l["name"] for l in fx["payload"]["pull_request"].get("labels", [])],
        "files": None, "stats": {},
    }


# --------------------------------------------------------------------------------- tier 0

def test_tier0_revert_flag_wins_over_everything():
    pr = {"title": "feat: add x", "labels": [], "info": {"github.is_revert": True}, "reverts_sha": "a" * 40}
    c = tier0_classify(pr)
    assert c.label == "revert" and c.confidence == 1.0


def test_tier0_conventional_prefix():
    for prefix, expected in [
        ("feat: add widget", "feature"), ("fix: null pointer", "fix"),
        ("refactor: simplify", "refactor"), ("docs: update readme", "docs"),
        ("test: add coverage", "test"), ("chore: bump deps", "chore"),
        ("perf: speed up loop", "refactor"), ("ci: fix pipeline", "chore"),
    ]:
        pr = {"title": prefix, "labels": [], "info": {}, "reverts_sha": None}
        c = tier0_classify(pr)
        assert c.label == expected, prefix
        assert c.confidence == 1.0


def test_tier0_scoped_conventional_prefix_with_bang():
    pr = {"title": "fix(auth)!: reject expired tokens", "labels": [], "info": {}, "reverts_sha": None}
    c = tier0_classify(pr)
    assert c.label == "fix"


def test_tier0_label_fallback_when_no_prefix():
    pr = {"title": "Add widget support", "labels": ["enhancement"], "info": {}, "reverts_sha": None}
    c = tier0_classify(pr)
    assert c.label == "feature"
    assert c.confidence == 0.8


def test_tier0_none_when_no_signal():
    pr = {"title": "Add widget support", "labels": ["needs-review"], "info": {}, "reverts_sha": None}
    assert tier0_classify(pr) is None


# --------------------------------------------------------------------------------- tier 1

def _files(*paths_ops):
    return [{"path": p, "op": op, "additions": 1, "deletions": 0} for p, op in paths_ops]


def test_tier1_docs_from_file_composition():
    pr = {"files": _files(("README.md", "modified"), ("docs/guide.md", "modified")), "stats": {}}
    c = tier1_classify(pr, ARTIFACT_CLASS)
    assert c.label == "docs" and c.confidence >= 0.8


def test_tier1_test_from_file_composition():
    pr = {"files": _files(("tests/test_foo.py", "added"), ("tests/test_bar.py", "added")), "stats": {}}
    c = tier1_classify(pr, ARTIFACT_CLASS)
    assert c.label == "test"


def test_tier1_chore_from_file_composition():
    pr = {"files": _files(("package.json", "modified"), ("package-lock.json", "modified")), "stats": {}}
    c = tier1_classify(pr, ARTIFACT_CLASS)
    assert c.label == "chore"


def test_tier1_refactor_shaped_change_is_scored_low():
    pr = {
        "files": _files(("src/a.py", "renamed"), ("src/b.py", "modified")),
        "stats": {"additions": 10, "deletions": 9},
    }
    c = tier1_classify(pr, ARTIFACT_CLASS)
    assert c.label == "refactor"
    assert c.confidence < 0.6   # deliberately modest -- the noisy split (module docstring)


def test_tier1_returns_none_without_a_file_list():
    # A bare webhook-mapped PR (git-source A4): files is None, not [].
    assert tier1_classify({"files": None, "stats": {}}, ARTIFACT_CLASS) is None


# ------------------------------------------------------------------------------- combine

def test_fix_prefix_beats_tier1_inference_under_tier0_authoritative():
    tier0 = tier0_classify({"title": "fix: broken link", "labels": [], "info": {}, "reverts_sha": None})
    tier1 = tier1_classify({"files": _files(("README.md", "modified")), "stats": {}}, ARTIFACT_CLASS)
    assert tier1.label == "docs"   # tier 1 alone would say docs
    result = combine_change_type(tier0, tier1, CHANGE_TYPE)
    assert result.label == "fix"   # tier 0 (the author's own word) wins
    assert result.tier == 0


def test_low_confidence_tier1_alone_falls_to_unknown():
    pr = {
        "files": _files(("src/new_feature.py", "added"), ("src/helper.py", "added")),
        "stats": {"additions": 100, "deletions": 5},
    }
    tier1 = tier1_classify(pr, ARTIFACT_CLASS)
    assert tier1.label == "feature"
    assert tier1.confidence < CHANGE_TYPE.unknown_below   # 0.45 < 0.5 by construction
    result = combine_change_type(None, tier1, CHANGE_TYPE)
    assert result.label == "unknown"
    assert result.digest["suppressed_label"] == "feature"   # auditable, not just discarded


def test_no_signal_at_all_is_unknown():
    result = combine_change_type(None, None, CHANGE_TYPE)
    assert result.label == "unknown"
    assert result.confidence == 0.0


def test_agree_or_unknown_mode_requires_agreement():
    config = ChangeTypeConfig(combine="agree_or_unknown")
    tier0 = tier0_classify({"title": "fix: x", "labels": [], "info": {}, "reverts_sha": None})
    tier1 = tier1_classify({"files": _files(("README.md", "modified")), "stats": {}}, ARTIFACT_CLASS)
    result = combine_change_type(tier0, tier1, config)   # fix vs docs -- disagree
    assert result.label == "unknown"


def test_highest_confidence_mode_picks_the_stronger_signal():
    config = ChangeTypeConfig(combine="highest_confidence")
    tier0 = tier0_classify({"title": "Add widget", "labels": ["enhancement"], "info": {}, "reverts_sha": None})
    tier1 = tier1_classify({"files": _files(("README.md", "modified")), "stats": {}}, ARTIFACT_CLASS)
    result = combine_change_type(tier0, tier1, config)
    assert result.label == "docs"   # 0.9 (tier1 docs) beats 0.8 (tier0 label)


# --------------------------------------------------------------------- golden fixture PRs

def test_golden_labels_per_fixture_pr():
    # A bare webhook-mapped PR with no conventional title and unmapped labels, no file list:
    # honestly unknown, not a guess.
    pr = _load_pr("pull_request_opened")
    result = classify_change_type(pr, CHANGE_TYPE, ARTIFACT_CLASS)
    assert result.label == "unknown"

    merged_pr = _load_pr("pull_request_closed_merged")
    result = classify_change_type(merged_pr, CHANGE_TYPE, ARTIFACT_CLASS)
    assert result.label == "unknown"


def test_golden_label_revert_fixture_from_push():
    # The revert commit fixture's message shape ("Revert \"...\"") -- exercised at the PR level
    # here since tier0/tier1 operate on a pr-shaped dict; git-source's own info.github.is_revert
    # flag is what tier 0 reads, reused directly.
    pr = {"title": 'Revert "Add flaky retry"', "labels": [], "info": {"github.is_revert": True},
          "reverts_sha": "2" * 40, "files": None, "stats": {}}
    result = classify_change_type(pr, CHANGE_TYPE, ARTIFACT_CLASS)
    assert result.label == "revert"
    assert result.confidence == 1.0
