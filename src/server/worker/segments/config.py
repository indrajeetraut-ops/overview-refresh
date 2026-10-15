"""Config for the segments library (beat detection + Tier-0 session-type classification).

Vendored from the retired EconFold fold (`worker/fold/config.py`, classes `BeatsConfig` /
`SessionTypeConfig` + their default keyword sets) so GraphFold can drive the same proven
beat/segment logic from the graph turn model. Values mirror `config/worker.yaml`'s fold.* section;
they are analytically-chosen defaults (re-tune against labeled sessions before trusting exact
numbers, not a blocker to shipping the shape of the model)."""
from __future__ import annotations

from pydantic import BaseModel, Field

_DEFAULT_VERIFIERS = [
    r"\bpytest\b", r"\bnpm\s+(run\s+)?test\b", r"\byarn\s+test\b", r"\bpnpm\s+test\b",
    r"\bgo\s+test\b", r"\bcargo\s+test\b", r"\btsc\b", r"\beslint\b", r"\bruff\b",
    r"\bmypy\b", r"\bnpm\s+run\s+build\b", r"\bmake\s+test\b",
]
_DEFAULT_REVIEW_AGENT_TYPES = ["review", "code-review", "code-reviewer", "reviewer"]
_DEFAULT_GIT_COMMANDS = ["commit", "checkout", "switch", "branch", "merge", "push", "pull", "fetch"]


class BeatsConfig(BaseModel):
    """R4.1's beat taxonomy + R4.2's segmentation thresholds. Geometry/timing only, never quality."""

    survey_min_reads: int = 5          # "Survey": a run of read-type calls this long, no file_change
    build_cluster_gap_s: float = 600.0  # file_change events this close together are one "Build"
    stall_cluster_gap_s: float = 120.0  # api_error events this close together are one "Stall"
    stall_min_events: int = 2          # fewer than this is a retry, not a stall
    revision_loop_min_edits: int = 3   # same-path file_change count that makes it a loop
    pause_threshold_s: float = 600.0   # turn-to-turn gap past this is a "Pause" beat
    # R4.2: segmentation reuses only the *strong* beats as boundaries; deliberately larger than
    # pause_threshold_s so a beat-level Pause doesn't over-segment.
    segment_pause_threshold_s: float = 1800.0


#: R5 intent-keyword sets, regexed against a segment's own user-message text (match outcome only,
#: never the text). Deliberately short/generic; org vocabulary belongs in a config overlay.
_DEFAULT_BUG_FIX_KEYWORDS = [r"\bfix(es|ed|ing)?\b", r"\bbugs?\b", r"\bcrash(es|ed|ing)?\b",
                            r"\bbroken\b", r"\berrors?\b", r"\bfail(s|ing|ed)?\b", r"\bissue\b"]
_DEFAULT_IMPLEMENT_KEYWORDS = [r"\bimplement(s|ed|ing)?\b", r"\badd(s|ed|ing)?\b",
                               r"\bbuild(s|ing)?\b", r"\bcreate(s|d)?\b",
                               r"\brefactor(s|ed|ing)?\b", r"\bwrite\b"]
_DEFAULT_DESIGN_KEYWORDS = [r"\bdesign\b", r"\barchitecture\b", r"\bpropose(s|d)?\b",
                            r"\brfc\b", r"\bschema\b"]
_DEFAULT_PLAN_KEYWORDS = [r"\bplan\b", r"\bfigure out\b", r"\bbefore we\b", r"\bapproach\b"]
_DEFAULT_REVIEW_KEYWORDS = [r"\breview\b", r"\blook over\b", r"\bfeedback\b", r"\bpr\b"]
_DEFAULT_COMPREHEND_KEYWORDS = [r"\bwhy\b", r"\bhow does\b", r"\bunderstand\b", r"\bexplain\b",
                                r"\binvestigate\b"]

#: OPS scope past git verbs alone (R5): "git operations, environment setup, CI wrangling."
_DEFAULT_OPS_COMMAND_PATTERNS = [
    r"\bnpm\s+install\b", r"\byarn\s+add\b", r"\bpnpm\s+add\b", r"\bpip\s+install\b",
    r"\bpoetry\s+(add|install)\b", r"\bcargo\s+add\b", r"\bdocker(\s|-compose)\b",
    r"\bkubectl\b", r"\bterraform\b",
]
_DEFAULT_OPS_PATH_PREFIXES = [".github/workflows/", "docker/", "k8s/", "deploy/", ".circleci/"]

#: Conventional-commit prefix -> label (R5): the developer's own explicit label on a
#: `git commit -m "prefix: ..."`, regexed from the same Bash input.command text.
_DEFAULT_COMMIT_PREFIX_LABELS = {
    "fix": "BUG_FIX", "feat": "IMPLEMENT", "refactor": "IMPLEMENT", "test": "IMPLEMENT",
    "docs": "DESIGN", "chore": "OPS", "build": "OPS", "ci": "OPS",
}


class SessionTypeConfig(BaseModel):
    """R5's Tier-0 signatures -- a weighted-evidence scoring model. Two labels (explicit subagent
    type, explicit review slash command) stay fact-tier short-circuits (confidence 1.0); everything
    else is weighed evidence. Tunable thresholds/keyword sets here; scoring weights + taxonomy in
    session_type.py. Analytically-chosen defaults; re-tune against labeled sessions."""

    misc_min_reads: int = 3
    docs_dominant_fraction: float = 0.5
    review_slash_commands: list[str] = Field(default_factory=lambda: ["/review", "/security-review"])
    doc_extensions: list[str] = Field(default_factory=lambda: [".md", ".mdx", ".rst", ".adoc"])
    doc_path_prefixes: list[str] = Field(default_factory=lambda: ["docs/", "rfcs/"])

    ops_command_patterns: list[str] = Field(default_factory=lambda: list(_DEFAULT_OPS_COMMAND_PATTERNS))
    ops_path_prefixes: list[str] = Field(default_factory=lambda: list(_DEFAULT_OPS_PATH_PREFIXES))
    commit_prefix_labels: dict[str, str] = Field(default_factory=lambda: dict(_DEFAULT_COMMIT_PREFIX_LABELS))

    bug_fix_keywords: list[str] = Field(default_factory=lambda: list(_DEFAULT_BUG_FIX_KEYWORDS))
    implement_keywords: list[str] = Field(default_factory=lambda: list(_DEFAULT_IMPLEMENT_KEYWORDS))
    design_keywords: list[str] = Field(default_factory=lambda: list(_DEFAULT_DESIGN_KEYWORDS))
    plan_keywords: list[str] = Field(default_factory=lambda: list(_DEFAULT_PLAN_KEYWORDS))
    review_keywords: list[str] = Field(default_factory=lambda: list(_DEFAULT_REVIEW_KEYWORDS))
    comprehend_keywords: list[str] = Field(default_factory=lambda: list(_DEFAULT_COMPREHEND_KEYWORDS))

    diagnostic_read_window_s: float = 300.0
    min_evidence_score: float = 1.0
    confident_evidence_score: float = 4.0
    neighbor_inherit_confidence_floor: float = 0.5
    neighbor_inherit_confidence_factor: float = 0.7
    neighbor_gap_threshold_s: float = 600.0
