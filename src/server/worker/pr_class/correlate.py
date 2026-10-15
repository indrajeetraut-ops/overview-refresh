"""The SHA correlator: session <-> PR (docs/modules/pr-work-classification.md §4, §6.4, §8).

Runs on the `git_correlate` queue_jobs topic, rung by git-source's `apply.py` whenever a
`commit` or a merged `pr` artifact lands or gains a `commit_shas` list (implementation plan
B2) -- not yet wired to "a session finishes" (design §3's other trigger). A PR typically merges
well after the session that produced its commits has already finished recording, so re-scanning
on artifact arrival alone already covers the common ordering (a session's commit shas are
already in `sessions.signal_blob` long before its PR merges); the session-finish trigger would
only add coverage for the rarer reverse ordering, and is deferred rather than built blind.

**One join, precedence `trailer > sha > heuristic`** (§4.2):

- `trailer` is a reserved, NOT-LIVE path in v1: the collector git-hook plugin that writes a
  `Talium-Session:` trailer (Part C) is deferred, and git-source deliberately never persists
  commit messages (§5.9) -- there is no data source to read one back from. `_trailer_artifact_for_session`
  always returns `None`; kept as an explicit, separately-testable seam so enabling it later is
  purely a matter of implementing that one function, per the design's own promise (§4.2).
- `sha` is the real v1 mechanism: a session's recorded commit shas (from the collector's
  Bash-commit capture, already built -- git-source implementation plan A6) joined against
  `git_artifacts` by PREFIX match, not exact equality -- measured real data shows Claude Code's
  own telemetry only ever records git's abbreviated (~7 char) hash, in both the vendor-provided
  `gitOperation.commit.sha` field and the stdout-parse fallback, while `git_artifacts.sha` (from
  GitHub's API) is always the full 40 chars. A short sha is a valid prefix, not a different
  value -- git itself guarantees the abbreviation it prints is unique within the repo, so this
  is git's own notion of "same commit," not a guess (`config/queries/pr_class.yaml`'s
  `commit_artifacts_for_shas`/`owning_prs_for_shas`/`sessions_with_commit_sha`). "Known to
  git-source at all" is satisfied by EITHER a standalone `commit` artifact OR appearing inside
  some merged PR's `commit_shas` -- required because backfill (unlike a live webhook push) never
  creates standalone `commit` artifacts, only `pr` artifacts carrying `commit_shas`, so a
  repo that has only ever been backfilled would otherwise never resolve a single sha.
- `heuristic` (file-overlap within one repo_id) is the last resort, for a sha that resolves to
  NEITHER a `commit` artifact NOR any merged PR's `commit_shas` -- e.g. a local history rewrite
  before push (§4.3). It can only ever match a BACKFILLED PR, since a bare webhook-mapped PR
  carries no file list (git-source A4); this is a documented limit, not a bug.

**Owning-PR dedup happens AT LINK TIME, not at rollup time** (§6.4): `correlate_session` only
ever links a sha to its OWNING PR (earliest-OPENED -- `min(created_at)`, `merged_at` a
deterministic secondary tiebreak -- among in-scope PRs whose `commit_shas` contain it) -- a
non-owning aggregate (sprint→main re-including an already-merged commit) never gets a session
link at all, so it costs zero at attribution automatically, with no special-casing needed in
B6's rollup. `created_at` (not `merged_at`) is the tiebreak because a stacked child PR can
merge BEFORE the parent that introduced the shared commit; the PR opened first is the commit's
real home in both that case and the feature→sprint→main aggregate case.
"""
from __future__ import annotations

import json
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session as DbSession

from server.storage.queries import QueryCatalog

from .config import PrClassConfig
from .scope import is_in_scope

log = structlog.get_logger(module="worker.pr_class.correlate")

#: Below this file-overlap fraction, a heuristic candidate is not trusted enough to link (§4.2:
#: "noisy; flagged lower-confidence in coverage" -- this floor keeps the noise out entirely
#: rather than storing a link nobody should act on).
_HEURISTIC_MIN_OVERLAP = 0.3


def _trailer_artifact_for_session(s: DbSession, session_id: str) -> str | None:
    """Reserved (§4.2): would resolve directly to a commit's artifact_id if any commit in the
    repo carried a `Talium-Session: <session_id>` trailer -- rewrite-proof, since it does not
    depend on sha equality at all. Always `None` in v1 (see module docstring)."""
    return None


def _default_branches_by_repo(s: DbSession, catalog: QueryCatalog, repo_ids: list[str]) -> dict[str, str | None]:
    if not repo_ids:
        return {}
    rows = s.execute(catalog.get("pr_class", "repo_default_branches"), {"repo_ids": repo_ids})
    return {r.repo_id: r.default_branch for r in rows}


def pick_owning_pr(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """§6.4: `owning_pr(commit) = the PR opened first (`min(created_at)`) among all in-scope
    PRs whose commit_shas contain that sha`, with `merged_at` as a deterministic secondary
    tiebreak. First-to-OPEN wins, so the PR that first formed around a commit owns the effort;
    a later aggregate (sprint→main) that re-includes the same commit owns zero new cost --
    correct, because the aggregate merge MOVED work, it did not PRODUCE it.

    `created_at`, not `merged_at`: a stacked child PR (built on a parent PR's branch, carrying
    its commits) can merge BEFORE that parent. Keying on `merged_at` would then hand the
    parent's own commit to the child, leaving the originating PR owning zero commits and
    mis-flagged `integration`. `created_at` is durable (never changed by reopen) so re-runs
    stay byte-identical (§7), and still yields the feature→sprint→main result unchanged (the
    feature PR is opened before the aggregate)."""
    return min(candidates, key=lambda pr: (pr["created_at"], pr["merged_at"]))


def _upsert_link(s: DbSession, catalog: QueryCatalog, session_id: str, artifact_id: str,
                 method: str, confidence: float, signals: dict[str, Any]) -> None:
    s.execute(catalog.get("pr_class", "upsert_session_git_link"), {
        "session_id": session_id, "artifact_id": artifact_id, "method": method,
        "confidence": confidence, "signals": json.dumps(signals),
    })


def correlate_session(s: DbSession, catalog: QueryCatalog, config: PrClassConfig, session_id: str) -> None:
    """(Re-)link one session to whatever PRs its recorded commits currently resolve to. Safe to
    call repeatedly (design §7: "a logic fix is a re-run, byte-identical result") -- every link
    is a plain upsert keyed on `(session_id, artifact_id)`."""
    shas = [r.sha for r in s.execute(catalog.get("pr_class", "session_commit_shas"), {"session_id": session_id})]
    if not shas:
        return   # no commits recorded at all -- correctly stays out of the "candidate" bucket (§4.4)

    # Keyed by `matched_sha` (the ORIGINAL, possibly-short sha this session recorded), not the
    # row's own full `sha` column -- see commit_artifacts_for_shas's prefix-match note. A
    # collision (two rows matching the same short sha) silently keeps the last one; documented,
    # accepted limitation, not handled here.
    commit_rows = {r.matched_sha: r for r in s.execute(catalog.get("pr_class", "commit_artifacts_for_shas"), {"shas": shas})}
    pr_rows = list(s.execute(catalog.get("pr_class", "owning_prs_for_shas"), {"shas": shas}))
    default_branches = _default_branches_by_repo(s, catalog, list({pr.repo_id for pr in pr_rows}))

    unresolved: list[str] = []
    for sha in shas:
        trailer_hit = _trailer_artifact_for_session(s, session_id)   # reserved; always None in v1
        if trailer_hit:
            _upsert_link(s, catalog, session_id, trailer_hit, "trailer", 1.0, {"sha": sha})
            continue
        # Prefix match, not `sha in (...)`: `sha` here is the session's own (possibly short)
        # recorded value, `pr.commit_shas` are always full 40-char GitHub shas.
        matching_prs = [pr for pr in pr_rows if any(full.startswith(sha) for full in (pr.commit_shas or []))]
        # "Known to git-source at all" no longer requires a standalone `commit` artifact --
        # backfill (unlike a live webhook push) never creates one, only ever a `pr` artifact
        # carrying `commit_shas`. A sha reachable through EITHER source counts as known;
        # `commit_rows` alone would silently strand every backfill-only repo's shas as
        # "unresolved" even when they plainly are inside a merged PR (§4.2/§4.3's "known but
        # not yet in scope" case still needs a way to be recognized without a live webhook).
        if sha not in commit_rows and not matching_prs:
            unresolved.append(sha)
            continue
        candidates = [
            pr for pr in matching_prs
            if is_in_scope(config.eval_scope, pr.repo_id, default_branches.get(pr.repo_id),
                          pr.base_branch, pr.branch)
        ]
        if not candidates:
            # The commit is known, but no in-scope merged PR contains it YET (still open, or
            # every containing PR is out of scope). Correctly waits -- no link, no heuristic
            # (a heuristic guess here would be strictly worse than the sha we already have).
            continue
        owning = pick_owning_pr([dict(c._mapping) for c in candidates])
        _upsert_link(s, catalog, session_id, owning["artifact_id"], "sha", 1.0,
                    {"sha": sha, "candidate_prs": len(candidates)})

    if unresolved:
        _heuristic_fallback(s, catalog, config, session_id, unresolved)

    # The denormalized read view a dashboard hits (storage.md §3.4, B6) -- refreshed right
    # here rather than only in a separate batch pass, so it never lags behind what this
    # session actually just linked to. A no-op (0 rows affected) if `session_info` hasn't
    # folded this session yet; the next fold/refresh pass catches it up.
    s.execute(catalog.get("pr_class", "refresh_session_pr_list"), {"session_id": session_id})


def _heuristic_fallback(s: DbSession, catalog: QueryCatalog, config: PrClassConfig,
                        session_id: str, unresolved_shas: list[str]) -> None:
    """File-overlap within one repo_id, for a sha that never landed as a `commit` artifact at
    all (§4.2's last resort -- a local rewrite before push is the common cause, §4.3)."""
    info = s.execute(text("SELECT repo_id, files FROM session_info WHERE session_id = :id"),
                     {"id": session_id}).mappings().first()
    if info is None or not info["repo_id"]:
        return
    session_files = set((info["files"] or {}).keys())
    if not session_files:
        return

    candidates = list(s.execute(catalog.get("pr_class", "merged_prs_with_files_for_repo"),
                                {"repo_id": info["repo_id"]}))
    default_branch = _default_branches_by_repo(s, catalog, [info["repo_id"]]).get(info["repo_id"])

    best, best_score = None, 0.0
    for pr in candidates:
        if not is_in_scope(config.eval_scope, info["repo_id"], default_branch, pr.base_branch, pr.branch):
            continue
        pr_files = {f["path"] for f in (pr.files or [])}
        if not pr_files:
            continue
        overlap = len(session_files & pr_files) / len(pr_files)
        if overlap > best_score:
            best, best_score = pr, overlap

    if best is not None and best_score >= _HEURISTIC_MIN_OVERLAP:
        _upsert_link(s, catalog, session_id, best.artifact_id, "heuristic", best_score,
                    {"overlap": round(best_score, 3), "unresolved_shas": unresolved_shas})
        log.info("heuristic_link", session_id=session_id, artifact_id=best.artifact_id,
                 overlap=round(best_score, 3))


def make_git_correlate_handler(config: PrClassConfig):
    """Binds the `git_correlate` handler to a config, matching `dispatch.py`'s 3-arg
    `Handler` shape (`s, catalog, payload`) the same way `rollup.make_rollup_handler` binds a
    `SessionFold`."""

    def handler(s: DbSession, catalog: QueryCatalog, payload: dict) -> None:
        git_correlate(s, catalog, config, payload)

    return handler


def git_correlate(s: DbSession, catalog: QueryCatalog, config: PrClassConfig, payload: dict) -> None:
    """The `git_correlate` topic handler. `payload = {"artifact_id": ...}` -- a commit or a
    merged PR that just landed or gained a `commit_shas` list. Finds every session that could
    plausibly link to it and re-correlates each (idempotent, so "every session with a matching
    sha" is always safe to recompute rather than trying to diff what changed)."""
    artifact_id = payload["artifact_id"]
    row = s.execute(text(
        "SELECT kind, sha, commit_shas, merged_at FROM git_artifacts WHERE artifact_id = :id"
    ), {"id": artifact_id}).mappings().first()
    if row is None:
        log.warning("git_correlate_artifact_missing", artifact_id=artifact_id)
        return

    if row["kind"] == "commit":
        shas = [row["sha"]] if row["sha"] else []
    elif row["kind"] == "pr" and row["merged_at"] is not None:
        shas = row["commit_shas"] or []
    else:
        return   # an unmerged PR, a review, a check -- nothing to correlate yet
    if not shas:
        return

    session_ids = {r.session_id for r in s.execute(
        catalog.get("pr_class", "sessions_with_commit_sha"), {"shas": shas}
    )}
    for session_id in session_ids:
        correlate_session(s, catalog, config, session_id)
