"""Config models for pr-work-classification (docs/modules/pr-work-classification.md §3.1,
§5.3, §5.4, §5.8, §6.2). Layering: defaults here -> YAML -> env, the same shape as every other
config loader in this codebase (arch §12).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_ENV_REF = re.compile(r"\$\{(\w+)\}")

#: The literal sentinel `eval_scope.base_branches` uses for "this repo's own default branch"
#: (§3.1) -- resolved per-repo from `git_repos.default_branch`, never a real branch name.
DEFAULT_BRANCH_SENTINEL = "<default-branch>"


class RepoScopeOverride(BaseModel):
    base_branches: list[str] | None = None
    exclude_head_branches: list[str] | None = None


class EvalScopeConfig(BaseModel):
    base_branches: list[str] = Field(default_factory=lambda: [DEFAULT_BRANCH_SENTINEL])
    exclude_head_branches: list[str] = Field(
        default_factory=lambda: ["release/*", "staging", "production"]
    )
    per_repo: dict[str, RepoScopeOverride] = Field(default_factory=dict)


class ChangeTypeConfig(BaseModel):
    tier0_self_reported: bool = True
    tier1_metadata: bool = True
    tier2_diff: bool = False   # content transit -- opt-in, off by default (§5.2, §5.3)
    combine: str = "tier0_authoritative"   # | "highest_confidence" | "agree_or_unknown"
    unknown_below: float = 0.5


class PrClassSubConfig(BaseModel):
    change_type: ChangeTypeConfig = Field(default_factory=ChangeTypeConfig)
    artifact_class: bool = True
    recall_revert: bool = True
    recall_reason: bool = False   # content transit -- opt-in, off by default (§5.6)
    analyzer: str = "heuristic"   # | "llm" (future backend, §5.5)


class ArtifactClassConfig(BaseModel):
    documentation_min_fraction: float = 0.8
    coding_min_fraction: float = 0.8
    doc_path_globs: list[str] = Field(
        default_factory=lambda: ["*.md", "*.rst", "*.mdx", "docs/**", "README*", "CHANGELOG*", "LICENSE*"]
    )


class IntegrationConfig(BaseModel):
    already_owned_ratio_threshold: float = 0.8


class ModuleMapEntry(BaseModel):
    name: str
    path_globs: list[str]


class PrClassConfig(BaseModel):
    eval_scope: EvalScopeConfig = Field(default_factory=EvalScopeConfig)
    pr_class: PrClassSubConfig = Field(default_factory=PrClassSubConfig)
    artifact_class: ArtifactClassConfig = Field(default_factory=ArtifactClassConfig)
    integration: IntegrationConfig = Field(default_factory=IntegrationConfig)
    modules: list[ModuleMapEntry] = Field(default_factory=list)


def load_config(path: str | Path) -> PrClassConfig:
    text = Path(path).read_text(encoding="utf-8")
    text = _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), text)
    data = yaml.safe_load(text) or {}
    return PrClassConfig.model_validate(data)
