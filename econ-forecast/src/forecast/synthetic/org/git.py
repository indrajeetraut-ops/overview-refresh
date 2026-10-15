"""GitHub artifacts, events, links, and rework (plan §5 P6): commits, PRs, reviews, issues,
reverts, `pr_issue_links`, `session_git_links`, `pr_rework`.

Artifact dicts share ONE key set across every `kind` (pr/issue/commit/review), matching
`server.ingest.git.map_github`'s mapper output exactly (D12; `test_artifact_keyset_matches_mapper`
checks this) -- fields that don't apply to a kind are `None`/`[]`/`{}`, the same way the real
mapper leaves them for webhook payloads that don't carry that information.

**Implementation judgment calls** (documented here and in the progress log):
- Numbering is a final pass, done AFTER commits/reviews/outcomes/reverts/issues are otherwise
  complete: PR title (`title` method) and branch (`branch` method) embellishments reference an
  issue number, which does not exist before an issue is numbered, so those two fields are
  finalized in the same pass that assigns numbers.
- "Reviewers ... take 1-2" has no stated split probability; implemented as `randint(1, 2)`
  (clamped to the candidate pool size), drawn from the `review` stream.
- The reopen check ("`reopen_rate` (merged path only): insert...") reads as a probability gate
  (`r.random() < reopen_rate`) before the insertion, drawn from the `outcome` stream right after
  the merge-delay draw.
- "the lead of the root-nearest other team" (the reviewer fallback when a team has only one
  member): read as the first OTHER team in `resolved.team_order` (deterministic, not spatial --
  there is no team hierarchy distance defined anywhere else in the plan).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Mapping
from zoneinfo import ZoneInfo

from common.msging.log import get_logger
from ..allocation import allocate_integer
from ..randomness import rng_for
from . import ids, lexicon
from .config import ResolvedOrg
from .directory import resolve_leads
from .models import synthetic_marker
from .sessions import SessionPlan
from .work import WorkItem, WorkResult

#: linkage.py:41's `_CONFIDENCE`, copied for the three methods this package ever produces
#: (`graphql`/`branch`/`title`; `trailer`/`body` never appear here). `test_link_confidence_parity`
#: checks these three values against the real dict.
_LINK_CONFIDENCE = {"graphql": 1.0, "branch": 0.7, "title": 0.6}

_LABELS = {"feature": ["enhancement"], "fix": ["bug"], "refactor": ["refactor"],
          "docs": ["documentation"], "test": ["tests"], "chore": ["chore"]}

_ARTIFACT_KEYS = (
    "artifact_id", "kind", "repo_id", "org_id", "number", "sha", "author_login", "author_email",
    "user_id", "is_bot", "state", "title", "branch", "base_branch", "created_at", "updated_at",
    "closed_at", "merged_at", "stats", "files", "parent_artifact_id", "labels", "milestone",
    "linked_issue", "merge_commit_sha", "before_sha", "after_sha", "integration_method",
    "reverts_sha", "commit_shas", "info",
)


def _shell(repo_num: int, kind: str, key, org_id: str) -> dict:
    row = {k: None for k in _ARTIFACT_KEYS}
    row.update(artifact_id=ids.artifact_id(repo_num, kind, key), kind=kind,
              repo_id=f"github:{repo_num}", org_id=org_id, is_bot=False, stats={}, labels=[],
              info={})
    return row


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _event(artifact_id: str, action: str, to_state: str, occurred_at: str) -> dict:
    """A `git_artifact_events` row. `recorded_at = occurred_at` (plan §5 P6 step 5) -- this
    generator has no separate ingest-time to distinguish it from the event's own time."""
    return {"artifact_id": artifact_id, "action": action, "to_state": to_state,
           "occurred_at": occurred_at, "recorded_at": occurred_at,
           "source_key": f"{artifact_id}:{action}:{occurred_at}"}


def _slug(text: str, limit: int = 40) -> str:
    cleaned = "".join(c.lower() if c.isalnum() else "-" for c in text)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:limit] or "change"


@dataclass
class _PrDraft:
    item: WorkItem
    pr_key: str
    artifact: dict
    events: list[dict] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)
    commits: list[dict] = field(default_factory=list)
    session_first_commit: dict[str, str] = field(default_factory=dict)  # session_id -> sha
    is_revert: bool = False
    orig_pr_key: str | None = None


@dataclass
class GitResult:
    artifacts: list[dict]
    events: list[dict]
    pr_issue_links: list[dict]
    pr_rework: list[dict]
    session_links: list[dict]
    pr_list_by_session: dict[str, list[dict]]


def _team_of(resolved: ResolvedOrg, l1_user: str) -> str:
    return next(t.team_name for t in resolved.teams if l1_user in t.user_ids)


def _reviewer_pool(resolved: ResolvedOrg, l1_user: str, leads: Mapping[str, str]) -> list[str]:
    team_name = _team_of(resolved, l1_user)
    team = resolved.teams_by_name[team_name]
    others = sorted(uid for uid in team.user_ids if uid != l1_user)
    if others:
        return others
    for other_team in resolved.team_order:
        if other_team == team_name:
            continue
        lead = leads.get(other_team)
        if lead:
            return [lead]
    return [f"__admin__{resolved.config.org.admin.github_login}"]


def _login_and_user_id(resolved: ResolvedOrg, l1_user_or_admin_marker: str) -> tuple[str, str]:
    if l1_user_or_admin_marker.startswith("__admin__"):
        login = l1_user_or_admin_marker.removeprefix("__admin__")
        return login, f"github:{login}"
    user = resolved.users_by_id[l1_user_or_admin_marker]
    return user.github_login, f"github:{user.github_login}"


def _finalize_commits(resolved: ResolvedOrg, item: WorkItem, pr_key: str, branch: str,
                      author_login: str, author_user_id: str, org_id: str, repo_num: int,
                      pr_artifact_id: str, plans_by_id: Mapping[str, SessionPlan],
                      rounds: int) -> tuple[list[dict], dict[str, str]]:
    seed = resolved.config.org.seed
    team = resolved.team_effective[_team_of(resolved, item.l1_user)]
    marker = synthetic_marker(seed)
    commits: list[dict] = []
    session_first_commit: dict[str, str] = {}
    per_session_n: dict[str, int] = {}
    for sid in item.sessions:
        r = rng_for(seed, "commits", sid)
        n_c = int(ids.choice(dict(team.pr.commits_per_session), r))
        force = sid == item.opening_session_id or (item.finalized_at and sid == item.sessions[-1])
        if force:
            n_c = max(1, n_c)
        per_session_n[sid] = n_c

    total_commits = sum(per_session_n.values())
    if total_commits == 0:
        return [], {}

    churn_r = rng_for(seed, "commits", pr_key, "churn")
    churn_extra = rounds * churn_r.uniform(0.05, 0.2)
    total_lines = round(item.size_loc * (1 + churn_extra))
    weight_r = rng_for(seed, "commits", pr_key, "weights")
    weights = {str(i): weight_r.lognormvariate(0, 0.5) for i in range(total_commits)}
    line_alloc = allocate_integer(total_lines, weights)

    index = 0
    for sid in item.sessions:
        n_c = per_session_n[sid]
        if n_c == 0:
            continue
        plan = plans_by_id[sid]
        span = (plan.ended_at - plan.started_at).total_seconds()
        time_r = rng_for(seed, "commits", sid, "times")
        offsets = sorted(time_r.uniform(0, span) for _ in range(n_c))
        for offset in offsets:
            lines = line_alloc[str(index)]
            deletions = round(lines * team.pr.deletion_share)
            additions = lines - deletions
            sha = ids.sha(item.repo.name, pr_key, str(index))
            ts = plan.started_at + timedelta(seconds=offset)
            commit = {
                **_shell(0, "commit", sha, org_id),
                "artifact_id": ids.artifact_id(repo_num, "commit", sha),
                "repo_id": f"github:{repo_num}", "sha": sha, "state": "pushed",
                "branch": branch, "author_login": author_login, "user_id": author_user_id,
                "stats": {"additions": additions, "deletions": deletions},
                "created_at": _iso(ts), "updated_at": _iso(ts),
                "parent_artifact_id": pr_artifact_id, "info": {"synthetic": marker},
            }
            commits.append(commit)
            if sid not in session_first_commit:
                session_first_commit[sid] = sha
            index += 1

    if commits:
        commits[-1]["info"] = {
            **commits[-1]["info"],
            "github.push_meta": {
                "before_sha": ids.sha(item.repo.name, pr_key, "before"),
                "after_sha": commits[-1]["sha"],
                "integration_method": None,
                "commit_shas": [c["sha"] for c in commits],
            },
        }
    return commits, session_first_commit


def _build_files(resolved: ResolvedOrg, item: WorkItem, pr_key: str) -> tuple[list[dict], int, int]:
    team = resolved.team_effective[_team_of(resolved, item.l1_user)]
    r = rng_for(resolved.config.org.seed, "files", "pr", pr_key)
    total_lines = item.size_loc
    deletions_total = round(total_lines * team.pr.deletion_share)
    additions_total = total_lines - deletions_total
    n_files = max(1, min(round(item.size_loc / 40 * r.uniform(0.6, 1.4)), 60))

    entries = []
    for i in range(n_files):
        stem = r.choice(lexicon.FILE_STEMS)
        ext = ids.choice(dict(item.module.extensions), r)
        op = "added" if r.random() < 0.3 else "modified"
        entries.append({"path": f"{item.module.path}{stem}_{i}{ext}", "op": op})

    weight_r = rng_for(resolved.config.org.seed, "files", "pr", pr_key, "weights")
    weights = {str(i): weight_r.lognormvariate(0, 0.6) for i in range(n_files)}
    add_alloc = allocate_integer(additions_total, weights)
    modified = [i for i, e in enumerate(entries) if e["op"] == "modified"]
    del_targets = modified or list(range(n_files))
    del_weights = {str(i): weights[str(i)] for i in del_targets}
    del_alloc = allocate_integer(deletions_total, del_weights)

    files = []
    for i, entry in enumerate(entries):
        additions = add_alloc[str(i)]
        deletions = del_alloc.get(str(i), 0)
        files.append({"path": entry["path"], "op": entry["op"],
                      "additions": additions, "deletions": deletions})
    return files, additions_total, deletions_total


def _build_reviews_and_outcome(resolved: ResolvedOrg, item: WorkItem, pr_key: str,
                               pr_artifact_id: str, repo_num: int, org_id: str,
                               leads: Mapping[str, str], commits: list[dict],
                               series_end_local: datetime) -> tuple[list[dict], dict]:
    seed = resolved.config.org.seed
    team = resolved.team_effective[_team_of(resolved, item.l1_user)]
    r = rng_for(seed, "review", pr_key)
    outcome_r = rng_for(seed, "outcome", pr_key)
    marker = synthetic_marker(seed)

    pool = _reviewer_pool(resolved, item.l1_user, leads)
    shuffled = list(pool)
    r.shuffle(shuffled)
    reviewer_count = min(len(shuffled), r.randint(1, 2))
    reviewers = shuffled[:reviewer_count]

    opened_at = item.opened_at
    first_review_at = opened_at + timedelta(
        hours=r.lognormvariate(math.log(team.review.first_review_median_hours),
                               team.review.first_review_sigma))

    post_open_sessions = [s for s in item.sessions[1:]]  # sessions after the opening one, by order
    rounds = 0
    while rounds < 3 and r.random() < team.review.changes_requested_rate:
        rounds += 1
    rounds = min(rounds, len(post_open_sessions))

    def _sha_before(ts: datetime) -> str | None:
        candidates = [c for c in commits if c["created_at"] and
                     datetime.fromisoformat(c["created_at"]) <= ts]
        return candidates[-1]["sha"] if candidates else None

    reviews: list[dict] = []
    last_time = first_review_at
    for round_i in range(rounds):
        reviewer = reviewers[round_i % len(reviewers)]
        login, user_id = _login_and_user_id(resolved, reviewer)
        cr_time = last_time + timedelta(minutes=max(10, r.uniform(10, 120)))
        reviews.append({
            **_shell(repo_num, "review", f"{pr_key}:{len(reviews)}", org_id),
            "artifact_id": ids.artifact_id(repo_num, "review", ids.review_id(pr_key, len(reviews))),
            "number": item.seq, "sha": _sha_before(cr_time), "author_login": login,
            "user_id": user_id, "state": "changes_requested",
            "created_at": _iso(cr_time), "updated_at": _iso(cr_time),
            "parent_artifact_id": pr_artifact_id, "info": {"synthetic": marker},
        })
        last_time = cr_time
        if r.random() < team.review.commented_review_prob:
            comment_time = last_time + timedelta(minutes=max(10, r.uniform(5, 60)))
            reviewer2 = reviewers[(round_i + 1) % len(reviewers)]
            login2, user_id2 = _login_and_user_id(resolved, reviewer2)
            reviews.append({
                **_shell(repo_num, "review", f"{pr_key}:{len(reviews)}", org_id),
                "artifact_id": ids.artifact_id(repo_num, "review", ids.review_id(pr_key, len(reviews))),
                "number": item.seq, "sha": _sha_before(comment_time), "author_login": login2,
                "user_id": user_id2, "state": "commented",
                "created_at": _iso(comment_time), "updated_at": _iso(comment_time),
                "parent_artifact_id": pr_artifact_id, "info": {"synthetic": marker},
            })
            last_time = comment_time

    last_session_end = item.last_session_at
    approved_time = max(last_time, last_session_end) + timedelta(minutes=max(5, r.uniform(5, 30)))
    approver = reviewers[0]
    login, user_id = _login_and_user_id(resolved, approver)

    integration_method = ids.choice(dict(item.repo.integration_mix), outcome_r)
    abandon = outcome_r.random() < team.review.abandon_rate

    outcome: dict = {"integration_method": integration_method, "abandoned": abandon,
                     "reopen": None}
    if abandon:
        merge_delay = outcome_r.uniform(team.pr.merge_delay_hours.min, team.pr.merge_delay_hours.max)
        closed_at = last_session_end + timedelta(hours=merge_delay)
        if closed_at > series_end_local:
            outcome["state"] = "open"
            outcome["closed_at"] = None
        else:
            outcome["state"] = "closed"
            outcome["closed_at"] = closed_at
        outcome["merged_at"] = None
    else:
        approved_review_exists = approved_time <= series_end_local
        if not approved_review_exists:
            outcome["state"] = "open"
            outcome["closed_at"] = None
            outcome["merged_at"] = None
        else:
            reviews.append({
                **_shell(repo_num, "review", f"{pr_key}:{len(reviews)}", org_id),
                "artifact_id": ids.artifact_id(repo_num, "review", ids.review_id(pr_key, len(reviews))),
                "number": item.seq, "sha": _sha_before(approved_time), "author_login": login,
                "user_id": user_id, "state": "approved",
                "created_at": _iso(approved_time), "updated_at": _iso(approved_time),
                "parent_artifact_id": pr_artifact_id, "info": {"synthetic": marker},
            })
            merge_delay = outcome_r.uniform(team.pr.merge_delay_hours.min, team.pr.merge_delay_hours.max)
            merged_at = max(last_session_end, approved_time) + timedelta(hours=merge_delay)
            if merged_at > series_end_local:
                outcome["state"] = "open"
                outcome["closed_at"] = None
                outcome["merged_at"] = None
            else:
                outcome["state"] = "merged"
                outcome["merged_at"] = merged_at
                outcome["closed_at"] = merged_at
                if outcome_r.random() < team.review.reopen_rate:
                    t1 = opened_at + (merged_at - opened_at) / 2
                    window = min(timedelta(hours=48), merged_at - t1 - timedelta(minutes=10))
                    if window >= timedelta(hours=1):
                        reopened_at = t1 + timedelta(
                            hours=outcome_r.uniform(1, window.total_seconds() / 3600))
                        outcome["reopen"] = (t1, reopened_at)
    outcome["rounds"] = rounds
    return reviews, outcome


def _numbering_sort_key(entry: dict) -> tuple:
    return (entry["created_at"], entry["kind_order"], entry["key"])


def compute_git(resolved: ResolvedOrg, plans: list[SessionPlan], work: WorkResult,
                *, log=None) -> GitResult:
    log = log or get_logger(__name__)
    seed = resolved.config.org.seed
    org_id = resolved.config.org.directory_org_id
    marker = synthetic_marker(seed)
    leads = resolve_leads(resolved)
    plans_by_id = {p.session_id: p for p in plans}
    series = resolved.series.series
    series_end_local = datetime.combine(
        series.start_date + timedelta(days=series.days - 1), time(23, 59),
        tzinfo=ZoneInfo(series.timezone)).astimezone(timezone.utc)

    all_artifacts: list[dict] = []
    all_events: list[dict] = []
    all_links: list[dict] = []
    all_rework: list[dict] = []
    session_links: list[dict] = []
    pr_list_by_session: dict[str, list[dict]] = {}

    drafts_by_repo: dict[str, list[_PrDraft]] = {}
    items_by_id = work.items

    opened_items = [item for item in items_by_id.values() if item.has_pr]
    for item in opened_items:
        repo_num = ids.repo_num(f"{resolved.config.org.github_owner}/{item.repo.name}")
        pr_key = item.item_id
        pr_artifact_id = ids.artifact_id(repo_num, "pr", pr_key)
        author_login, author_user_id = _login_and_user_id(resolved, item.l1_user)
        branch_slug = _slug(item.change_type + "-" + item.module.path)
        branch = lexicon.branch(item.change_type, None, branch_slug)  # issue number backfilled below

        commits, session_first_commit = _finalize_commits(
            resolved, item, pr_key, branch, author_login, author_user_id, org_id, repo_num,
            pr_artifact_id, plans_by_id, rounds=0)
        reviews, outcome = _build_reviews_and_outcome(
            resolved, item, pr_key, pr_artifact_id, repo_num, org_id, leads, commits,
            series_end_local)
        # Rebuild commits with the real churn (rounds) now that outcome is known.
        commits, session_first_commit = _finalize_commits(
            resolved, item, pr_key, branch, author_login, author_user_id, org_id, repo_num,
            pr_artifact_id, plans_by_id, rounds=outcome["rounds"])

        files, additions, deletions = _build_files(resolved, item, pr_key)
        comment_r = rng_for(seed, "outcome", pr_key, "comments")
        comment_count = _poisson(team_pr_comments(resolved, item), comment_r)
        label_r = rng_for(seed, "outcome", pr_key, "labels")
        team = resolved.team_effective[_team_of(resolved, item.l1_user)]
        labels = list(_LABELS.get(item.change_type, [])) if label_r.random() < team.pr.label_prob else []

        merge_commit_sha = ids.sha(item.repo.name, pr_key, "merge") if outcome["state"] == "merged" else None
        before_sha = ids.sha(item.repo.name, pr_key, "before") if outcome["state"] == "merged" else None
        after_sha = ids.sha(item.repo.name, pr_key, "after") if outcome["state"] == "merged" else None

        title_base = lexicon.pr_title(rng_for(seed, "text", "pr", pr_key), item.change_type,
                                      item.module.path.strip("/").split("/")[-1] or item.repo.name)

        artifact = {
            **_shell(repo_num, "pr", pr_key, org_id),
            "artifact_id": pr_artifact_id, "number": None, "sha": commits[-1]["sha"] if commits else None,
            "author_login": author_login, "user_id": author_user_id, "state": outcome["state"],
            "title": title_base, "branch": branch, "base_branch": item.repo.default_branch,
            "created_at": _iso(item.opened_at), "updated_at": None,
            "closed_at": _iso(outcome.get("closed_at")), "merged_at": _iso(outcome.get("merged_at")),
            "stats": {"additions": additions, "deletions": deletions, "changed_files": len(files),
                     "commits": len(commits), "review_count": len(reviews),
                     "comment_count": comment_count},
            "files": files, "labels": labels, "linked_issue": None,
            "merge_commit_sha": merge_commit_sha, "before_sha": before_sha, "after_sha": after_sha,
            "integration_method": outcome["integration_method"], "commit_shas": [c["sha"] for c in commits],
            "info": {"github.draft": False, "github.merged_by": None, "synthetic": marker},
        }

        events = [_event(pr_artifact_id, "opened", "open", _iso(item.opened_at))]
        if outcome.get("reopen"):
            t1, reopened_at = outcome["reopen"]
            events.append(_event(pr_artifact_id, "closed", "closed", _iso(t1)))
            events.append(_event(pr_artifact_id, "reopened", "open", _iso(reopened_at)))
        if outcome["state"] == "merged":
            events.append(_event(pr_artifact_id, "merged", "merged", _iso(outcome["merged_at"])))
        elif outcome["state"] == "closed":
            events.append(_event(pr_artifact_id, "closed", "closed", _iso(outcome["closed_at"])))
        artifact["updated_at"] = max((e["occurred_at"] for e in events if e["occurred_at"]),
                                     default=_iso(item.opened_at))

        draft = _PrDraft(item=item, pr_key=pr_key, artifact=artifact, events=events,
                         reviews=reviews, commits=commits, session_first_commit=session_first_commit)
        drafts_by_repo.setdefault(item.repo.name, []).append(draft)

        if outcome["state"] == "merged" and item.feature_id:
            rounds = outcome["rounds"]
            rework_r = rng_for(seed, "rework", pr_key)
            ratio = round(min(0.95, rework_r.uniform(team.review.rework_ratio.min,
                                                     team.review.rework_ratio.max) * (1 + 0.5 * rounds)), 3)
            all_rework.append({"artifact_id": pr_artifact_id, "rework_ratio": ratio})

        # Reverts (step 6)
        if outcome["state"] == "merged":
            revert_r = rng_for(seed, "revert", pr_key)
            if revert_r.random() < team.review.revert_rate:
                revert_created = outcome["merged_at"] + timedelta(days=revert_r.uniform(0.5, 10))
                if revert_created <= series_end_local:
                    other_members = [u for u in resolved.teams_by_name[
                        _team_of(resolved, item.l1_user)].user_ids if u != item.l1_user]
                    revert_author_id = other_members[0] if other_members else item.l1_user
                    revert_login, revert_user_id = _login_and_user_id(resolved, revert_author_id)
                    revert_merge_delay = revert_r.uniform(0.5, 6)
                    revert_merged_at = revert_created + timedelta(hours=revert_merge_delay)
                    revert_pr_key = f"revert:{pr_key}"
                    revert_artifact_id = ids.artifact_id(repo_num, "pr", revert_pr_key)
                    revert_integration = ids.choice(dict(item.repo.integration_mix), revert_r)
                    revert_files = [
                        {"path": f["path"],
                         "op": ("removed" if f["op"] == "added" else
                               "added" if f["op"] == "removed" else "modified"),
                         "additions": f["deletions"], "deletions": f["additions"]}
                        for f in files
                    ]
                    revert_artifact = {
                        **_shell(repo_num, "pr", revert_pr_key, org_id),
                        "artifact_id": revert_artifact_id, "author_login": revert_login,
                        "user_id": revert_user_id, "state": "merged",
                        "title": f'Revert "{title_base}"',
                        "branch": f"revert-{revert_pr_key}-{branch_slug}",
                        "base_branch": item.repo.default_branch,
                        "created_at": _iso(revert_created), "updated_at": _iso(revert_merged_at),
                        "closed_at": _iso(revert_merged_at), "merged_at": _iso(revert_merged_at),
                        "stats": {"additions": deletions, "deletions": additions,
                                 "changed_files": len(files), "commits": 0, "review_count": 1,
                                 "comment_count": 0},
                        "files": revert_files, "labels": [],
                        "reverts_sha": merge_commit_sha,
                        "merge_commit_sha": ids.sha(item.repo.name, revert_pr_key, "merge"),
                        "before_sha": ids.sha(item.repo.name, revert_pr_key, "before"),
                        "after_sha": ids.sha(item.repo.name, revert_pr_key, "after"),
                        "integration_method": revert_integration, "commit_shas": [],
                        "info": {"github.draft": False, "github.merged_by": None,
                                "github.is_revert": True, "synthetic": marker},
                    }
                    lead_login, lead_user_id = _login_and_user_id(
                        resolved, leads.get(_team_of(resolved, item.l1_user), item.l1_user))
                    revert_review = {
                        **_shell(repo_num, "review", f"{revert_pr_key}:0", org_id),
                        "artifact_id": ids.artifact_id(repo_num, "review",
                                                       ids.review_id(revert_pr_key, 0)),
                        "sha": revert_artifact["merge_commit_sha"], "author_login": lead_login,
                        "user_id": lead_user_id, "state": "approved",
                        "created_at": _iso(revert_merged_at), "updated_at": _iso(revert_merged_at),
                        "parent_artifact_id": revert_artifact_id, "info": {"synthetic": marker},
                    }
                    revert_events = [
                        _event(revert_artifact_id, "opened", "open", _iso(revert_created)),
                        _event(revert_artifact_id, "merged", "merged", _iso(revert_merged_at)),
                    ]
                    revert_draft = _PrDraft(item=item, pr_key=revert_pr_key, artifact=revert_artifact,
                                            events=revert_events, reviews=[revert_review], commits=[],
                                            session_first_commit={}, is_revert=True,
                                            orig_pr_key=pr_key)
                    drafts_by_repo.setdefault(item.repo.name, []).append(revert_draft)

    # Issues (step 7): one per feature with a canonical (non-shared, method != none) issue, plus backlog.
    issues_by_repo: dict[str, list[dict]] = {}
    # issue_key (stable, pre-numbering) -> its artifact dict. `issue_key` never changes; the
    # numbering pass below rewrites `artifact_id` in place (D12: an issue's real artifact_id is
    # `github:<repo_num>:issue:<n>`, `n` the assigned number -- `quality.feature_by_id` parses
    # it that way), so lookups here must go through this stable key, never through `artifact_id`.
    issue_by_key: dict[str, dict] = {}
    feature_issue_key: dict[str, str] = {}   # feature_id -> issue key (its own, or the owner's)
    for feature in work.features.values():
        if feature.link_method == "none":
            continue
        owner = work.features[feature.issue_owner] if feature.issue_owner else feature
        feature_issue_key[feature.feature_id] = owner.feature_id

    canonical_features = {fid for fid in feature_issue_key.values()}
    for feature_id in canonical_features:
        feature = work.features[feature_id]
        sharing_features = [f for f in work.features.values()
                            if feature_issue_key.get(f.feature_id) == feature_id]
        team = resolved.team_effective[feature.team_name]
        repo_num = ids.repo_num(f"{resolved.config.org.github_owner}/{feature.repo.name}")
        text_r = rng_for(seed, "text", "issue", feature_id)
        author_pick = feature.l1_user if text_r.random() < 0.5 else leads.get(feature.team_name, feature.l1_user)
        author_login, author_user_id = _login_and_user_id(resolved, author_pick)
        first_item = work.items[feature.items[0]]
        module_name = first_item.module.path.strip("/").split("/")[-1] or feature.repo.name
        title = lexicon.issue_title(text_r, text_r.choice(lexicon.FEATURE_NOUNS), module_name)

        created_r = rng_for(seed, "text", "issue", feature_id, "created")
        start = feature.start or datetime.combine(
            resolved.series.series.start_date, time(0, 0), tzinfo=timezone.utc)
        created_at = start - timedelta(days=created_r.uniform(0, 10))
        earliest = datetime.combine(resolved.series.series.start_date - timedelta(days=30),
                                    time(0, 0), tzinfo=timezone.utc)
        created_at = max(created_at, earliest)

        all_pr_items = [work.items[iid] for f in sharing_features for iid in f.items]
        all_finalized = all(it.has_pr and (it.finalized_at is not None) for it in all_pr_items)
        last_activity = max((it.finalized_at for it in all_pr_items if it.finalized_at),
                            default=created_at)
        close_r = rng_for(seed, "text", "issue", feature_id, "close")
        candidate_close = last_activity + timedelta(days=close_r.uniform(0, 2))
        state = "closed" if all_finalized and candidate_close <= series_end_local else "open"
        closed_at = candidate_close if state == "closed" else None

        comment_r = rng_for(seed, "outcome", "issue", feature_id)
        assignee_logins = sorted({_login_and_user_id(resolved, it.l1_user)[0] for it in all_pr_items})
        change_type = first_item.change_type
        issue_labels = ["bug"] if change_type == "fix" else ["enhancement"]

        issue_key = feature_id
        issue_artifact = {
            **_shell(repo_num, "issue", issue_key, org_id),
            "author_login": author_login, "user_id": author_user_id, "state": state,
            "title": title, "created_at": _iso(created_at), "updated_at": _iso(closed_at or created_at),
            "closed_at": _iso(closed_at),
            "stats": {"comment_count": _poisson(1.5, comment_r)},
            "labels": issue_labels,
            "info": {"github.assignees": assignee_logins, "synthetic": marker},
        }
        issues_by_repo.setdefault(feature.repo.name, []).append(issue_artifact)
        issue_by_key[issue_key] = issue_artifact

    # Backlog issues
    for backlog in work.backlog_issues:
        repo_num = ids.repo_num(f"{resolved.config.org.github_owner}/{backlog.repo.name}")
        author_login, author_user_id = _login_and_user_id(resolved, backlog.author_l1_user)
        text_r = rng_for(seed, "text", "backlog", backlog.repo.name, str(backlog.week_index),
                         backlog.created_at.isoformat())
        title = lexicon.issue_title(text_r, text_r.choice(lexicon.FEATURE_NOUNS), backlog.repo.name)
        issue_key = f"backlog:{backlog.repo.name}:{backlog.week_index}:{backlog.created_at.isoformat()}"
        info = {"synthetic": marker}
        if backlog.not_planned:
            info["github.state_reason"] = "not_planned"
        issue_artifact = {
            **_shell(repo_num, "issue", issue_key, org_id),
            "author_login": author_login, "user_id": author_user_id,
            "state": "closed" if backlog.closed_at else "open",
            "title": title, "created_at": _iso(backlog.created_at),
            "updated_at": _iso(backlog.closed_at or backlog.created_at),
            "closed_at": _iso(backlog.closed_at), "stats": {"comment_count": 0},
            "labels": ["chore"], "info": info,
        }
        issues_by_repo.setdefault(backlog.repo.name, []).append(issue_artifact)
        issue_by_key[issue_key] = issue_artifact

    # ---- Numbering (step 1), per repo ----
    for repo_name in set(list(drafts_by_repo) + list(issues_by_repo)):
        repo_num = ids.repo_num(f"{resolved.config.org.github_owner}/{repo_name}")
        entries = []
        for issue in issues_by_repo.get(repo_name, []):
            entries.append({"created_at": issue["created_at"], "kind_order": 0,
                            "key": issue["artifact_id"], "ref": issue})
        for draft in drafts_by_repo.get(repo_name, []):
            entries.append({"created_at": draft.artifact["created_at"], "kind_order": 1,
                            "key": draft.pr_key, "ref": draft})
        entries.sort(key=_numbering_sort_key)
        for i, entry in enumerate(entries, start=1):
            ref = entry["ref"]
            if isinstance(ref, dict):
                ref["number"] = i
                # D12: a real issue's artifact_id is `github:<repo_num>:issue:<n>`, `n` the
                # assigned GitHub issue number (`quality.feature_by_id` parses it that way) --
                # unknown until this pass runs, so the shell's placeholder key is corrected here.
                ref["artifact_id"] = ids.artifact_id(repo_num, "issue", i)
            else:
                ref.artifact["number"] = i
                # Reviews carry the PR's own number (map_github's real review mapping does the
                # same: `"number": pr["number"]`) -- unknown until this numbering pass runs, so
                # the placeholder `item.seq` set when each review was built is corrected here.
                for review in ref.reviews:
                    review["number"] = i

    # Backfill title "(#n)"/branch "feat/<n>-slug" for graphql/branch/title-linked PRs, and
    # `linked_issue` (graphql only), and pr_issue_links rows.
    for feature in work.features.values():
        if feature.link_method == "none":
            continue
        issue_id = feature_issue_key[feature.feature_id]
        issue = issue_by_key[issue_id]
        for item_id in feature.items:
            item = work.items[item_id]
            if not item.has_pr:
                continue
            draft = next(d for d in drafts_by_repo.get(item.repo.name, [])
                        if d.pr_key == item.item_id)
            method = feature.link_method
            if method == "graphql":
                draft.artifact["linked_issue"] = issue["artifact_id"]
            elif method == "title":
                draft.artifact["title"] = f"{draft.artifact['title']} (#{issue['number']})"
            elif method == "branch":
                slug = _slug(item.change_type + "-" + item.module.path)
                draft.artifact["branch"] = lexicon.branch(item.change_type, issue["number"], slug)
            all_links.append({
                "pr_artifact_id": draft.artifact["artifact_id"], "tracker": "github",
                "project_key": f"{resolved.config.org.github_owner}/{item.repo.name}",
                "number": issue["number"], "issue_artifact_id": issue["artifact_id"],
                "method": method, "confidence": _LINK_CONFIDENCE[method],
                "created_at": draft.artifact["created_at"],
            })

    # Assemble output lists.
    for repo_issues in issues_by_repo.values():
        all_artifacts.extend(repo_issues)
    for drafts in drafts_by_repo.values():
        for draft in drafts:
            all_artifacts.append(draft.artifact)
            all_artifacts.extend(draft.commits)
            all_artifacts.extend(draft.reviews)
            all_events.extend(draft.events)

    # session_git_links (step 9) + pr_list (for P7's content.py)
    children_by_parent: dict[str, list[SessionPlan]] = {}
    for p in plans:
        if p.parent_session_id:
            children_by_parent.setdefault(p.parent_session_id, []).append(p)

    for drafts in drafts_by_repo.values():
        for draft in drafts:
            if draft.is_revert:
                continue
            item = draft.item
            for sid in item.sessions:
                plan = plans_by_id[sid]
                method_r = rng_for(seed, "item_pick", sid)
                if sid in draft.session_first_commit:
                    method, confidence = "sha", 1.0
                    signals = {"sha": draft.session_first_commit[sid]}
                else:
                    confidence = round(method_r.uniform(0.55, 0.9), 2)
                    method = "heuristic"
                    signals = {"files": len(draft.artifact["files"] or []),
                              "window_s": int((plan.ended_at - plan.started_at).total_seconds())}
                link_row = {"session_id": sid, "artifact_id": draft.artifact["artifact_id"],
                           "method": method, "confidence": confidence, "signals": signals,
                           "created_at": draft.artifact["created_at"]}
                session_links.append(link_row)
                pr_list_by_session.setdefault(sid, []).append({
                    "method": method, "number": draft.artifact["number"],
                    "repo_id": f"github:{ids.repo_num(f'{resolved.config.org.github_owner}/{item.repo.name}')}",
                    "confidence": confidence, "artifact_id": draft.artifact["artifact_id"],
                    "change_type": None})
                for child in children_by_parent.get(sid, ()):
                    session_links.append({**link_row, "session_id": child.session_id})
                    pr_list_by_session.setdefault(child.session_id, []).append(
                        pr_list_by_session[sid][-1])

    log.info("synthetic.org.git", artifacts=len(all_artifacts), events=len(all_events),
             links=len(all_links), rework=len(all_rework))
    return GitResult(artifacts=all_artifacts, events=all_events, pr_issue_links=all_links,
                     pr_rework=all_rework, session_links=session_links,
                     pr_list_by_session=pr_list_by_session)


def _poisson(mean: float, r) -> int:
    if mean <= 0:
        return 0
    limit = math.exp(-mean)
    count = 0
    product = 1.0
    while True:
        product *= r.random()
        if product <= limit:
            return count
        count += 1


def team_pr_comments(resolved: ResolvedOrg, item: WorkItem) -> float:
    return resolved.team_effective[_team_of(resolved, item.l1_user)].pr.comments_per_pr_mean
