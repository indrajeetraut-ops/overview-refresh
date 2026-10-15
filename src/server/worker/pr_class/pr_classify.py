"""The `pr_classify` topic handler (docs/modules/pr-work-classification.md §3, §5, §8): ties
B3 (change-type), B4 (artifact_class, recall/revert, module), and B5 (integration detection)
together into one `pr_classification` row. Rung by git-source's `apply.py` whenever a `pr`
artifact reaches a TERMINAL state (merged or closed) -- a closed-unmerged PR still needs
classifying (it is a recall candidate, §5.6) even though it will never correlate to a session.

**Integration detection runs first and, when it fires, is the final answer** (§5.8): an
aggregate's own title/labels/paths are not more trustworthy than what its commit ownership
already proves. Otherwise the usual tier 0/1 pipeline (tier 2 is v1.5, not built) decides
change_type, and `artifact_class`/recall-revert/`module` are computed independently -- they are
separate axes, not alternatives to change_type (design §2's own distinction).
"""
from __future__ import annotations

import json

import structlog
from sqlalchemy.orm import Session as DbSession

from server.storage.queries import QueryCatalog

from .classify import classify_artifact_class, classify_change_type, classify_module, structural_recall_revert
from .config import PrClassConfig
from .integration import detect_integration

log = structlog.get_logger(module="worker.pr_class.pr_classify")


def make_pr_classify_handler(config: PrClassConfig):
    """Binds the `pr_classify` handler to a config, matching `dispatch.py`'s 3-arg `Handler`
    shape, the same way `make_git_correlate_handler` does for the correlator."""

    def handler(s: DbSession, catalog: QueryCatalog, payload: dict) -> None:
        pr_classify(s, catalog, config, payload)

    return handler


def pr_classify(s: DbSession, catalog: QueryCatalog, config: PrClassConfig, payload: dict) -> None:
    artifact_id = payload["artifact_id"]
    pr = s.execute(catalog.get("pr_class", "get_artifact_for_classify"),
                   {"artifact_id": artifact_id}).mappings().first()
    if pr is None:
        log.warning("pr_classify_artifact_missing", artifact_id=artifact_id)
        return
    pr = dict(pr)

    integration = detect_integration(s, catalog, config, pr) if pr.get("merged_at") else None
    if integration is not None:
        change_type = integration["label"]
        change_confidence = integration["confidence"]
        change_tier = integration["tier"]
        change_digest = integration["digest"]
    else:
        candidate = classify_change_type(pr, config.pr_class.change_type, config.artifact_class)
        change_type, change_confidence, change_tier, change_digest = (
            candidate.label, candidate.confidence, candidate.tier, candidate.digest,
        )

    if config.pr_class.artifact_class:
        artifact_class, artifact_class_digest = classify_artifact_class(pr, config.artifact_class)
    else:
        artifact_class, artifact_class_digest = "mixed", {}

    if config.pr_class.recall_revert:
        is_revert, is_recall = structural_recall_revert(pr)
    else:
        is_revert, is_recall = False, False

    module, module_digest = classify_module(pr, config.modules)

    features_digest = {
        "change_type": change_digest, "artifact_class": artifact_class_digest, "module": module_digest,
    }

    s.execute(catalog.get("pr_class", "upsert_pr_classification"), {
        "artifact_id": artifact_id, "change_type": change_type,
        "change_confidence": change_confidence, "change_tier": change_tier,
        "artifact_class": artifact_class, "is_revert": is_revert, "is_recall": is_recall,
        "recall_reason": None, "recall_confidence": None,   # v1.5, opt-in content-transit (§5.6)
        "module": module, "rework_ratio": None,              # owned by the sibling rework module (§5.7)
        "features_digest": json.dumps(features_digest), "analyzer": config.pr_class.analyzer,
    })
    log.info("pr_classified", artifact_id=artifact_id, change_type=change_type,
             artifact_class=artifact_class, is_revert=is_revert, is_recall=is_recall, module=module)
