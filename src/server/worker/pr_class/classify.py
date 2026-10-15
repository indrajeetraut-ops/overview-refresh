"""Change-type classification -- tiers 0 and 1 (docs/modules/pr-work-classification.md §5.1,
§5.3). Each tier is a pure function of evidence already on `git_artifacts`; **no diff fetch, no
LLM, no rework computation** (§5.1's own note: rework moved to the sibling module).

**The honest calibration point (§5.1), taken literally in the confidence numbers below:**
`docs`/`test`/`chore`/`revert` are reliable from paths and flags and get high confidence;
`feature`/`fix`/`refactor` are genuinely noisy from tier 1 alone and are scored AT OR BELOW the
default `unknown_below` (0.5), so they honestly fall back to `unknown` unless tier 0
(author-declared intent, via a conventional-commit prefix or a label) is available to be
authoritative over them. This is a design choice, not an oversight: a wrong high-confidence
guess is worse than an honest `unknown` in a report a customer reads.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any

from .config import ArtifactClassConfig, ChangeTypeConfig, ModuleMapEntry

_CONVENTIONAL_RE = re.compile(
    r"^(?P<type>feat|fix|refactor|docs|test|chore|revert|perf|build|ci|style)(\([^)]*\))?!?:\s",
    re.IGNORECASE,
)
#: Conventional-commit types outside the closed enum collapse to their closest bucket --
#: `perf`/`style` are refactor-shaped (behavior-preserving), `build`/`ci` are chore-shaped.
_CONVENTIONAL_TYPE_MAP = {
    "feat": "feature", "fix": "fix", "refactor": "refactor", "docs": "docs", "test": "test",
    "chore": "chore", "revert": "revert", "perf": "refactor", "build": "chore", "ci": "chore",
    "style": "refactor",
}
_LABEL_MAP = {
    "bug": "fix", "bugfix": "fix", "fix": "fix",
    "feature": "feature", "enhancement": "feature",
    "docs": "docs", "documentation": "docs",
    "chore": "chore", "dependencies": "chore", "build": "chore", "ci": "chore",
    "refactor": "refactor", "refactoring": "refactor", "cleanup": "refactor",
    "test": "test", "tests": "test", "testing": "test",
    "revert": "revert",
}
_TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__)/|(^|/)(test_[^/]*|[^/]*_test\.[^/]*|[^/]*\.test\.[^/]*|[^/]*\.spec\.[^/]*)$",
    re.IGNORECASE,
)
_CHORE_PATH_RE = re.compile(
    r"(^|/)(\.github/workflows/[^/]*|Dockerfile[^/]*|docker-compose[^/]*\.ya?ml|"
    r"package(-lock)?\.json|requirements[^/]*\.txt|poetry\.lock|Makefile|\.gitignore|"
    r"\.pre-commit-config\.ya?ml|pyproject\.toml|setup\.(py|cfg))$",
    re.IGNORECASE,
)


@dataclass
class Candidate:
    """One tier's answer: a label, how sure it is, which tier produced it, and a non-reversible
    digest of *why* (counts/ratios only -- never code/prose, design §5.9)."""

    label: str
    confidence: float
    tier: int
    digest: dict[str, Any] = field(default_factory=dict)


def _matches_any_glob(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in globs)


def tier0_classify(pr: dict[str, Any]) -> Candidate | None:
    """Self-reported evidence (§5.1): a structural revert flag beats everything (certain, from
    git-source's own `is_revert`/`reverts_sha`, §5.6), then a conventional-commit prefix on the
    PR title (the author's own words), then a label. Free -- already on `git_artifacts`."""
    info = pr.get("info") or {}
    if info.get("github.is_revert") or pr.get("reverts_sha"):
        return Candidate("revert", 1.0, 0, {"signal": "is_revert"})

    title = (pr.get("title") or "").strip()
    m = _CONVENTIONAL_RE.match(title)
    if m:
        label = _CONVENTIONAL_TYPE_MAP.get(m.group("type").lower())
        if label:
            return Candidate(label, 1.0, 0, {"signal": "conventional_commit_prefix", "prefix": m.group("type")})

    for raw_label in pr.get("labels") or []:
        mapped = _LABEL_MAP.get(str(raw_label).lower())
        if mapped:
            return Candidate(mapped, 0.8, 0, {"signal": "label", "label": raw_label})
    return None


def tier1_classify(pr: dict[str, Any], config: ArtifactClassConfig) -> Candidate | None:
    """Metadata heuristic (§5.1): file paths/extensions, churn shape, test-file presence, new-
    file/rename ratios. Cheap -- already on `git_artifacts.files`/`.stats`. Returns `None` when
    there is no file list at all (a bare webhook-mapped PR, git-source A4) or no signal clears
    even this tier's own (already-modest) bar."""
    files = pr.get("files") or []
    total = len(files)
    if total == 0:
        return None

    doc_n = sum(1 for f in files if _matches_any_glob(f.get("path", ""), config.doc_path_globs))
    test_n = sum(1 for f in files if _TEST_PATH_RE.search(f.get("path", "")))
    chore_n = sum(1 for f in files if _CHORE_PATH_RE.search(f.get("path", "")))
    new_n = sum(1 for f in files if f.get("op") == "added")
    removed_n = sum(1 for f in files if f.get("op") == "removed")
    renamed_n = sum(1 for f in files if f.get("op") == "renamed")

    if doc_n / total >= 0.8:
        return Candidate("docs", 0.9, 1, {"doc_fraction": round(doc_n / total, 3)})
    if test_n / total >= 0.6:
        return Candidate("test", 0.85, 1, {"test_fraction": round(test_n / total, 3)})
    if chore_n / total >= 0.6:
        return Candidate("chore", 0.8, 1, {"chore_fraction": round(chore_n / total, 3)})

    stats = pr.get("stats") or {}
    additions = stats.get("additions") or 0
    deletions = stats.get("deletions") or 0

    # These three are the genuinely noisy split (module docstring): scored at/below
    # unknown_below by design, so tier 0 (or an explicit lower threshold) is what promotes them.
    if renamed_n / total >= 0.3 or (new_n == 0 and removed_n == 0
                                    and abs(additions - deletions) <= max(additions, deletions, 1) * 0.3):
        return Candidate("refactor", 0.55, 1, {"renamed_fraction": round(renamed_n / total, 3)})
    if new_n / total >= 0.4 and additions > deletions * 1.5:
        return Candidate("feature", 0.45, 1, {"new_file_fraction": round(new_n / total, 3)})
    if total <= 3 and (additions + deletions) < 30:
        return Candidate("fix", 0.35, 1, {"small_change": True, "changed_files": total})
    return None


def combine_change_type(tier0: Candidate | None, tier1: Candidate | None,
                        config: ChangeTypeConfig) -> Candidate:
    """Resolves the enabled tiers' candidates per `config.combine` (§5.3), then applies
    `unknown_below`: a chosen label under that confidence emits `unknown` rather than a guess."""
    if config.combine == "highest_confidence":
        candidates = [c for c in (tier0, tier1) if c is not None]
        chosen = max(candidates, key=lambda c: c.confidence) if candidates else None
    elif config.combine == "agree_or_unknown":
        if tier0 and tier1 and tier0.label == tier1.label:
            chosen = tier0
        elif tier0 and tier1:
            chosen = None   # disagreement -- a customer who chose this mode prefers unknown
        else:
            chosen = tier0 or tier1
    else:   # "tier0_authoritative" (default): the author's own word beats any inference
        chosen = tier0 or tier1

    if chosen is None:
        return Candidate("unknown", 0.0, 1, {})
    if chosen.confidence < config.unknown_below:
        return Candidate("unknown", chosen.confidence, chosen.tier,
                         {**chosen.digest, "suppressed_label": chosen.label})
    return chosen


def classify_change_type(pr: dict[str, Any], change_type_config: ChangeTypeConfig,
                         artifact_class_config: ArtifactClassConfig) -> Candidate:
    """The B3 entry point: run whichever tiers `change_type_config` enables, combine, and
    return the final `{label, confidence, tier, digest}` -- tier 2 (diff-structure) is a
    separate, opt-in, not-yet-built path (§5.2, v1.5)."""
    tier0 = tier0_classify(pr) if change_type_config.tier0_self_reported else None
    tier1 = tier1_classify(pr, artifact_class_config) if change_type_config.tier1_metadata else None
    return combine_change_type(tier0, tier1, change_type_config)


# ------------------------------------------------------------------------- artifact class (§5.4)

def _file_weight(f: dict[str, Any]) -> int:
    """Changed-LINE weight when git-source knows it (a backfilled PR); falls back to 1 per
    file when it doesn't (a webhook-mapped PR's files carry no additions/deletions at all,
    git-source A4 -- an honest degrade, not a bug)."""
    lines = (f.get("additions") or 0) + (f.get("deletions") or 0)
    return lines if lines > 0 else 1


def classify_artifact_class(pr: dict[str, Any], config: ArtifactClassConfig) -> tuple[str, dict[str, Any]]:
    """`coding` | `documentation` | `mixed` (§5.4), from the SAME file-category composition
    tier 1 already computes -- but weighted by changed LINES where available, not file count
    (§5.4's own wording), since one one-line README tweak alongside a 500-line refactor should
    not read as "documentation." `"mixed"` is the honest fallback with no file list at all,
    never a guessed axis."""
    files = pr.get("files") or []
    if not files:
        return "mixed", {}

    total = sum(_file_weight(f) for f in files)
    doc_weight = sum(_file_weight(f) for f in files if _matches_any_glob(f.get("path", ""), config.doc_path_globs))
    doc_fraction = doc_weight / total if total else 0.0

    digest = {"doc_fraction": round(doc_fraction, 3), "file_count": len(files)}
    if doc_fraction >= config.documentation_min_fraction:
        return "documentation", digest
    if (1.0 - doc_fraction) >= config.coding_min_fraction:
        return "coding", digest
    return "mixed", digest


# ---------------------------------------------------------------------- recall / revert (§5.6)

def structural_recall_revert(pr: dict[str, Any]) -> tuple[bool, bool]:
    """Content-free, always-on structural flags (§5.6) -- both come straight off
    `git_artifacts`, no content. A **revert** is `info.github.is_revert` or a `reverts_sha`
    link; a **recall** is a PR closed without merging (worked, then abandoned). `recall_reason`
    (why it was abandoned) is v1.5, opt-in content-transit -- not computed here."""
    info = pr.get("info") or {}
    is_revert = bool(info.get("github.is_revert")) or bool(pr.get("reverts_sha"))
    is_recall = pr.get("state") == "closed" and pr.get("merged_at") is None
    return is_revert, is_recall


# --------------------------------------------------------------------------------- module (§6.2)

def classify_module(pr: dict[str, Any], modules: list[ModuleMapEntry]) -> tuple[str | None, dict[str, Any]]:
    """The one grouping dimension not directly on an artifact (§6.2): a config-driven
    path→module map. A PR's module is the DOMINANT module of its changed paths (weighted by
    changed lines where known, else file count, same as `classify_artifact_class`); a
    cross-module PR records the full split in the digest rather than losing it to the single
    dominant label. `None` when there is no file list, or no configured module claims any of
    the PR's paths -- never a guess."""
    files = pr.get("files") or []
    if not files or not modules:
        return None, {}

    weights: dict[str, int] = {}
    for f in files:
        path = f.get("path", "")
        for m in modules:
            if _matches_any_glob(path, m.path_globs):
                weights[m.name] = weights.get(m.name, 0) + _file_weight(f)
                break   # first-matching-module wins per file -- no double-counting one path
    if not weights:
        return None, {}

    total = sum(weights.values())
    dominant = max(weights, key=weights.get)
    return dominant, {"module_split": {k: round(v / total, 3) for k, v in weights.items()}}
