"""artifact_class, recall/revert flags, and module mapping (docs/modules/pr-work-classification.md
§5.4, §5.6, §6.2; §11's test bar: artifact_class thresholds, recall/revert flags, module mapping
including a cross-module split)."""
from __future__ import annotations

from server.worker.pr_class.classify import (
    classify_artifact_class,
    classify_module,
    structural_recall_revert,
)
from server.worker.pr_class.config import ArtifactClassConfig, ModuleMapEntry

ARTIFACT_CLASS = ArtifactClassConfig()


def _file(path, additions=0, deletions=0, op="modified"):
    return {"path": path, "op": op, "additions": additions, "deletions": deletions}


# --------------------------------------------------------------------------- artifact_class

def test_artifact_class_documentation_by_changed_lines():
    files = [_file("README.md", additions=90, deletions=0), _file("src/x.py", additions=5, deletions=5)]
    label, digest = classify_artifact_class({"files": files}, ARTIFACT_CLASS)
    assert label == "documentation"
    assert digest["doc_fraction"] >= 0.8


def test_artifact_class_coding_by_changed_lines():
    files = [_file("src/x.py", additions=100, deletions=20), _file("README.md", additions=2, deletions=0)]
    label, _ = classify_artifact_class({"files": files}, ARTIFACT_CLASS)
    assert label == "coding"


def test_artifact_class_mixed_when_neither_threshold_met():
    files = [_file("README.md", additions=40, deletions=0), _file("src/x.py", additions=60, deletions=0)]
    label, _ = classify_artifact_class({"files": files}, ARTIFACT_CLASS)
    assert label == "mixed"


def test_artifact_class_falls_back_to_file_count_without_line_data():
    # Webhook-mapped files (push-derived commits): additions/deletions are None, not present.
    files = [{"path": "docs/a.md", "op": "modified", "additions": None, "deletions": None}]
    label, _ = classify_artifact_class({"files": files}, ARTIFACT_CLASS)
    assert label == "documentation"


def test_artifact_class_mixed_with_no_file_list_at_all():
    label, digest = classify_artifact_class({"files": None}, ARTIFACT_CLASS)
    assert label == "mixed"
    assert digest == {}


# ----------------------------------------------------------------------- recall / revert

def test_revert_flag_from_info():
    is_revert, is_recall = structural_recall_revert(
        {"info": {"github.is_revert": True}, "reverts_sha": None, "state": "merged", "merged_at": "t"}
    )
    assert is_revert is True
    assert is_recall is False


def test_revert_flag_from_reverts_sha_link():
    is_revert, _ = structural_recall_revert(
        {"info": {}, "reverts_sha": "a" * 40, "state": "merged", "merged_at": "t"}
    )
    assert is_revert is True


def test_recall_is_closed_and_never_merged():
    is_revert, is_recall = structural_recall_revert(
        {"info": {}, "reverts_sha": None, "state": "closed", "merged_at": None}
    )
    assert is_revert is False
    assert is_recall is True


def test_merged_pr_is_never_a_recall():
    _, is_recall = structural_recall_revert(
        {"info": {}, "reverts_sha": None, "state": "merged", "merged_at": "t"}
    )
    assert is_recall is False


def test_open_pr_is_not_a_recall_either():
    # Still being worked -- a recall is specifically "abandoned," not "in progress."
    _, is_recall = structural_recall_revert(
        {"info": {}, "reverts_sha": None, "state": "open", "merged_at": None}
    )
    assert is_recall is False


# --------------------------------------------------------------------------------- module

MODULES = [
    ModuleMapEntry(name="collector", path_globs=["src/collector/**"]),
    ModuleMapEntry(name="server", path_globs=["src/server/**"]),
]


def test_module_dominant_by_changed_lines():
    files = [
        _file("src/server/worker/x.py", additions=100, deletions=0),
        _file("src/collector/y.py", additions=5, deletions=0),
    ]
    module, digest = classify_module({"files": files}, MODULES)
    assert module == "server"
    assert digest["module_split"]["server"] > digest["module_split"]["collector"]


def test_module_cross_module_split_recorded():
    files = [
        _file("src/server/a.py", additions=50, deletions=0),
        _file("src/collector/b.py", additions=50, deletions=0),
    ]
    module, digest = classify_module({"files": files}, MODULES)
    assert module in ("server", "collector")   # tie -- either is a defensible dominant pick
    assert set(digest["module_split"].keys()) == {"server", "collector"}
    assert abs(digest["module_split"]["server"] - 0.5) < 0.01
    assert abs(digest["module_split"]["collector"] - 0.5) < 0.01


def test_module_none_when_no_path_matches_any_glob():
    files = [_file("docs/readme.md", additions=1, deletions=0)]
    module, digest = classify_module({"files": files}, MODULES)
    assert module is None
    assert digest == {}


def test_module_none_without_modules_configured():
    files = [_file("src/server/a.py", additions=1, deletions=0)]
    module, digest = classify_module({"files": files}, [])
    assert module is None


def test_module_one_path_never_double_counted_across_overlapping_globs():
    overlapping = [
        ModuleMapEntry(name="all_src", path_globs=["src/**"]),
        ModuleMapEntry(name="server", path_globs=["src/server/**"]),
    ]
    files = [_file("src/server/a.py", additions=10, deletions=0)]
    module, digest = classify_module({"files": files}, overlapping)
    # First-matching-module wins per file -- exactly one module gets the weight, not both.
    assert len(digest["module_split"]) == 1
    assert sum(digest["module_split"].values()) == 1.0
