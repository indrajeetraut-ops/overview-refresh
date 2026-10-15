"""Phase 2: F4 (feature boundary key), X1 (acceptance-cost enrichment), X2 (quality 2x2).

**Query-time, not a new worker pass.** Every field here is a cheap join over already-persisted
`git_artifacts`/`session_info` rows -- Track G/H already built the hard parts (`pr_issue_links`,
`ingest.git.features`); this module composes them the same way `aggregate.py` composes
Track A's figures, rather than standing up a new table + migration for signals that don't need
one.

Post-open push churn AND reopen count are captured (Track churn/reopen, Landings 1-2):
`git_artifact_events` records PR state transitions and the `git_commit_stats` worker fills
commit line stats via the compare API. `pr_acceptance_metrics` is the single batched source of
all X1 acceptance signals -- `review_rounds`, `reopens`, `churn_post_lines`/`rework_ratio` --
and `quality_label` folds them into X2's acceptance-cost axis as the EFF composite (Landing 3):
messy acceptance if ANY signal meets its `config.quality` cutoff (X2-config, now in
`config/api.yaml`, no longer hardcoded), with uncaptured signals never flipping a clean PR.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session as DbSession

from server.ingest.git import features as git_features
from server.ingest.git.issue_ref import github_ref
from server.storage.queries import QueryCatalog

from ..config import ApiConfig
from . import aggregate

#: (making_high, acceptance_high) -> the EFF section 4.1 quadrant label (locked vocabulary,
#: lexicon-checked -- never re-worded here). X2's axis thresholds now live in
#: `config/api.yaml`'s `quality:` block (`ApiConfig.quality`, X2-config), no longer hardcoded.
_QUALITY_LABEL = {
    (False, False): "Clean",
    (True, False): "Heavy lift",
    (False, True): "Quick & dirty",
    (True, True): "Costly rework",
}

#: The UI's Feature.status vocabulary is two-valued (mock.ts has no third state) -- collapse
#: `ingest.git.features`'s `not_started` into `in-progress` rather than widening the wire
#: contract for a distinction the front-end doesn't render differently yet.
_STATUS = {"shipped": "shipped", "in_progress": "in-progress", "not_started": "in-progress"}


# ------------------------------------------------------------------------------- F4

def _merged_count(s: DbSession, catalog: QueryCatalog, pr_ids: list[str]) -> int:
    if not pr_ids:
        return 0
    rows = s.execute(catalog.get("git", "artifact_states_for_ids"), {"artifact_ids": pr_ids})
    return sum(1 for r in rows if r.merged_at is not None)


def _feature_id(f: git_features.Feature) -> str:
    """D3's routable id -- distinct from `issue_key` (a display label like `"#214"` that is
    **not** unique across repos and was never enough on its own to link back to a feature;
    a real gap this closes). An issue-keyed feature's own `artifact_id` is already globally
    unique (`github:<repo_num>:issue:<n>`); a `ClusterKey`-ref'd feature (H5, branch/label)
    is keyed `branch:<repo_num>:<branch>`/`label:<repo_num>:<label>` -- checked before the
    generic PR fallback below, since a cluster always has `member_pr_ids` too; a single-PR
    fallback feature has no issue artifact (`single_pr_feature` leaves `ref.artifact_id`
    `None` by construction) so it's keyed off its one member PR instead."""
    if isinstance(f.ref, git_features.ClusterKey):
        return f"{f.ref.kind}:{f.ref.repo_num}:{f.ref.value}"
    if f.ref.artifact_id:
        return f.ref.artifact_id
    if f.member_pr_ids:
        return f"pr:{f.member_pr_ids[0]}"
    # Not reachable from list_features_for_repo today (GitHub-only, H3) -- a referenced-but-
    # unpulled tracker ref (e.g. Jira) with zero linked PRs -- kept as a stable fallback
    # rather than raising, since `Feature.as_dict` already tolerates this shape.
    return f"{f.ref.tracker}:{f.ref.project_key}:{f.ref.number}"


def _feature_view(s: DbSession, catalog: QueryCatalog, f: git_features.Feature) -> dict:
    issue_key = git_features.ref_issue_key(f.ref)
    title = (f.issue or {}).get("title") if f.issue else None
    return {
        "id": _feature_id(f),
        "issue_key": issue_key,
        "title": title or issue_key,
        "spend": {"usd_micros": f.cost_usd_micros, "cost_basis": "cost_effective", "cost_complete": None},
        "merged_prs": _merged_count(s, catalog, f.member_pr_ids),
        "status": _STATUS[f.status],
    }


def _orphan_pr_rows_and_default_branch(
    s: DbSession, catalog: QueryCatalog, repo_id: str,
) -> tuple[list[dict], str | None]:
    """H5: the two reads `branch_label_features` needs, shared by `list_features_for_repo` and
    `feature_by_id`'s `branch:`/`label:` re-derivation."""
    rows = [dict(r) for r in s.execute(
        catalog.get("manager", "repo_prs_without_issue_link"), {"repo_id": repo_id}).mappings().all()]
    default_branch = s.execute(
        catalog.get("manager", "git_repo_default_branch"), {"repo_id": repo_id}).scalar()
    return rows, default_branch


def feature_by_id(s: DbSession, catalog: QueryCatalog, feature_id: str) -> git_features.Feature | None:
    """D3's lookup, the reverse of `_feature_id`: `github:<repo_num>:issue:<n>` composes the
    real feature via `feature_of`; `pr:<pr_artifact_id>` recomposes the single-PR fallback via
    `single_pr_feature`; `branch:<repo_num>:<branch>`/`label:<repo_num>:<label>` (H5)
    re-derives the whole repo's current clustering and picks the matching one -- every id is
    re-derived from live data rather than stored, so a stale/bogus id fails closed (`None`)
    instead of resolving to a feature that no longer matches its members. `None` for an id in
    none of these shapes, a `pr:` id whose PR doesn't exist, or a cluster that no longer forms
    (its PRs got an issue link, or moved off that branch/label, since the id was handed out)."""
    if feature_id.startswith("pr:"):
        pr_id = feature_id.removeprefix("pr:")
        exists = s.execute(catalog.get("manager", "pr_basic_facts"), {"pr_ids": [pr_id]}).first()
        if exists is None:
            return None
        return git_features.single_pr_feature(s, catalog, pr_id)
    if feature_id.startswith("branch:") or feature_id.startswith("label:"):
        kind, repo_num, value = feature_id.split(":", 2)
        repo_id = f"github:{repo_num}"
        orphan_rows, default_branch = _orphan_pr_rows_and_default_branch(s, catalog, repo_id)
        for f in git_features.branch_label_features(s, catalog, repo_num, orphan_rows, default_branch):
            if isinstance(f.ref, git_features.ClusterKey) and f.ref.kind == kind and f.ref.value == value:
                return f
        return None
    if feature_id.startswith("github:") and ":issue:" in feature_id:
        _prefix, repo_num, _kind, number = feature_id.split(":")
        ref = github_ref(int(repo_num), repo_num, int(number))
        f = git_features.feature_of(s, catalog, ref)
        # `feature_of` composes a Feature even for a bare, never-seen issue number (by design
        # -- "an issue with no linked PR is still a feature," `ingest.git.features`'s own
        # rule, for numbers `list_features_for_repo` itself enumerated). A caller-supplied id
        # with *no* evidence at all -- no pulled issue row, no PR ever linked to it -- isn't a
        # real feature, just an arbitrary number; fail closed rather than fabricate one.
        if f.issue is None and not f.member_pr_ids:
            return None
        return f
    return None


def list_features_for_repo(s: DbSession, catalog: QueryCatalog, repo_id: str) -> list[dict]:
    """F4: every feature in a repo -- issue-keyed features (whether they have a linked PR yet
    or not: "an issue with no linked PR is still a feature", `ingest.git.features`'s own rule)
    plus H5's branch/label clustering of every PR that carries no issue link at all (REPO
    section 4's boundary-key priority, all four rungs: issue, branch, label, single-PR
    fallback for the un-clusterable remainder).

    `repo_id` must already be git-source's own numeric scheme (`github:<repo_num>`,
    `map_github.py`) -- callers resolve it there first via `aggregate.resolve_git_repo_id`
    (the session-side `github:owner/name` scheme `git_artifacts` was never keyed on)."""
    if not repo_id.startswith("github:"):
        return []  # F4 is GitHub-only in MVP (H3).
    repo_num = repo_id.removeprefix("github:")
    numbers = {r[0] for r in s.execute(catalog.get("manager", "repo_feature_numbers"),
                                       {"project_key": repo_num, "repo_id": repo_id})}

    result = []
    for n in sorted(numbers):
        ref = github_ref(int(repo_num), repo_num, n)
        result.append(_feature_view(s, catalog, git_features.feature_of(s, catalog, ref)))

    orphan_rows, default_branch = _orphan_pr_rows_and_default_branch(s, catalog, repo_id)
    for f in git_features.branch_label_features(s, catalog, repo_num, orphan_rows, default_branch):
        result.append(_feature_view(s, catalog, f))

    result.sort(key=lambda ft: -(ft["spend"]["usd_micros"] or 0))
    return result


# ------------------------------------------------------------------------------- X1

#: `git_artifacts.state` for a review-kind row is GitHub's own GraphQL
#: `PullRequestReviewState` enum, passed through verbatim by `map_github.py` (no case
#: normalization anywhere in the ingest pipeline) -- **uppercase**
#: (`tests/server/ingest/git/test_backfill.py`'s own fixture: `"state": "APPROVED"`), not the
#: lowercase `"changes_requested"` this comparison originally assumed. That assumption was
#: never caught because the test fixtures here made the same wrong assumption -- fixed below,
#: same class of bug as the repo-identity and beats-shape ones found earlier this branch.
_CHANGES_REQUESTED = "CHANGES_REQUESTED"


def _rework_from_churn_row(row) -> tuple[int | None, float | None]:
    """X1-churn: (post-open churn lines, rework ratio) from a `post_open_commit_churn` row, or
    (None, None) when the PR's commit history / post-open line stats aren't captured -- never a
    guessed 0. Shared by the metrics batch below and (transitively) PR detail. `rework_ratio` is
    churn over the PR's final size, a denominator that doesn't depend on pre-open pushes also
    being captured."""
    if row is None or row.branch_commit_count == 0:
        return None, None            # no commit history captured for this PR
    if row.post_commit_count > 0 and row.post_commits_with_stats == 0:
        return None, None            # post-open pushes seen, but their line stats not captured
    churn = row.churn_post_lines
    rework_ratio = round(churn / row.final_lines, 4) if row.final_lines > 0 else None
    return churn, rework_ratio


def pr_acceptance_metrics(s: DbSession, catalog: QueryCatalog, pr_ids: list[str]) -> dict[str, dict]:
    """X1: the acceptance-cost signals for a set of PRs, one dict per id -- `review_rounds`
    (changes_requested count), `time_to_first_review_hours`, `time_to_merge_days`,
    `comment_count`, `reopens` (X1-reopen), and `churn_post_lines`/`rework_ratio` (X1-churn).
    The single batched source X2's composite (`quality_label`) and PR detail both read from.

    `reopens`/`churn_post_lines`/`rework_ratio` follow the honest-absence pattern: a real number
    when captured (`0`/`0.0` meaning "measured, and clean"), `None` when the underlying signal
    wasn't captured for that PR yet (pre-Landing-1/2 or pre-backfill) -- never a guessed value."""
    if not pr_ids:
        return {}
    basics = {r.artifact_id: r for r in
             s.execute(catalog.get("manager", "pr_basic_facts"), {"pr_ids": pr_ids})}
    reviews_by_pr: dict[str, list] = {}
    for r in s.execute(catalog.get("manager", "pr_review_children"), {"pr_ids": pr_ids}):
        reviews_by_pr.setdefault(r.pr_artifact_id, []).append(r)
    # PRs with any transition row -> their reopen count; PRs absent from this map were never
    # captured, so their `reopens` stays None below (not 0).
    reopens_by_pr = {r.artifact_id: r.reopens for r in
                     s.execute(catalog.get("manager", "pr_reopen_counts"), {"pr_ids": pr_ids})}
    churn_by_pr = {r.artifact_id: r for r in
                   s.execute(catalog.get("manager", "post_open_commit_churn"), {"pr_ids": pr_ids})}

    out: dict[str, dict] = {}
    for pr_id in pr_ids:
        basic = basics.get(pr_id)
        reviews = reviews_by_pr.get(pr_id, [])
        review_rounds = sum(1 for r in reviews if r.state == _CHANGES_REQUESTED)
        first_review_at: datetime | None = min((r.created_at for r in reviews), default=None)

        time_to_first_review_hours = time_to_merge_days = None
        comment_count = None
        if basic is not None:
            if first_review_at is not None and basic.created_at is not None:
                time_to_first_review_hours = (first_review_at - basic.created_at).total_seconds() / 3600
            if basic.merged_at is not None and basic.created_at is not None:
                time_to_merge_days = (basic.merged_at - basic.created_at).total_seconds() / 86400
            comment_count = (basic.stats or {}).get("comment_count")

        churn_post_lines, rework_ratio = _rework_from_churn_row(churn_by_pr.get(pr_id))
        out[pr_id] = {
            "review_rounds": review_rounds,
            "time_to_first_review_hours": time_to_first_review_hours,
            "time_to_merge_days": time_to_merge_days,
            "comment_count": comment_count,
            "reopens": reopens_by_pr.get(pr_id),
            "churn_post_lines": churn_post_lines,
            "rework_ratio": rework_ratio,
        }
    return out


# ------------------------------------------------------------------------------- X2

def _repo_making_cost_threshold(s: DbSession, catalog: QueryCatalog, config: ApiConfig,
                                git_repo_id: str | None, as_of: datetime) -> float | None:
    """The making-cost cutoff for one repo at one point in time: the configured percentile of its
    priced merged-PR costs over the trailing window ending at `as_of`. `None` when the repo has
    fewer than `making_cost_min_prs` priced merged PRs in that window -- too thin to rank against.
    Reads the same `pr_costs_for_ids` cost path as every other PR cost (one-cost-source rule)."""
    from .. import forecast

    q = config.quality
    if not git_repo_id:
        return None
    window_pr_ids = [r.artifact_id for r in s.execute(
        catalog.get("manager", "repo_merged_prs_in_range"),
        {"repo_id": git_repo_id, "from": as_of - timedelta(days=q.making_cost_trailing_days),
         "to": as_of + timedelta(microseconds=1)},  # inclusive of the anchoring PR itself
    )]
    if len(window_pr_ids) < q.making_cost_min_prs:
        return None
    costs = [int(r.cost_micros) for r in s.execute(catalog.get("manager", "pr_costs_for_ids"),
                                                  {"pr_ids": window_pr_ids})
             if r.cost_micros and r.cost_micros > 0]  # SUM() comes back as Decimal
    if len(costs) < q.making_cost_min_prs:
        return None
    return forecast.percentile(costs, q.making_cost_high_percentile)


def making_cost_high(s: DbSession, catalog: QueryCatalog, config: ApiConfig,
                     pr_ids: list[str], now: datetime | None = None) -> dict[str, bool | None]:
    """X2's making-cost axis, per PR (efficiency note §4.1, decision 2026-09-17): `True` when the PR's
    cost is at or above its repo's configured cost percentile over the trailing window ending at the
    PR's own merge (an open PR anchors at `now`), so a label never drifts as later PRs land.

    `None` -- never a guessed quadrant -- when the PR has no linked, priced sessions, or its repo is
    too thin to rank against. This replaced "recoverable share >= 25%", which measured efficiency,
    not cost: a long, expensive PR with a small avoidable share used to read as cheap."""
    if not pr_ids:
        return {}
    now = now or datetime.now(timezone.utc)
    basics = {r.artifact_id: r for r in s.execute(catalog.get("manager", "pr_basic_facts"),
                                                  {"pr_ids": pr_ids})}
    costs = {r.pr_artifact_id: int(r.cost_micros) for r in s.execute(catalog.get("manager", "pr_costs_for_ids"),
                                                                     {"pr_ids": pr_ids})}
    thresholds: dict[tuple[str | None, datetime], float | None] = {}
    out: dict[str, bool | None] = {}
    for pr_id in pr_ids:
        basic, cost = basics.get(pr_id), costs.get(pr_id)
        if basic is None or not cost:
            out[pr_id] = None
            continue
        key = (basic.repo_id, basic.merged_at or now)
        if key not in thresholds:
            thresholds[key] = _repo_making_cost_threshold(s, catalog, config, *key)
        threshold = thresholds[key]
        out[pr_id] = None if threshold is None else cost >= threshold
    return out


def quality_label(making_high: bool | None, acceptance: dict, config: ApiConfig) -> str | None:
    """EFF section 4.1's 2x2. `making_high` is `making_cost_high`'s per-PR verdict; `acceptance` is a
    `pr_acceptance_metrics` entry. `None` when the making-cost axis can't be measured -- never a
    guessed quadrant.

    The acceptance-cost axis is the EFF composite (Landing 3): high if ANY of review rounds,
    reopens, or post-open rework ratio meets its `config.quality` cutoff. A signal that wasn't
    captured for this PR (`reopens`/`rework_ratio` is `None`) simply doesn't contribute, so
    missing churn/reopen data can never flip a clean PR to messy -- it only ever adds evidence."""
    if making_high is None:
        return None
    q = config.quality
    reopens, rework_ratio = acceptance.get("reopens"), acceptance.get("rework_ratio")
    acceptance_high = (
        (acceptance.get("review_rounds") or 0) >= q.review_rounds_high
        or (reopens or 0) >= q.reopens_high
        or (rework_ratio is not None and rework_ratio >= q.rework_ratio_high)
    )
    return _QUALITY_LABEL[(making_high, acceptance_high)]


def quality_counts_for_prs(s: DbSession, catalog: QueryCatalog, config: ApiConfig,
                           pr_ids: list[str]) -> list[dict]:
    """A5's `work_quality` -- one count per label, over a person's merged PRs. A PR that can't
    be classified (no linked sessions) is omitted, not bucketed as "unknown" -- the same
    "absent, not guessed" posture as everywhere else in this API. `config` carries X2's
    now-externalized thresholds (X2-config)."""
    if not pr_ids:
        return []
    acceptance = pr_acceptance_metrics(s, catalog, pr_ids)
    making = making_cost_high(s, catalog, config, pr_ids)
    counts: Counter[str] = Counter()
    for pr_id in pr_ids:
        label = quality_label(making[pr_id], acceptance.get(pr_id, {}), config)
        if label:
            counts[label] += 1
    return [{"label": label, "count": n} for label, n in counts.items()]
