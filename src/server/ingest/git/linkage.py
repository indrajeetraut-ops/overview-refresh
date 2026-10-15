"""PR -> issue linkage resolver (ISSUE §3.2, plan H3).

For a pull request, find the issue(s) it closes, best source first:

    1. closingIssuesReferences present   -> method=graphql, confidence=1.0   (authoritative)
    2. else commit trailer "Closes #N"   -> method=trailer,  confidence=0.9
    3. else branch name ".../ISSUE-123"  -> method=branch,   confidence=0.7
    4. else PR title "#N" / "PROJ-123"   -> method=title,    confidence=0.6
    5. else PR body "Closes #N"          -> method=body,     confidence=0.5   (was the only path)

**First rung that yields any ref wins**, and every ref from that rung carries that rung's
method + confidence -- a PR that closes two issues via `closingIssuesReferences` gets two 1.0
links; a lower rung is not consulted once a higher one matched. The confidence is *carried, not
flattened* (measurement honesty §5): a feature assembled from 0.5 body-regex links is not the
same claim as one from 1.0 GraphQL links, and F4 can say so.

This replaces the body-only `map_github._parse_linked_issue` regex that F4 wrongly assumed was
a full extractor -- that regex is now just rung 5.

Pure and framework-free: the GraphQL/REST/webhook readers gather the raw inputs (which of them
they can see varies -- only GraphQL carries `closingIssuesReferences` and commit messages) and
call `resolve_links`; `apply.py` persists the result to `pr_issue_links`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .issue_ref import IssueRef, github_ref, is_sha, parse_jira_refs

#: "closes/fixes/resolves #N" -- shared by the commit-trailer rung and the body rung. Same
#: shape as `map_github._CLOSES_RE` (kept in sync deliberately; that one is rung 5's own use).
_CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*#(\d+)", re.IGNORECASE)
#: A leading issue number on a branch segment: `318-async-auth`, `gh-318-...`, `issue/318`,
#: `#318`. The `(?:gh|issue|bug|feat|fix)?[-_/]?` prefix is optional noise teams put in front.
_BRANCH_NUM_RE = re.compile(r"^(?:gh|issue|bug|feature|feat|fix|hotfix|chore)?[-_/]?#?(\d{1,9})(?:[-_/]|$)", re.IGNORECASE)
#: A `#N` anywhere in a PR title, or a title that is *just* a number.
_TITLE_HASH_RE = re.compile(r"(?:^|[^\w/])#(\d{1,9})\b")
_TITLE_BARE_NUM_RE = re.compile(r"^\s*(\d{1,9})\s*[:\-]")

_CONFIDENCE = {"graphql": 1.0, "trailer": 0.9, "branch": 0.7, "title": 0.6, "body": 0.5}


@dataclass(frozen=True)
class Link:
    """One resolved PR->issue tie: which issue, how it was found, how much to trust it."""

    ref: IssueRef
    method: str          # graphql | trailer | branch | title | body
    confidence: float

    def as_dict(self) -> dict:
        return {"ref": self.ref.as_dict(), "method": self.method, "confidence": self.confidence}


def _closes_numbers(texts: list[str]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for t in texts:
        for m in _CLOSES_RE.finditer(t or ""):
            n = int(m.group(1))
            if n and n not in seen:
                seen.add(n)
                out.append(n)
    return out


def _branch_github_number(branch: str | None) -> int | None:
    if not branch:
        return None
    for seg in re.split(r"[/]", branch):
        if is_sha(seg):
            continue   # a branch cut straight off a sha -- not an issue number (§3.2)
        m = _BRANCH_NUM_RE.match(seg)
        if m:
            return int(m.group(1))
    return None


def _title_github_number(title: str | None) -> int | None:
    if not title:
        return None
    m = _TITLE_HASH_RE.search(title)
    if m:
        return int(m.group(1))
    m = _TITLE_BARE_NUM_RE.match(title)
    return int(m.group(1)) if m else None


def resolve_links(
    *,
    repo_num: int,
    project_key: str,
    closing_issue_numbers: list[int] | None = None,
    commit_messages: list[str] | None = None,
    branch: str | None = None,
    title: str | None = None,
    body: str | None = None,
    known_project_keys: set[str] | None = None,
) -> list[Link]:
    """Run the §3.2 ladder and return the winning rung's links (possibly several, possibly
    none). `project_key` is what a GitHub ref is keyed under (the repo's full_name, or its
    `repo_num` as a string when the name is not resolvable). `known_project_keys` gates
    Jira-style tokens in the branch/title rungs (see `issue_ref.parse_jira_refs`)."""

    # Rung 1 -- authoritative GraphQL references.
    gql = [github_ref(repo_num, project_key, n) for n in (closing_issue_numbers or []) if n]
    if gql:
        return [Link(r, "graphql", _CONFIDENCE["graphql"]) for r in _dedup(gql)]

    # Rung 2 -- "Closes #N" in a commit message (trailer or inline).
    trailer = [github_ref(repo_num, project_key, n) for n in _closes_numbers(commit_messages or [])]
    if trailer:
        return [Link(r, "trailer", _CONFIDENCE["trailer"]) for r in _dedup(trailer)]

    # Rung 3 -- the branch name.
    branch_refs: list[IssueRef] = []
    n = _branch_github_number(branch)
    if n:
        branch_refs.append(github_ref(repo_num, project_key, n))
    branch_refs += parse_jira_refs(branch, known_project_keys)
    if branch_refs:
        return [Link(r, "branch", _CONFIDENCE["branch"]) for r in _dedup(branch_refs)]

    # Rung 4 -- the PR title.
    title_refs: list[IssueRef] = []
    n = _title_github_number(title)
    if n:
        title_refs.append(github_ref(repo_num, project_key, n))
    title_refs += parse_jira_refs(title, known_project_keys)
    if title_refs:
        return [Link(r, "title", _CONFIDENCE["title"]) for r in _dedup(title_refs)]

    # Rung 5 -- "Closes #N" in the PR body (the pre-H3 behaviour, now the last resort).
    body_refs = [github_ref(repo_num, project_key, n) for n in _closes_numbers([body or ""])]
    if body_refs:
        return [Link(r, "body", _CONFIDENCE["body"]) for r in _dedup(body_refs)]

    return []


def _dedup(refs: list[IssueRef]) -> list[IssueRef]:
    out: list[IssueRef] = []
    seen: set[tuple[str, str, int]] = set()
    for r in refs:
        if r.key not in seen:
            seen.add(r.key)
            out.append(r)
    return out
