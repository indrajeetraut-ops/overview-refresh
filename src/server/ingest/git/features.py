"""Feature grouping helper (issue-tracker-ingestion.md §3, plan H5) -- what F4 / `api/aggregate.py`
consumes to build the "cost of a feature" surface.

A **feature** is an issue ref plus the PRs linked to it (`pr_issue_links`, plan H3); session
economics compose up to the feature through `session -> PR` (`session_git_links`, the
pr-work-classification correlator's output). Two shapes the design calls out explicitly:

- **An issue with no linked PR is still a feature** -- work not yet built, shown as
  not-started / in-progress, never hidden (measurement honesty §5).
- **A PR with no issue link falls back to a single-PR feature** (`single_pr_feature`).

Link confidence is *carried, not flattened* (§5): the feature reports the lowest confidence
among its PR links, so F4 can mark a feature assembled from 0.5 body-regex links differently
from one built on 1.0 GraphQL references.

Boundary-key priority is `linked issue -> PR-cluster on one branch -> branch -> label`
(REPO §4). This helper implements the top rung (issue-keyed), the branch/label clustering of
issueless PRs (H5, `branch_label_features`), and the single-PR fallback for the
un-clusterable remainder.

Read-only. Takes a live `Session` + `QueryCatalog`, no HTTP -- the same framework-free shape as
`apply.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session as DbSession

from server.storage.queries import QueryCatalog

from .issue_ref import IssueRef


@dataclass(frozen=True)
class ClusterKey:
    """H5: the boundary key for a branch- or label-clustered orphan-PR feature -- REPO §4's
    remaining two rungs (`PR-cluster on one branch -> branch -> label`), after the issue rung
    `IssueRef` keys and the `single_pr_feature` fallback. Parallel to `IssueRef` but never
    resolves to a real tracker entity, so it's its own small type rather than forcing a branch
    or label *name* through `IssueRef.number` (an `int`)."""

    kind: str      # "branch" | "label"
    repo_num: str
    value: str     # the branch name or label name

    def as_dict(self) -> dict:
        return {"kind": self.kind, "repo_num": self.repo_num, "value": self.value}


#: H5: a branch/label cluster is an inference, never a link -- the same `inferred` confidence
#: convention `revision_loop` (STORY §4) already uses for its one inferred taxonomy member.
INFERRED_CONFIDENCE = 0.5


@dataclass
class Feature:
    """One feature's composition and economics. `issue` is `None` for a referenced-but-not-
    pulled ref (a Jira key, or a GitHub issue whose backfill has not reached it) -- the feature
    still exists, keyed by `ref`."""

    ref: IssueRef | ClusterKey
    issue: dict | None
    status: str                                   # shipped | in_progress | not_started
    member_pr_ids: list[str] = field(default_factory=list)
    session_ids: list[str] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)   # per PR: {pr_artifact_id, method, confidence}
    min_confidence: float | None = None
    cost_usd_micros: int = 0

    def as_dict(self) -> dict:
        return {
            "ref": self.ref.as_dict(), "issue": self.issue, "status": self.status,
            "member_pr_ids": self.member_pr_ids, "session_ids": self.session_ids,
            "links": self.links, "min_confidence": self.min_confidence,
            "cost_usd_micros": self.cost_usd_micros,
        }


def ref_repo_num(ref: IssueRef | ClusterKey) -> str | None:
    """The git-source repo_num a `Feature.ref` resolves to, regardless of which kind it is --
    `IssueRef.project_key` for a github-tracker ref, `ClusterKey.repo_num` for an H5 branch/
    label cluster (both are GitHub-only in MVP). `None` for a non-GitHub `IssueRef` (e.g.
    Jira) -- no git-source bridge exists for it."""
    if isinstance(ref, ClusterKey):
        return ref.repo_num
    return ref.project_key if ref.tracker == "github" else None


def ref_issue_key(ref: IssueRef | ClusterKey) -> str:
    """The feature's display label -- `#<n>` for a github issue, `<PROJ>-<n>` for a Jira
    ref, or the branch/label name itself for an H5 cluster (its closest equivalent, since a
    cluster has no tracker number at all)."""
    if isinstance(ref, ClusterKey):
        return ref.value
    return f"#{ref.number}" if ref.tracker == "github" else f"{ref.project_key}-{ref.number}"


def _sessions_and_cost(s: DbSession, catalog: QueryCatalog, pr_ids: list[str]) -> tuple[list[str], int]:
    if not pr_ids:
        return [], 0
    session_ids = sorted(
        r[0] for r in s.execute(catalog.get("git", "sessions_for_artifacts"),
                                {"artifact_ids": pr_ids}).all()
    )
    cost = 0
    if session_ids:
        cost = int(s.execute(catalog.get("git", "cost_micros_for_sessions"),
                             {"session_ids": session_ids}).scalar() or 0)
    return session_ids, cost


def _member_states(s: DbSession, catalog: QueryCatalog, pr_ids: list[str]) -> list[tuple[str, object]]:
    if not pr_ids:
        return []
    return [
        (r["state"], r["merged_at"])
        for r in s.execute(catalog.get("git", "artifact_states_for_ids"),
                           {"artifact_ids": pr_ids}).mappings().all()
    ]


def _status_no_issue(member_states: list[tuple[str, object]]) -> str:
    """Status for a feature with no issue at all -- the single-PR fallback, and now branch/
    label clusters (H5): `shipped` once any member PR merged, `in_progress` otherwise. There
    is no issue to gate `shipped` on (unlike `_status`'s issue-keyed rule), and a cluster
    (unlike a single PR) is only ever constructed from at least one member, so it is never
    `not_started`."""
    return "shipped" if any(m is not None for _, m in member_states) else "in_progress"


def _status(issue: dict | None, member_states: list[tuple[str, object]]) -> str:
    """`shipped` once the issue is closed and at least one member PR merged; `in_progress` while
    any PR exists; `not_started` for an issue with no PR at all."""
    any_merged = any(merged_at is not None for _, merged_at in member_states)
    issue_closed = bool(issue) and issue.get("state") == "closed"
    if issue_closed and any_merged:
        return "shipped"
    if member_states:
        return "in_progress"
    return "not_started"


def feature_of(s: DbSession, catalog: QueryCatalog, ref: IssueRef) -> Feature:
    """Compose the feature keyed by `ref`: its issue entity (if pulled), the PRs linked to it,
    the sessions that fed those PRs, and Σ their effective cost."""
    link_rows = s.execute(catalog.get("git", "prs_for_issue_ref"), {
        "tracker": ref.tracker, "project_key": ref.project_key, "number": ref.number,
    }).mappings().all()

    member_pr_ids = sorted({r["pr_artifact_id"] for r in link_rows})
    links = [{"pr_artifact_id": r["pr_artifact_id"], "method": r["method"],
              "confidence": float(r["confidence"])} for r in link_rows]
    min_confidence = min((lk["confidence"] for lk in links), default=None)

    issue_artifact_id = ref.artifact_id
    if issue_artifact_id is None and link_rows:
        issue_artifact_id = next((r["issue_artifact_id"] for r in link_rows
                                  if r["issue_artifact_id"]), None)
    issue = None
    if issue_artifact_id:
        row = s.execute(catalog.get("git", "issue_artifact_by_id"),
                        {"artifact_id": issue_artifact_id}).mappings().first()
        issue = dict(row) if row else None

    member_states = _member_states(s, catalog, member_pr_ids)
    session_ids, cost = _sessions_and_cost(s, catalog, member_pr_ids)
    return Feature(
        ref=ref, issue=issue, status=_status(issue, member_states),
        member_pr_ids=member_pr_ids, session_ids=session_ids, links=links,
        min_confidence=min_confidence, cost_usd_micros=cost,
    )


def single_pr_feature(s: DbSession, catalog: QueryCatalog, pr_artifact_id: str) -> Feature:
    """The fallback for a PR that carries no issue link: a feature of exactly one PR, keyed by
    a synthetic `pr:` ref so downstream grouping stays uniform (§3 "PRs with no issue fall back
    to a single-PR feature")."""
    _, _, repo_num, pr_number = pr_artifact_id.split(":")
    ref = IssueRef("github", repo_num, int(pr_number), None)
    states = _member_states(s, catalog, [pr_artifact_id])
    session_ids, cost = _sessions_and_cost(s, catalog, [pr_artifact_id])
    return Feature(
        ref=ref, issue=None, status=_status_no_issue(states), member_pr_ids=[pr_artifact_id],
        session_ids=session_ids, links=[], min_confidence=None, cost_usd_micros=cost,
    )


def _primary_label(labels: list[str] | None) -> str | None:
    """A single deterministic label when a PR carries several -- first alphabetically, so a
    PR with multiple labels lands in exactly one label cluster, never two (H5's own rule)."""
    return sorted(labels)[0] if labels else None


def _cluster_feature(s: DbSession, catalog: QueryCatalog, kind: str, repo_num: str,
                     value: str, pr_ids: list[str]) -> Feature:
    pr_ids = sorted(pr_ids)
    states = _member_states(s, catalog, pr_ids)
    session_ids, cost = _sessions_and_cost(s, catalog, pr_ids)
    return Feature(
        ref=ClusterKey(kind, repo_num, value), issue=None, status=_status_no_issue(states),
        member_pr_ids=pr_ids, session_ids=session_ids, links=[],
        min_confidence=INFERRED_CONFIDENCE, cost_usd_micros=cost,
    )


def branch_label_features(s: DbSession, catalog: QueryCatalog, repo_num: str,
                          orphan_pr_rows: list[dict], default_branch: str | None) -> list[Feature]:
    """H5: branch, then label, clustering for orphan PRs (no issue link at all) -- REPO §4's
    boundary-key priority's remaining two rungs, after `feature_of`'s issue rung. `single_pr_
    feature` is still the fallback here for the un-clusterable remainder (today's behaviour,
    preserved).

    `orphan_pr_rows` is `[{artifact_id, branch, labels}]` (`manager.repo_prs_without_issue_
    link`). Grouping:

    1. **Branch** -- a non-null branch that isn't the repo's own default (`default_branch`,
       `None`/unknown falls back to the literal `main`/`master`) puts a PR in that branch's
       cluster, even alone (a later PR pushed to the same branch just joins it).
    2. **Label** -- no usable branch, but at least one label: the PR's `_primary_label` (first
       alphabetically, so a multi-labelled PR lands in exactly one cluster).
    3. **Fallback** -- neither: `single_pr_feature`, unchanged.

    Never clusters on the default branch: every direct-to-main PR shares it, which would
    otherwise collapse a whole repo's orphan PRs into one bogus feature."""
    known_defaults = {default_branch} if default_branch else {"main", "master"}
    by_branch: dict[str, list[str]] = {}
    by_label: dict[str, list[str]] = {}
    singles: list[str] = []

    for row in orphan_pr_rows:
        pr_id = row["artifact_id"]
        branch = row.get("branch")
        if branch and branch not in known_defaults:
            by_branch.setdefault(branch, []).append(pr_id)
            continue
        label = _primary_label(row.get("labels"))
        if label:
            by_label.setdefault(label, []).append(pr_id)
            continue
        singles.append(pr_id)

    features = [
        _cluster_feature(s, catalog, "branch", repo_num, branch, pr_ids)
        for branch, pr_ids in sorted(by_branch.items())
    ]
    features += [
        _cluster_feature(s, catalog, "label", repo_num, label, pr_ids)
        for label, pr_ids in sorted(by_label.items())
    ]
    features += [single_pr_feature(s, catalog, pr_id) for pr_id in singles]
    return features
