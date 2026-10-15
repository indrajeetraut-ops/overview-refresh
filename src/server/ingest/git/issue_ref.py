"""The tracker-agnostic issue reference (ISSUE §3.1, plan H4) -- the key F4 groups a feature on
and the seam a Jira connector fills later without reshaping the feature surfaces.

`IssueRef` is `(tracker, project_key, number)` plus an optional resolved `artifact_id`:

- **GitHub** ships now: a `#N` token in a repo resolves to
  `IssueRef("github", "<repo full_name or repo_num>", N, "github:<repo_num>:issue:N")`.
- **Jira** parses to the *same shape* -- a `PROJ-123` token becomes
  `IssueRef("jira", "PROJ", 123, None)` -- but only when `PROJ` is a *known* project key, and
  `artifact_id` stays `None` because there is no Jira connector to resolve it against yet.

The known-key gate is the false-positive guard §3.2 calls for: `UTF-8`, `ISO-8601`, `SHA-1`,
`RFC-2119` and friends all match a naive `[A-Z]+-\\d+` and would otherwise manufacture phantom
tickets. With no Jira integration configured (`known_project_keys` empty), every bare
`PROJ-N`-shaped token is rejected -- a missed real Jira ref is recoverable later; a fabricated
feature grouping is not (measurement honesty §5).

Pure and framework-free: no DB, no I/O. Callers pass the repo context in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: A GitHub issue reference: `#123` (optionally `GH-123`), as it appears in a branch name, PR
#: title or body. Bare `123` is deliberately NOT matched here -- too noisy out of context; the
#: linkage resolver handles the "PR title is just a number" case explicitly.
_GH_REF_RE = re.compile(r"(?:^|[^\w/])#(\d{1,9})\b")
#: A Jira-style key: 2+ upper-case letters, a hyphen, digits. The known-key gate downstream is
#: what keeps this from matching `UTF-8` etc.
_JIRA_REF_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-(\d{1,9})\b")
#: A 40- (or 7-to-40-) hex string: a git sha, never an issue number. Guards the number rungs.
_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
#: Tokens that match `_JIRA_REF_RE` but are standards/RFC names, never trackers. Rejected even
#: if someone mis-configures them as a project key.
_NEVER_JIRA = frozenset({"UTF", "ISO", "SHA", "RFC", "PEP", "CVE", "ES", "HTTP", "TLS", "SSL"})

MethodConfidence = tuple[str, float]


@dataclass(frozen=True)
class IssueRef:
    """`(tracker, project_key, number)` -- the grouping key -- plus the resolved entity id when
    the tracker is connected. `artifact_id` is `None` for a referenced-but-not-pulled issue
    (a Jira key today; a GitHub issue whose backfill has not reached it yet)."""

    tracker: str          # "github" | "jira"
    project_key: str      # repo full_name / repo_num for github; project key ("PLAT") for jira
    number: int
    artifact_id: str | None = None

    @property
    def key(self) -> tuple[str, str, int]:
        """The 3-tuple `pr_issue_links` keys on and F4 groups on -- never a bare string."""
        return (self.tracker, self.project_key, self.number)

    def as_dict(self) -> dict:
        return {"tracker": self.tracker, "project_key": self.project_key,
                "number": self.number, "artifact_id": self.artifact_id}


def github_ref(repo_num: int, project_key: str, number: int) -> IssueRef:
    """Build a resolved GitHub ref. `artifact_id` follows the namespaced scheme
    `github:<repo_num>:issue:<n>` (ISSUE §0.5) -- collision-free against any later Jira id."""
    return IssueRef("github", project_key, int(number),
                    f"github:{repo_num}:issue:{int(number)}")


def is_sha(token: str) -> bool:
    """A bare 7-40 hex token is a git sha, not an issue number (§3.2 validation)."""
    return bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", token or ""))


def parse_github_refs(text: str | None, repo_num: int, project_key: str) -> list[IssueRef]:
    """Every `#N` reference in `text`, de-duplicated, order-preserving. SHA-looking `#<hex>`
    never matches because `_GH_REF_RE` requires digits only."""
    if not text:
        return []
    out: list[IssueRef] = []
    seen: set[int] = set()
    for m in _GH_REF_RE.finditer(text):
        n = int(m.group(1))
        if n and n not in seen:
            seen.add(n)
            out.append(github_ref(repo_num, project_key, n))
    return out


def parse_jira_refs(text: str | None, known_project_keys: set[str] | None) -> list[IssueRef]:
    """Every `PROJ-123` token whose `PROJ` is a *known* project key. With no known keys
    (`None`/empty -- no Jira connector configured) this returns nothing: an un-vetted
    `PROJ-N` token is far more often `UTF-8`/`ISO-8601` than a real ticket (§3.2)."""
    keys = {k.upper() for k in (known_project_keys or set())}
    if not keys or not text:
        return []
    out: list[IssueRef] = []
    seen: set[tuple[str, int]] = set()
    for m in _JIRA_REF_RE.finditer(text):
        proj, num = m.group(1).upper(), int(m.group(2))
        if proj in _NEVER_JIRA or proj not in keys:
            continue
        if (proj, num) not in seen:
            seen.add((proj, num))
            out.append(IssueRef("jira", proj, num, None))
    return out
