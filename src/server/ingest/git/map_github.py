"""Pure payload -> artifact mapping (git-source.md §5.3/§5.4/§5.6/§5.7).

One function per webhook event; `map_delivery` dispatches by event name. Deterministic and
side-effect-free -- the same payload maps to the same artifacts every time (the idempotency
`upsert_artifact`'s last-writer-wins on `updated_at` depends on), and this module never
performs I/O. That purity is the whole reason it will also be the backfill readers' mapper
(implementation plan A5, git-source.md §4.2, §8.2): a GraphQL page and a REST response feed
the identical function a webhook does, so all three readers cannot drift.

`org_id` is an INPUT, not something this module resolves: the installation is the unit of
`org_id` resolution (§6.1), and resolving it needs a `git_installations` lookup this pure
function cannot make. The worker (`server/worker/git_map.py`) resolves it once per delivery
and passes it in here.

What is never stored, at any point: diff text, patch hunks, file contents, review comment
bodies, PR/issue body text (§5.3). The only body text this module reads at all is the PR
body, and only to regex two references out of it -- the "closes #n" issue link and the
"This reverts commit <sha>" line -- discarding the rest immediately, never retaining it.

**Sticky fields, deliberately left `None` here.** `files` (paths), `commit_shas`,
`before_sha`, `after_sha`, `integration_method`, and `user_id` are things this module often
cannot know from one event alone -- a webhook `pull_request` payload carries no file-path
list or commit-sha list at all (those need the backfill reader or push-correlation, A5/§5.7),
and `user_id` is never set by this module (§6.3, A6). Emitting `None` for "I don't know" lets
`upsert_artifact`'s COALESCE preserve whatever a different pass already established, rather
than a blind overwrite erasing it on the next ordinary webhook.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .linkage import resolve_links

_BOT_SUFFIX = "[bot]"
_REVERTS_RE = re.compile(r"[Tt]his reverts commit ([0-9a-fA-F]{7,40})")
_CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*#(\d+)", re.IGNORECASE)
#: GitHub inlines up to this many commits per push; a push at or over this is treated as
#: possibly truncated and mapped as a range, never as (possibly incomplete) per-commit
#: artifacts (git-source.md §5.4, §12).
PUSH_TRUNCATE_AT = 2048

#: pull_request actions v1 maps (git-source.md §5.4).
_PR_ACTIONS = {"opened", "reopened", "synchronize", "closed", "ready_for_review", "edited"}
_REVIEW_ACTIONS = {"submitted", "dismissed"}
_COMMENT_ACTIONS = {"created", "deleted"}
#: `issues` actions that carry a full issue object worth (re)mapping (ISSUE §3.1). Every one
#: of these ships the same complete `issue` payload, so the mapping is action-independent --
#: exactly as `_PR_ACTIONS` is for `pull_request`. `deleted`/`transferred` are deliberately
#: absent: the row is retained as history (§5.4), nothing to upsert.
_ISSUE_ACTIONS = {
    "opened", "edited", "closed", "reopened", "labeled", "unlabeled",
    "milestoned", "demilestoned", "assigned", "unassigned", "pinned", "unpinned",
}
_INSTALL_ACTIONS = {"created": "active", "deleted": "deleted", "suspend": "suspended", "unsuspend": "active"}


@dataclass
class MapResult:
    """What `map_delivery` produced. `mapped=False` is a first-class outcome (§5.2) -- the
    worker marks the delivery `unmapped`, not a failure."""

    mapped: bool
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    #: [{"artifact_id", "field", "delta"}] -- an atomic counter bump the worker applies with
    #: its own SQL; jsonb arithmetic needs the live row, which a pure function cannot see.
    bump_counters: list[dict[str, Any]] = field(default_factory=list)
    repos: list[dict[str, Any]] = field(default_factory=list)
    installation: dict[str, Any] | None = None
    #: Only for a merged `pull_request`: the sha the worker looks up among existing `commit`
    #: artifacts to see whether the correlating push already landed (§5.7).
    merge_commit_sha: str | None = None
    #: Only for a `push`: what the worker tries to correlate INTO an existing PR (§5.7).
    push_before: str | None = None
    push_after: str | None = None
    push_integration_method: str | None = None
    push_commit_shas: list[str] | None = None
    #: Only for a `pull_request`: the resolved PR->issue links (ISSUE §3.2, H3), each a
    #: `{"pr_artifact_id", "tracker", "project_key", "number", "issue_artifact_id", "method",
    #: "confidence"}` dict. `apply.py` persists these to `pr_issue_links`. `None` (not `[]`)
    #: for any non-PR result, so `apply.py` can tell "not a PR" from "a PR with no links".
    issue_links: list[dict[str, Any]] | None = None
    #: Only for a `pull_request`: the state transition this delivery observed, a
    #: `{"artifact_id", "action", "to_state", "occurred_at", "source_key"}` dict `apply.py`
    #: appends to `git_artifact_events` (Track churn/reopen). `git_artifacts.state` is
    #: current-state-only, so a reopen COUNT needs this history. `None` for any non-PR result.
    #: `source_key` (<artifact_id>:<action>:<occurred_at>) makes the append idempotent under
    #: delivery replay without threading a `delivery_id` down here.
    pr_state_event: dict[str, Any] | None = None


def _is_bot(login: str | None) -> bool:
    return bool(login) and login.endswith(_BOT_SUFFIX)


def _artifact_id(repo_num: int, kind: str, key: Any) -> str:
    return f"github:{repo_num}:{kind}:{key}"


def _repo_id(repo_num: int) -> str:
    return f"github:{repo_num}"


def _is_revert_title(text: str | None) -> bool:
    return bool(text) and text.startswith('Revert "')


def _parse_reverts_sha(text: str | None) -> str | None:
    if not text:
        return None
    m = _REVERTS_RE.search(text)
    return m.group(1) if m else None


def _parse_linked_issue(repo_num: int, body: str | None) -> str | None:
    if not body:
        return None
    m = _CLOSES_RE.search(body)
    return _artifact_id(repo_num, "issue", int(m.group(1))) if m else None


def pr_issue_links(
    pr_artifact_id: str, repo_num: int, *,
    closing_issue_numbers: list[int] | None = None,
    commit_messages: list[str] | None = None,
    branch: str | None = None, title: str | None = None, body: str | None = None,
) -> list[dict[str, Any]]:
    """Resolve a PR's issue links via the shared §3.2 ladder (`linkage.resolve_links`) and
    shape them for `pr_issue_links` (ISSUE H3). A reader passes whichever inputs its wire shape
    carries -- only GraphQL has `closing_issue_numbers` and `commit_messages`; the webhook and
    REST paths reach the branch/title/body rungs only. `project_key` for a GitHub ref is the
    `repo_num` as a string (the human-readable `owner/name` is a read-time label, exactly as
    `git_repos.full_name` is a label over `repo_id`, git-source.md §6.2)."""
    links = resolve_links(
        repo_num=repo_num, project_key=str(repo_num),
        closing_issue_numbers=closing_issue_numbers, commit_messages=commit_messages,
        branch=branch, title=title, body=body,
    )
    return [{
        "pr_artifact_id": pr_artifact_id,
        "tracker": lk.ref.tracker, "project_key": lk.ref.project_key, "number": lk.ref.number,
        "issue_artifact_id": lk.ref.artifact_id,
        "method": lk.method, "confidence": lk.confidence,
    } for lk in links]


def _branch_from_ref(ref: str | None) -> str | None:
    if not ref:
        return None
    return ref.removeprefix("refs/heads/").removeprefix("refs/tags/")


def _infer_integration_method(commits: list[dict], merge_commit_sha: str) -> str | None:
    """Best-effort, from webhook data alone -- no extra API call (§5.7's "captured, not
    reconstructed" push). `integration_method` genuinely needs the merge commit's parent
    count, which a push payload doesn't carry; these are the closest observable proxies:

    - exactly one new commit, and it IS the merge commit -> squash (a squash merge produces
      one new commit on the base branch, by definition).
    - the merge commit is the newest of several, and its message has GitHub's own
      auto-generated "Merge pull request" prefix -> merge.
    - the merge commit is the newest of several but the message doesn't match -> rebase (each
      original commit replayed with a new sha; no distinct merge commit is created).

    A5's compare-API fallback can firm this up later; this is the v1 approximation.
    """
    if not commits:
        return None
    if len(commits) == 1 and commits[0].get("id") == merge_commit_sha:
        return "squash"
    head = commits[-1]
    if head.get("id") != merge_commit_sha:
        return None
    if (head.get("message") or "").startswith("Merge pull request"):
        return "merge"
    return "rebase"


#: The `pull_request` actions that ARE a lifecycle state transition worth logging to
#: `git_artifact_events` -- `synchronize`/`edited`/`ready_for_review` change a PR without
#: changing its open/closed/merged state, so they record nothing. A `closed` action carrying
#: `merged=true` is logged as `"merged"` so the event `action` matches the artifact `state`
#: vocabulary rather than the raw webhook action.
_PR_TRANSITION_ACTIONS = {"opened", "reopened", "closed"}


def _pr_state_event(artifact_id: str, action: str, merged: bool, state: str,
                    updated_at: str | None) -> dict[str, Any] | None:
    if action not in _PR_TRANSITION_ACTIONS or updated_at is None:
        return None
    event_action = "merged" if (action == "closed" and merged) else action
    return {
        "artifact_id": artifact_id, "action": event_action, "to_state": state,
        "occurred_at": updated_at,
        "source_key": f"{artifact_id}:{event_action}:{updated_at}",
    }


def _map_pull_request(action: str, payload: dict, org_id: str) -> MapResult:
    if action not in _PR_ACTIONS:
        return MapResult(mapped=False)
    pr = payload["pull_request"]
    repo_num = payload["repository"]["id"]
    number = pr["number"]
    author = pr.get("user") or {}
    merged = bool(pr.get("merged"))
    state = "merged" if merged else pr.get("state", "open")
    title = pr.get("title")
    body = pr.get("body")
    is_revert = _is_revert_title(title)
    merge_commit_sha = pr.get("merge_commit_sha") if merged else None

    info: dict[str, Any] = {"github.draft": pr.get("draft", False)}
    if pr.get("merged_by"):
        info["github.merged_by"] = pr["merged_by"].get("login")
    if is_revert:
        info["github.is_revert"] = True

    artifact = {
        "artifact_id": _artifact_id(repo_num, "pr", number),
        "kind": "pr", "repo_id": _repo_id(repo_num), "org_id": org_id,
        "number": number, "sha": pr.get("head", {}).get("sha"),
        "author_login": author.get("login"), "author_email": None, "user_id": None,
        "is_bot": _is_bot(author.get("login")), "state": state, "title": title,
        "branch": pr.get("head", {}).get("ref"), "base_branch": pr.get("base", {}).get("ref"),
        "created_at": pr.get("created_at"), "updated_at": pr.get("updated_at"),
        "closed_at": pr.get("closed_at"), "merged_at": pr.get("merged_at"),
        "stats": {
            "additions": pr.get("additions"), "deletions": pr.get("deletions"),
            "changed_files": pr.get("changed_files"), "commits": pr.get("commits"),
        },
        "files": None,   # webhook payloads carry no file-path list (A5 fills this)
        "parent_artifact_id": None,
        "labels": [l.get("name") for l in (pr.get("labels") or [])],
        "milestone": (pr.get("milestone") or {}).get("title"),
        "linked_issue": _parse_linked_issue(repo_num, body),
        "merge_commit_sha": merge_commit_sha,
        "before_sha": None, "after_sha": None, "integration_method": None,
        "reverts_sha": _parse_reverts_sha(body) if is_revert else None,
        "commit_shas": None,   # webhook payloads carry no commit list (A5/§5.7 fill this)
        "info": info,
    }
    links = pr_issue_links(
        artifact["artifact_id"], repo_num,
        branch=pr.get("head", {}).get("ref"), title=title, body=body,
    )
    return MapResult(mapped=True, artifacts=[artifact], merge_commit_sha=merge_commit_sha,
                     issue_links=links,
                     pr_state_event=_pr_state_event(artifact["artifact_id"], action, merged,
                                                     state, pr.get("updated_at")))


def _map_pull_request_review(action: str, payload: dict, org_id: str) -> MapResult:
    if action not in _REVIEW_ACTIONS:
        return MapResult(mapped=False)
    review = payload["review"]
    pr = payload["pull_request"]
    repo_num = payload["repository"]["id"]
    author = review.get("user") or {}
    pr_artifact_id = _artifact_id(repo_num, "pr", pr["number"])

    artifact = {
        "artifact_id": _artifact_id(repo_num, "review", review["id"]),
        "kind": "review", "repo_id": _repo_id(repo_num), "org_id": org_id,
        "number": pr["number"], "sha": review.get("commit_id"),
        "author_login": author.get("login"), "author_email": None, "user_id": None,
        "is_bot": _is_bot(author.get("login")), "state": review.get("state"),
        "title": None, "branch": None, "base_branch": None,
        "created_at": review.get("submitted_at"), "updated_at": review.get("submitted_at"),
        "closed_at": None, "merged_at": None,
        "stats": {}, "files": None, "parent_artifact_id": pr_artifact_id,
        "labels": [], "milestone": None, "linked_issue": None,
        "merge_commit_sha": None, "before_sha": None, "after_sha": None,
        "integration_method": None, "reverts_sha": None, "commit_shas": None,
        "info": {},
    }
    # `review_count` is NOT bumped here: it is recomputed from actual review rows
    # (`recompute_review_count`, apply.py), which is idempotent under replay in a way a delta
    # bump on a "submitted" action cannot be (a mapper-defect re-run of this same delivery
    # would double it, breaking "re-running a delivery changes nothing," §10).
    return MapResult(mapped=True, artifacts=[artifact])


def _map_comment_bump(action: str, artifact_id: str, comment_id: Any) -> MapResult:
    """Shared by `pull_request_review_comment`, `issue_comment`-on-a-PR, and
    `issue_comment`-on-a-plain-issue (§5.4, ISSUE H1): none of the three comments gets its own
    artifact, each just bumps its parent artifact's `comment_count` -- the parent being a `pr`
    for the first two and an `issue` for the third.

    `dedup_key` makes the bump idempotent under replay (a mapper-defect re-run, §10): a comment
    never gets its own artifact row to COUNT(*) over the way a review does, so the dedup has to
    travel with the bump itself. Keyed on `(comment_id, action)`, not `comment_id` alone, so a
    `created` and a later `deleted` for the SAME comment are two distinct once-each events
    rather than the second being mistaken for a replay of the first."""
    if action not in _COMMENT_ACTIONS:
        return MapResult(mapped=False)
    delta = 1 if action == "created" else -1
    return MapResult(mapped=True, bump_counters=[{
        "artifact_id": artifact_id, "field": "comment_count", "delta": delta,
        "dedup_key": f"{comment_id}:{action}",
    }])


def _map_issue_comment(action: str, payload: dict) -> MapResult:
    """A comment on a PR bumps the `pr` artifact; a comment on a plain issue bumps the `issue`
    artifact (ISSUE H1 -- previously "landed, not mapped"). The `pull_request` key on the issue
    object is GitHub's own discriminator between the two."""
    issue = payload["issue"]
    repo_num = payload["repository"]["id"]
    comment_id = (payload.get("comment") or {}).get("id")
    kind = "pr" if "pull_request" in issue else "issue"
    return _map_comment_bump(action, _artifact_id(repo_num, kind, issue["number"]), comment_id)


def _map_pull_request_review_comment(action: str, payload: dict) -> MapResult:
    repo_num = payload["repository"]["id"]
    comment_id = (payload.get("comment") or {}).get("id")
    return _map_comment_bump(action, _artifact_id(repo_num, "pr", payload["pull_request"]["number"]), comment_id)


def issue_artifact(
    repo_num: int, org_id: str, *, number: int, state: str | None, title: str | None,
    author_login: str | None, labels: list[str], milestone: str | None,
    assignees: list[str], parent_number: int | None,
    created_at: str | None, updated_at: str | None, closed_at: str | None,
    comment_count: int | None,
) -> dict[str, Any]:
    """The single issue field-mapping every reader converges on (ISSUE §0.2, §3.1): the webhook
    mapper (`_map_issues`), the GraphQL backfill reader and the REST gap-fill reader all build
    their `issue` artifact through *this* function from normalized kwargs, so a webhook-, a
    GraphQL- and a REST-mapped issue for the same number are byte-identical by construction, not
    by convention (the parity rule SIG §3.6 states for PRs, extended to issues).

    Issues reuse the PR pipeline unchanged -- same table, same upsert, same last-writer-wins on
    `updated_at`; the PR-only columns (`sha`, `branch`, `files`, `merge_commit_sha`, ...) simply
    stay `None`. **Never carries issue body text** (§5.3) -- title, state, labels, milestone,
    counts, timestamps and refs only.

    `linked_issue` carries a parent/epic ref only when a caller passes a `parent_number`
    outright (GitHub's sub-issue parent); it is never inferred from a milestone or a label here
    -- that grouping choice belongs to the feature resolver (H5), not the mapper."""
    info: dict[str, Any] = {}
    clean_assignees = [a for a in (assignees or []) if a]
    if clean_assignees:
        info["github.assignees"] = clean_assignees
    return {
        "artifact_id": _artifact_id(repo_num, "issue", number),
        "kind": "issue", "repo_id": _repo_id(repo_num), "org_id": org_id,
        "number": number, "sha": None,
        "author_login": author_login, "author_email": None, "user_id": None,
        "is_bot": _is_bot(author_login), "state": state or "open",
        "title": title, "branch": None, "base_branch": None,
        "created_at": created_at, "updated_at": updated_at,
        "closed_at": closed_at, "merged_at": None,
        "stats": {"comment_count": comment_count},
        "files": None, "parent_artifact_id": None,
        "labels": list(labels or []),
        "milestone": milestone,
        "linked_issue": _artifact_id(repo_num, "issue", parent_number) if parent_number else None,
        "merge_commit_sha": None, "before_sha": None, "after_sha": None,
        "integration_method": None, "reverts_sha": None, "commit_shas": None,
        "info": info,
    }


def _map_issues(action: str, payload: dict, org_id: str) -> MapResult:
    """Map an `issues` webhook to a `kind="issue"` artifact via the shared `issue_artifact`
    builder (ISSUE §3.1, H1). Action-independent, exactly as `_map_pull_request` is: every
    action in `_ISSUE_ACTIONS` ships the same full `issue` object."""
    if action not in _ISSUE_ACTIONS:
        return MapResult(mapped=False)
    issue = payload["issue"]
    if "pull_request" in issue:
        # GitHub does not fire `issues` for pull requests; if one ever arrives, it is the
        # `pull_request` event's to map, not this one's.
        return MapResult(mapped=False)
    repo_num = payload["repository"]["id"]
    author = issue.get("user") or {}
    parent = issue.get("parent") or {}
    artifact = issue_artifact(
        repo_num, org_id,
        number=issue["number"], state=issue.get("state"), title=issue.get("title"),
        author_login=author.get("login"),
        labels=[l.get("name") for l in (issue.get("labels") or [])],
        milestone=(issue.get("milestone") or {}).get("title"),
        assignees=[a.get("login") for a in (issue.get("assignees") or [])],
        parent_number=parent.get("number"),
        created_at=issue.get("created_at"), updated_at=issue.get("updated_at"),
        closed_at=issue.get("closed_at"), comment_count=issue.get("comments"),
    )
    return MapResult(mapped=True, artifacts=[artifact])


def _map_push(payload: dict, org_id: str) -> MapResult:
    repo_num = payload["repository"]["id"]
    commits = payload.get("commits") or []
    before, after = payload.get("before"), payload.get("after")
    branch = _branch_from_ref(payload.get("ref"))

    if len(commits) >= PUSH_TRUNCATE_AT:
        # A history-import-sized push: record the range only; the compare API in gap-fill
        # completes it (§5.4, §8.2) -- never map a possibly-incomplete per-commit list.
        range_artifact = {
            "artifact_id": _artifact_id(repo_num, "commit", f"{before}..{after}"),
            "kind": "commit", "repo_id": _repo_id(repo_num), "org_id": org_id,
            "number": None, "sha": after, "author_login": None, "author_email": None,
            "user_id": None, "is_bot": False, "state": "pushed", "title": None,
            "branch": branch, "base_branch": None,
            "created_at": payload.get("head_commit", {}).get("timestamp"),
            "updated_at": payload.get("head_commit", {}).get("timestamp"),
            "closed_at": None, "merged_at": None,
            "stats": {}, "files": None, "parent_artifact_id": None,
            "labels": [], "milestone": None, "linked_issue": None,
            "merge_commit_sha": None, "before_sha": before, "after_sha": after,
            "integration_method": None, "reverts_sha": None, "commit_shas": None,
            "info": {"github.truncated_push": True, "github.commit_count_inlined": len(commits)},
        }
        return MapResult(mapped=True, artifacts=[range_artifact])

    integration_method = _infer_integration_method(commits, after) if after else None
    commit_shas = [c["id"] for c in commits]
    artifacts = []
    for i, c in enumerate(commits):
        is_head = i == len(commits) - 1
        is_revert = c.get("message", "").startswith("Revert ")
        author = c.get("author") or {}
        info: dict[str, Any] = {}
        if is_revert:
            info["github.is_revert"] = True
        if is_head:
            # Stash this push's transition data on the head commit so a `pull_request`
            # merged event mapped BEFORE this push can still find it (§5.7's "either order"
            # requirement) -- `get_commit_info_by_sha` is how the worker reads it back.
            info["github.push_meta"] = {
                "before_sha": before, "after_sha": after,
                "integration_method": integration_method, "commit_shas": commit_shas,
            }
        artifacts.append({
            "artifact_id": _artifact_id(repo_num, "commit", c["id"]),
            "kind": "commit", "repo_id": _repo_id(repo_num), "org_id": org_id,
            "number": None, "sha": c["id"],
            "author_login": author.get("username"), "author_email": author.get("email"),
            "user_id": None, "is_bot": _is_bot(author.get("username")), "state": "pushed",
            "title": None, "branch": branch, "base_branch": None,
            "created_at": c.get("timestamp"), "updated_at": c.get("timestamp"),
            "closed_at": None, "merged_at": None,
            "stats": {},
            "files": (
                [{"path": p, "op": "added", "additions": None, "deletions": None} for p in c.get("added", [])]
                + [{"path": p, "op": "removed", "additions": None, "deletions": None} for p in c.get("removed", [])]
                + [{"path": p, "op": "modified", "additions": None, "deletions": None} for p in c.get("modified", [])]
            ),
            "parent_artifact_id": None, "labels": [], "milestone": None, "linked_issue": None,
            "merge_commit_sha": None, "before_sha": None, "after_sha": None,
            "integration_method": None,
            "reverts_sha": _parse_reverts_sha(c.get("message")) if is_revert else None,
            "commit_shas": None, "info": info,
        })
    return MapResult(
        mapped=True, artifacts=artifacts,
        push_before=before, push_after=after,
        push_integration_method=integration_method, push_commit_shas=commit_shas,
    )


def _map_check_suite(action: str, payload: dict, org_id: str) -> MapResult:
    if action != "completed":
        return MapResult(mapped=False)
    suite = payload["check_suite"]
    repo_num = payload["repository"]["id"]
    prs = suite.get("pull_requests") or []
    parent = _artifact_id(repo_num, "pr", prs[0]["number"]) if prs else None

    artifact = {
        "artifact_id": _artifact_id(repo_num, "check", suite["id"]),
        "kind": "check", "repo_id": _repo_id(repo_num), "org_id": org_id,
        "number": prs[0]["number"] if prs else None, "sha": suite.get("head_sha"),
        "author_login": None, "author_email": None, "user_id": None, "is_bot": False,
        "state": suite.get("conclusion") or "neutral", "title": None,
        "branch": suite.get("head_branch"), "base_branch": None,
        "created_at": suite.get("created_at"), "updated_at": suite.get("updated_at"),
        "closed_at": None, "merged_at": None,
        "stats": {}, "files": None, "parent_artifact_id": parent,
        "labels": [], "milestone": None, "linked_issue": None,
        "merge_commit_sha": None, "before_sha": None, "after_sha": None,
        "integration_method": None, "reverts_sha": None, "commit_shas": None,
        "info": {"github.app": (suite.get("app") or {}).get("name")},
    }
    return MapResult(mapped=True, artifacts=[artifact])


def _map_installation(action: str, payload: dict, org_id: str) -> MapResult:
    state = _INSTALL_ACTIONS.get(action)
    if state is None:
        return MapResult(mapped=False)
    inst = payload["installation"]
    account = inst.get("account") or {}
    installation = {
        "installation_id": inst["id"], "org_id": org_id,
        "account_login": account.get("login"), "account_type": account.get("type"),
        "repo_selection": inst.get("repository_selection"), "state": state,
        "info": {},
    }
    repos = []
    if action == "created":
        for r in payload.get("repositories") or []:
            owner, _, _name = r.get("full_name", "").partition("/")
            repos.append({
                "repo_id": _repo_id(r["id"]), "repo_num": r["id"], "full_name": r.get("full_name"),
                "owner": owner, "default_branch": "", "private": bool(r.get("private")),
                "installation_id": inst["id"], "org_id": org_id, "info": {},
            })
    return MapResult(mapped=True, installation=installation, repos=repos)


def _map_installation_repositories(action: str, payload: dict, org_id: str) -> MapResult:
    if action != "added":
        # "removed" drops repos from scope; the row is retained as history (§5.4), nothing to
        # upsert. Neither action is an error, both are mapped outcomes.
        return MapResult(mapped=True)
    inst = payload["installation"]
    repos = []
    for r in payload.get("repositories_added") or []:
        owner, _, _name = r.get("full_name", "").partition("/")
        repos.append({
            "repo_id": _repo_id(r["id"]), "repo_num": r["id"], "full_name": r.get("full_name"),
            "owner": owner, "default_branch": "", "private": bool(r.get("private")),
            "installation_id": inst["id"], "org_id": org_id, "info": {},
        })
    return MapResult(mapped=True, repos=repos)


def map_delivery(event: str, action: str | None, payload: dict, org_id: str) -> MapResult:
    """The one dispatch point every reader (webhook, and later GraphQL/REST backfill, A5)
    calls. `mapped=False` means "landed, not mapped in v1" (§5.2) -- deliberate, not a bug."""
    if event == "pull_request":
        return _map_pull_request(action or "", payload, org_id)
    if event == "pull_request_review":
        return _map_pull_request_review(action or "", payload, org_id)
    if event == "pull_request_review_comment":
        return _map_pull_request_review_comment(action or "", payload)
    if event == "issue_comment":
        return _map_issue_comment(action or "", payload)
    if event == "issues":
        return _map_issues(action or "", payload, org_id)
    if event == "push":
        return _map_push(payload, org_id)
    if event == "check_suite":
        return _map_check_suite(action or "", payload, org_id)
    if event == "installation":
        return _map_installation(action or "", payload, org_id)
    if event == "installation_repositories":
        return _map_installation_repositories(action or "", payload, org_id)
    # Anything else (`fork`, `star`, `create`, ...): landed, not mapped (§5.2) -- a
    # first-class outcome, not a bug.
    return MapResult(mapped=False)
